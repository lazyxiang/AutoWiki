# Root Cause Investigation: Wiki Planner Robustness

I cross-referenced the broken planner output against `worker/pipeline/wiki_planner.py`, `ast_analysis.py`, and `dependency_graph.py`. There are three distinct failure modes producing the symptoms you describe, only one of which is what it appears to be on the surface.

## Finding #1 — The file assignments are not LLM output. They are round-robin alphabetical fallback.

Look closely at what each page received. Sort all the files in the planner output alphabetically and stride by 26 (the number of pages):

| stride index | file (alphabetical) | page assigned |
| :--- | :--- | :--- |
| 0 | .github/workflows/backend-ci.yml | AutoWiki Overview (page 0) |
| 1 | .github/workflows/frontend-ci.yml | Core Architecture and Design (page 1) |
| 2 | .pre-commit-config.yaml | System Configuration (page 2) |
| 3 | CLAUDE.md | Data Persistence and Models (page 3) |
| 4 | GEMINI.md | Global Logging and Telemetry (page 4) |
| 5 | README.md | The Ingestion Pipeline (page 5) |
| 6 | api/__init__.py | Code Analysis with Tree-Sitter (page 6) |
| … | | |

Every page contains exactly 7 files, every file is the next alphabetical file at offset `n*26 + page_index`. This pattern is produced by exactly one piece of code — the silent fallback at `worker/pipeline/wiki_planner.py:654-659`:

```python
# Fallback: round-robin distribution (ignores validation constraints)
result = {p["title"]: [] for p in outline}
titles = [p["title"] for p in outline]
for i, f in enumerate(sorted(all_files)):
    result[titles[i % len(titles)]].append(f)
return result
```

The LLM's Phase 2 file-assignment call failed all 3 retries, and the fallback round-robin produced what you saw. The assignment was never the LLM's judgment — it was `sorted()` modulo 26. That is why `worker/pipeline/wiki_planner.py` ended up on "Testing and Verification" instead of "Wiki Planning and Strategy": page 25 happened to receive `worker/pipeline/wiki_planner.py` by alphabetic position, nothing more.

### Why Phase 2 silently failed

Several compounding reasons:

1. **Output cardinality is huge.** With ~182 files, the LLM must produce 182 `{file, page_title}` objects in a single structured response. Schema-constrained generation at this size is fragile — Anthropic, OpenAI and Gemini all degrade as JSON arrays grow past ~100 items, especially when each item references a freeform string (`page_title`) that must match an enum exactly.
2. **No partial-success handling.** `_assign_files` (`worker/pipeline/wiki_planner.py:619-652`) treats each retry as all-or-nothing. If the LLM returns 170 of 182 valid assignments, the unassigned 12 trigger nothing, but if validation fails for any reason the entire response is thrown out and the prompt is appended with "Previous attempt failed: …" — which usually makes the next attempt worse because the prompt grows but the underlying difficulty is unchanged.
3. **`_validate_assignments` is too lax to detect bad assignments but too strict to accept good ones.** It only enforces ≤25 files/page and "non-overview pages must have ≥1 file" (`wiki_planner.py:506-519`). Round-robin satisfies both; meanwhile a perfectly reasonable LLM assignment that leaves "Provider Implementations" empty (because the LLM picked "LLM Infrastructure" instead) gets rejected and re-tried.
4. **Fallback writes through silently.** `_assign_files` returns the round-robin result and the outer `generate_wiki_plan` re-runs `validate_wiki_plan` on it — but `validate_wiki_plan` also only checks structural constraints, so the bad data passes the second gate. There is no `logger.warning` distinguishing "LLM produced this" from "fallback produced this." A user sees a broken wiki and has no signal anywhere in the logs that the LLM gave up.

## Finding #2 — The outline fragments the pipeline because the prompt does not encode "architectural locality."

The Phase 1 outline (`_build_outline_prompt`, `wiki_planner.py:327-392`) tells the LLM:

> ▎ Group by semantic purpose, not directory structure  
> ▎ Create 2-3 levels of hierarchy for larger repos  
> ▎ Page titles should describe concepts/components, not directory names

This is anti-directory phrasing without a counterweight. Combined with the dependency-graph signal (which presents files as a flat list of edges sorted by count), the LLM picks up local clusters but loses the macro-architecture: it sees "RAG indexer imports embedding providers" and produces "Retrieval Augmented Generation" as a top-level page, losing the fact that RAG is one stage of one pipeline. The same happens for "The Ingestion Pipeline" and "The Generation Engine" — they become peer top-level pages, when they should be siblings under a single "Generation Pipeline" page.

Three concrete reasons the prompt produces this:

1. **No architectural anchors are passed to Phase 1.** The README is provided, but `worker/pipeline/__init__.py`, the directory tree, and the package-level docstrings (which often contain the canonical architecture description) are not surfaced as distinct signals.
2. **Cluster signal is downweighted.** `_build_outline_prompt` includes clusters of ≤20 files only, and only the first 30. For a repo where `worker/pipeline/` is one large strongly-connected component, that single architecturally-decisive cluster is suppressed as a "Large cluster — see dependency relationships above," and the LLM never sees "these 8 files form a coherent unit."
3. **Directory paths are present but unaccented.** Every line of `file_summary` starts with a path like `worker/pipeline/wiki_planner.py`, so the LLM can see directory grouping, but the prompt actively discourages using it. The instruction should be "use directories as a hint, override only when semantics disagree" — not "ignore directories."

## Finding #3 — One-file-to-one-page is hard-coded in three places, not one.

You are correct that allowing redundant assignment would improve page accuracy. The constraint is enforced at:

1. **Schema** — `_ASSIGNMENT_SCHEMA` (`wiki_planner.py:283-299`) uses singular `page_title: string`, so the LLM cannot even express a multi-page assignment.
2. **Validator dedupe** — `_assign_files` (`wiki_planner.py:632-640`) maintains an `assigned_files: set[str]` and silently drops every assignment after the first one. Even if the schema allowed list-of-titles, this code would discard duplicates.
3. **Refresh logic** — `get_affected_pages` in `ingestion.py:418-424` already iterates pages and checks membership, so it would correctly handle a file appearing on multiple pages — but the storage in `wiki_plan.json` is a per-page `files: list`, which would mean multi-page-assignment files are physically duplicated in JSON. That is fine, but worth being explicit about.

The page generator itself (`page_generator.py:322-328`, `summarize_page_deps`, `_append_source_files_table`) just reads `spec.files`. It does not assume uniqueness across pages. So the downstream is already compatible — only the planner enforces the constraint.

---

# Comprehensive Solution

I propose a three-layer fix that addresses the failures in priority order: catch the silent fallback first, then make the assignment phase actually succeed, then improve the outline and allow controlled redundancy.

## Layer A — Stop the silent failure (highest priority, smallest change)

**Goal:** never again ship round-robin output as if it were planned.

1. **Make the fallback loud.** In `_assign_files`, when all retries are exhausted, log an ERROR (not silent return) with the failed prompt-tail, the validation error from the last attempt, and the file/page counts. Tag the returned plan with a `degraded: True` flag so observability surfaces this.
2. **Surface degraded plans in the worker pipeline.** Thread a `plan_quality` field through `WikiPlan` → job status → API. The frontend can show an amber banner ("Wiki structure was generated by fallback heuristic — re-run indexing or steer manually via .autowiki/wiki.json").
3. **Replace round-robin with a sane fallback.** Round-robin is the worst possible distribution because it actively destroys directory locality. Replace it with a directory-clustering fallback: group files by their top-level directory segment, then by second-level segment, then assign each cluster to the nearest matching page title (string similarity over title vs. directory name). For files with no obvious match, assign to "Overview" rather than scattering. This gives a deterministic, semantically-defensible fallback when the LLM fails.
4. **Detect round-robin-shaped output post-hoc.** Add a heuristic to `validate_wiki_plan`: if every page has exactly the same file count ±1, or if the standard deviation of page sizes is near zero AND files are alphabetically interleaved across pages, raise `ValueError("Assignment looks like round-robin fallback")`. This forces the outer loop into the new directory-clustering fallback above.

These four changes alone would have caught the bug you observed. Even before any planner-quality work, you would have seen an error in the logs and a degraded-quality banner instead of a wiki that looks fine but is unusable.

## Layer B — Make Phase 2 actually succeed (medium effort, biggest quality win)

**Goal:** the LLM's assignment attempts should converge, not collapse.

1. **Chunk the assignment task.** Split `all_files` into batches of ~40 files. For each batch, call the LLM with the full outline + the batch + a prompt asking it to assign only those files. Merge the results. This solves the cardinality problem: each call produces ≤40 JSON objects, well within the reliable structured-output range. Run batches in parallel via `LLMProvider.generate_batch` (already exists per `CLAUDE.md`).
2. **Pass per-page anchors with the outline.** When you build the assignment prompt, include the outline plus a "seed file" or two for each page — pre-computed by string/import similarity between page title+purpose and the file index. e.g. for "Wiki Planning and Strategy", the seed would be `worker/pipeline/wiki_planner.py` and `tests/worker/test_wiki_planner.py`, derived by matching the page title's content words against file path components and module docstring keywords. This anchors the LLM and dramatically reduces drift.
3. **Allow partial responses.** When the LLM returns N of M assignments, accept the N and re-prompt only for the missing M-N — do not throw the whole response away. This converts a 100% retry into a small targeted retry, which converges faster and produces better output.
4. **Tighten the validator without breaking it.** Replace "≤25 files / non-overview must be non-empty" with smarter checks:
    - Reject only when >40% of files are concentrated on a single page (sign of LLM laziness) — but only after anchors + chunking have been tried.
    - Reject when more than 20% of pages are empty (sign of poor outline-fit, which should retry the outline, not the assignment).
    - Warn (don't reject) when a cluster from the dependency graph is split across >3 pages.
5. **Use `fast_llm` for batches, escalate `llm` only on retry.** The current code does the opposite — it uses `fast_llm` first then escalates. This is correct; just make sure the batched calls preserve the pattern.

## Layer C — Outline quality and multi-page assignment

**Goal:** the outline reflects the actual architecture and pages can share files when warranted.

### C1. Better outline anchors

1. **Surface the directory tree explicitly.** Add a section to `_build_outline_prompt` that prints the top-3-level directory tree with file counts:
   ```text
   Repository structure (top 3 levels):
     api/         (8 files)
     api/routers/ (4 files)
     worker/      (12 files)
     worker/pipeline/ (15 files)   ← largest internal package
     ...
   ```
2. **And accompanying instructions:** "The largest internal packages are usually meaningful architectural units. Use them as hierarchy hints unless dependency analysis says otherwise."
3. **Reframe the anti-directory rule.** Replace "Group by semantic purpose, not directory structure" with "Group by semantic purpose. Directories are a strong default signal — only override directory structure when files in different directories form a tight semantic unit (e.g. a frontend component and its backend route)."
4. **Promote large clusters to first-class signal.** Stop suppressing clusters >20 files. Instead, summarize them with their package-level pattern: "Large cluster: worker/pipeline/* (15 files) — likely a single subsystem; consider one parent page with sub-pages per file role."
5. **Pass package-level docstrings.** When `worker/pipeline/__init__.py`, `api/__init__.py`, etc. have module docstrings, include them verbatim in the outline prompt under "Package descriptions." These often contain the canonical architecture statement and steer the LLM toward correct grouping.
6. **Add a "subsystem hint" extracted from README.** If the README has headings like "Core Components" or "Generation Pipeline," extract them as a list and pass to the prompt as "Author-declared subsystems."

### C2. Multi-page file assignment

1. **Schema change.** Update `_ASSIGNMENT_SCHEMA` to allow per-file primary + secondary assignments:
   ```json
   {
     "file": "worker/pipeline/wiki_planner.py",
     "primary_page": "Wiki Planning and Strategy",
     "secondary_pages": ["Generation Pipeline", "LLM Infrastructure"]
   }
   ```
2. **Cap secondary_pages at 2 to prevent abuse.**
3. **Prompt instructions.** Tell the LLM: "A file may appear on up to 3 pages: its primary home plus up to 2 pages where it provides essential supporting context. Use secondary assignment only when omitting the file would force the secondary page to fabricate or omit critical detail." This sets a high bar and prevents the LLM from sprinkling files everywhere.
4. **Validator changes.**
    - Drop the `assigned_files: set` dedupe in `_assign_files`. Track `primary_assigned` instead, ensure every file has exactly one primary.
    - Add a check: secondary count per page ≤ 30% of primary count (prevents pages full of borrowed files).
    - Add a check: any file appearing as secondary on N pages contributes to the dependency-cluster coherence warning N times.
5. **Storage.** `wiki_plan.json` keeps the existing `files: list` per page — populated from primary + secondary. Add a separate `primary_files: list` so refresh logic can distinguish "file changed → I am the home" vs "file changed → I borrow it" and only the home page triggers a full regeneration. Borrowing pages can use a faster regeneration path, or skip regeneration if the borrowed file's relevance to that page's purpose hasn't changed (this is a Phase-2 optimization).
6. **Refresh impact.** `get_affected_pages` already iterates over `page.files` — it will Just Work if a file appears on multiple pages. The only subtlety is incremental refresh cost: a hub file edited once now invalidates more pages. Mitigate by using `primary_files` as the strict trigger and treating secondary appearances as eligible-but-not-required regeneration.
7. **Page generator impact.** `page_generator.py:322` reads `spec.files` and feeds entities + dep summaries. Already compatible. The Pass-2 prompt should mention "Some files appear in this page as supporting context only — focus the narrative on this page's primary topic, not on documenting the borrowed files." Add this conditional instruction in `page_draft.build_draft_prompt`.

## Sequencing recommendation

I would land these in this order, each in its own branch:

1. **Layer A (catch the failure)** — half-day. Stops the immediate bleeding. Without this, you cannot tell whether subsequent improvements are working.
2. **Layer B (chunked assignment + anchors + partial accept)** — 1–2 days. This alone should fix the user-reported case (`wiki_planner.py` would land on "Wiki Planning and Strategy" via anchor seeding + smaller batches).
3. **Layer C1 (outline anchors)** — 1 day. Fixes the pipeline fragmentation; needs evals against a few real repos to tune the prompt.
4. **Layer C2 (multi-page assignment)** — 1–2 days. Quality polish; lower urgency, since fixing primary assignment quality (B + C1) recovers most of the lost accuracy. Multi-page is for the long tail.

## What I'd verify before writing code

Before starting Layer B, I'd want to confirm Phase 2 is actually failing on this repo (not just looking like it failed). One quick check: re-run indexing with `LOG_LEVEL=DEBUG` and look for "Previous attempt failed:" lines — they will reveal whether the LLM is producing schema-invalid JSON, missing files, or hitting timeouts. The fix differs:

- **Schema-invalid JSON** → batched output is the answer (Layer B1).
- **Missing files** → partial-accept retries (Layer B3).
- **Timeouts / token limit** → both, plus consider switching the assignment phase to a streaming call that emits assignments incrementally.

I have a strong prior on "all three are happening simultaneously" given 182 files in a single structured response, but confirming with a debug run before writing 1,500 lines of refactor saves rework.

---

**Tl;dr:** the catastrophic output is round-robin fallback, not LLM judgment. The hidden silent-failure path at `wiki_planner.py:654-659` masks the real problem. Fix observability first (Layer A), then make Phase 2 succeed via batching + anchors + partial-accept (Layer B), then improve outline anchoring and add controlled multi-page assignment (Layer C). The assignment-quality issue and the pipeline-fragmentation issue are independent root causes, and both need their own fix.
