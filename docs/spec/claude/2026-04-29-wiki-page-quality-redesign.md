# AutoWiki Page Generation Quality — Design Spec

**Status:** Draft 2 (revised after real-data review)
**Author:** lazyxiang
**Date:** 2026-04-29
**Scope:** Wiki page generation pipeline — Phase 2 file selection (`worker/pipeline/wiki_planner.py`) and the per-page generator (`worker/pipeline/page_generator.py` + `page_outline.py` + `page_draft.py` + `fact_check.py`).
**Out of scope:** Building the Skill package itself. Skill extraction will be a separate project; this spec only requires that Layers A and B leave the wiki pipeline in a Skill-ready shape (deterministic, no embeddings, reusable token utilities).
**Related:** Issue #39, PR #40, `docs/superpowers/plans/2026-04-10-wiki-page-quality-redesign.md`.

---

## 1. Executive Summary

Indexing AutoWiki against itself produces a wiki whose **structure is correct** but whose **page content is systematically wrong** in four distinct ways: assigned files are not ordered by relevance, orchestrator-class files are under-covered, sibling pages bleed content into each other, and the entire frontend / backend subtree is assigned to the wrong source files because the Phase-2 file scorer is ASCII-only (and even with a CJK tokenizer, CJK page titles share zero tokens with English source paths — a true cross-language bridge is required).

PR #40 raises retrieval / token / edge budgets and is necessary, but **does not fix any of these defects on its own** — they are structural (ordering, ownership, scoping, cross-language matching), not capacity-bound.

This spec proposes a two-layer in-tree remediation, plus design constraints that keep the door open to a separate Skill project later:

- **Layer A — Planner & prompt patches (1 PR, ~350 LoC).** Soft-validate file ordering, introduce sibling-aware outline + scope-disciplined draft, multi-query retrieval with rank-weighted per-file quotas, extract a shared CJK-aware tokenizer, **make `en_keywords` mandatory for non-English titles** (this, not tokenization, is the real cross-language bridge), enforce file ownership across sibling pages, and add an adaptive per-page file budget. Targets P1, P3, P4; partially mitigates P2.
- **Layer B — Section-level drafting + BM25 retrieval + Stage 4 deletion (2 PRs, ~700 LoC).** Replace the single-shot draft with `Outline → Skeleton → per-section draft → Stitch`, add a deterministic BM25 retriever, **delete Stage 4 (FAISS build) outright, drop the embedding provider from the indexing pipeline, and temporarily disable Deep Research** (the only remaining FAISS consumer; it will be migrated to keyword retrieval in a follow-up). Chat is already FAISS-free.

There is no in-tree Layer C. Skill-readiness shows up as design constraints (deterministic retrieval, reusable utilities, no FAISS / embedding dependency anywhere in the wiki indexing path), not as a Skill package.

---

## 2. Current State

### 2.1 Pipeline shape

```
Repo URL ──▶ Stage 1 Ingestion ──▶ Stage 2 AST analysis ──▶ Stage 3 Dep graph
        ──▶ Stage 4 RAG indexer (FAISS) ──▶ Stage 5 Wiki planner (2-phase LLM)
        ──▶ Stage 6 Page generator (4-pass LLM, per page, with concurrency)
```

The wiki planner is two LLM calls: Phase 1 produces an outline (titles, purposes, parents, optional `en_keywords`); Phase 2 selects 5–8 source files per page from a pre-filtered candidate set of 25 (PR #40: 40) files. The page generator runs four passes per page: outline (fast model) → draft (main model) → fact-check (fast model) → conditional revision (main model).

### 2.2 What outline & draft prompts actually consume

**Pass 1 — Outline (`worker/pipeline/page_outline.py:224-254`)**

| Source | Content | Cap |
|---|---|---|
| `WikiPageSpec` | `title`, `purpose`, `files` (5–10) | — |
| `entity_details` | up to 25 (PR #40: `8·N`) entity rows: type / name / signature / docstring(150 char) / file:line | `_format_entity_details` |
| `dep_info` | `depends_on` / `depended_by` / `external_deps`, first 10 each | string-joined |
| `child_titles` | titles of children (parents only) | — |

Crucially, **no source code is in the outline prompt.** Sections, key claims, and diagram plans are decided from metadata + entity signatures + dep summaries alone.

**Pass 2 — Draft (`worker/pipeline/page_draft.py:131-263`)**

Inherits Pass 1 inputs, plus:
- `outline` (sections JSON)
- `context_chunks`: top-k chunks from `FAISSStore.multi_search` — `k=12` (PR #40: 30), `doc_k=1` (down-weights pure docs)
- `child_contents`: structured summaries of already-generated children (headings, diagrams, intro 200 char, hard 2000 char cap)
- `repo_notes`, `page_notes`

Retrieval queries (`page_generator.py:224-249`):

```python
queries = [f"{spec.title} {' '.join((spec.files or [])[:5])}"]
if spec.purpose: queries.append(spec.purpose)
if entity_details: queries.append(' '.join(top5_entity_names))
```

The page title and the first 5 file paths are concatenated into one query. There is no per-file quota on the returned chunks.

### 2.3 Observed wiki (real data)

Indexing AutoWiki against itself produced 26 pages in `~/Downloads/wiki_plan.json`. Selected entries that motivate this spec:

| Page | `files` (verbatim, in order) |
|---|---|
| 依赖图谱构建 | `[wiki_planner.py, dependency_graph.py]` |
| 内容生成引擎 | `[page_outline.py, page_generator.py, outline_anchors.py, fact_check.py, test_page_outline.py, prompt_segment.py]` |
| 质量校验与修订 | `[fact_check.py, page_outline.py, page_generator.py, test_fact_check.py, prompt_segment.py]` |
| Mermaid 图表优化 | `[mermaid.py, page_outline.py, page_generator.py, test_mermaid_sanitize.py]` |
| 前端应用架构 | `[shared/config.py, wiki_planner.py, shared/models.py, mermaid.py, test_dependency_graph.py]` |
| Wiki 渲染组件 | `[wiki_planner.py, models.py, mermaid.py, page_outline.py, page_generator.py, test_mermaid_sanitize.py]` |
| 后端接口与服务 | `[web/lib/api.ts]` |

Cross-page file frequency in the same export:
- `worker/pipeline/wiki_planner.py` → 7 pages
- `worker/pipeline/page_generator.py` → 5 pages
- `worker/pipeline/page_outline.py` → 4 pages

The frontend pages contain zero `web/` files; the backend page contains a single frontend file.

---

## 3. Problem Statement

### P1 — File list is not ordered by relevance

`依赖图谱构建` is supposed to document `dependency_graph.py`, but the rendered page is dominated by `WikiPlanner` content. The page's `files` list places `wiki_planner.py` first; the retrieval query (`spec.title + files[:5]`) and the entity formatter both consume files in this order, so the larger, denser file wins.

### P2 — Orchestrator files are systematically under-covered

`内容生成引擎` should document `generate_page` and `generate_page_batch`; the rendered page barely mentions either. The page has six assigned files, FAISS retrieval has no per-file quota, and orchestrator code (mostly delegation and small helpers) loses to outline/validation code in chunk-level scoring.

### P3 — Sibling pages bleed content

`质量校验与修订` contains a section "大纲校验与生成优化" that documents `validate_outline`, which is on the sibling page `内容生成引擎`. The cause is mechanical: `page_outline.py` is in *both* pages' `files` lists, both pages retrieve overlapping chunks, and the outline prompt does not tell either page what its sibling covers.

### P4 — Phase-2 file scorer is ASCII-only and goes blind on non-English titles

`worker/pipeline/wiki_planner.py:733-735`:

```python
def _tokenize(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3}
```

For `前端应用架构 / 介绍 Next.js 应用的路由结构与交互组件库`, this yields `{"next"}` — a single token. `_score_file_for_page` then ranks files almost entirely by dep-graph centrality and entity count, completely missing the page's topic. The result: `前端应用架构.files` contains zero `web/` files. The same failure cascades through `Wiki 渲染组件`, `后端接口与服务`, and `实时通信机制`.

**A pure CJK tokenizer alone would not fix this.** Even with `{"前端", "组件", "应用", "架构"}` extracted, the candidate code paths are `web/components/...`, `web/app/...` — the token sets share **zero** elements. A cross-language bridge is required: the existing optional `en_keywords` field on the outline (`wiki_planner.py:478-481`) is exactly that bridge, but it is currently optional and rarely populated, so the prefilter falls back to ASCII tokenization.

### Why PR #40 is not enough

PR #40 raises capacity (`top_k 12→30`, `max_files 200→500..800`, `max_edges 500→1500`, `entity cap 25→8·N`, `max_tokens 16k→32k`). It addresses *truncation* and *summary completeness*. It does not address ordering, ownership, scoping, or cross-language matching — the four levers that drive P1–P4.

---

## 4. Root Cause Analysis

### 4.1 Code-level evidence

| Defect | Source | Mechanism |
|---|---|---|
| P1 ordering | `wiki_planner.py:334-353, 504-536` | `_SELECTION_SCHEMA.selections.items.files` is `array<string>` — no ordering hint; the system prompt at `wiki_planner.py:355-386` and the user prompt at `_build_selection_user` never ask the LLM to sort by relevance. |
| P1 propagation | `page_generator.py:225` | `queries = [f"{spec.title} {' '.join((spec.files or [])[:5])}"]` — first-N file paths flow directly into the retrieval query, so the order produced by Phase 2 directly shapes RAG context. |
| P2 chunk dilution | `page_generator.py:247` | `multi_search(query_vecs, k=top_k, doc_k=1)` returns top-k by global score with no per-file quota — one large file can monopolize the budget. |
| P2 entity dilution | `page_formatters.py:41` | `entities[:25]` (PR #40: `8·N`) is taken from a global list with no per-file quota; orchestrator files contribute fewer entities and are squeezed out. |
| P2 outline blindness | `page_outline.py:224-254` | Outline prompt never sees source code — only entity signatures and dep summary, so an orchestrator's "I delegate to A, B, C" structure is invisible at planning time. |
| P3 sibling unawareness | `page_outline.py:237-238` | Only `child_titles` is injected; sibling titles and out-of-scope topics are not. |
| P3 file co-ownership | Phase 4.6 selection design | After orphan enforcement was removed, no rule prevents two pages from claiming the same file. The export shows `page_outline.py` co-owned by `内容生成引擎` and `质量校验与修订`. |
| P4 ASCII tokenizer | `wiki_planner.py:733-735` | `_tokenize` strips all CJK characters; the `_prefilter_candidates → _score_file_for_page` pipeline becomes effectively title-blind for Chinese pages. |
| P4 cross-language gap | `wiki_planner.py:478-481, 808-809` | `en_keywords` is the documented bridge ("when page title/purpose are non-English, list 3–8 English directory or module names"). It is honored by `_score_file_for_page` but **declared optional**, so most plans omit it; the scorer then has no English signal at all. |

### 4.2 Why ordering-only or outline-only fixes are insufficient

Even if Phase 2 sorted `files` by relevance, P2 would persist because the retrieval/entity stages have no per-file quota; a top-ranked file would dominate so completely that mid-rank files would still be invisible. P3 cannot be solved by a smarter Phase 1 outline because the export shows the outline (titles + purposes + parents) is correct — leakage is between Phase 2 (file selection) and Pass 2 (draft retrieval).

### 4.3 Why `_score_file_for_page` cannot be the validator of LLM ordering

`_score_file_for_page` is a coarse heuristic (file extension, entity count, dep-graph in-degree, page-token overlap). For multilingual pages it is far weaker than the LLM, even after A7 lands. Using it to *override* LLM ordering would produce regressions whenever the heuristic is wrong (which is most of the time on architectural pages). The design therefore treats it as a **soft sanity check**, not a sort key:

1. Trust the LLM's ordering.
2. Re-rank only on egregious violations: a position-0 file with `_score_file_for_page ≤ 0`, or a file flagged by `_validate_selections` as not-on-this-page. In those cases, demote the offending file to the end of the list and log a `wiki_planner.ordering_demotion` event.
3. Prefer to fix ordering at the source: ask the LLM for relevance scores, not just an array — see A1 below.

---

## 5. Solution Design

### 5.1 Design principles

1. **Reuse, don't reinvent — and rename to reflect the shared role.** The deterministic index builder (`worker/pipeline/fast_report_index.py`), tokenizer (`worker/fast_report/search._tokenize`), graph-expansion + slice primitives (rest of `worker/fast_report/search.py`), and source-slice extractor (`worker/fast_report/slices.py`) were all written for fast reports but are domain-agnostic. The wiki path will consume them, so they must be renamed and relocated under `worker/pipeline/` (see A7 / A11 / A12 / A13). The fast-report code keeps thin re-export shims for one release. No new external dependencies.
2. **Rank-weighted fairness, not absolute fairness.** Once files are ordered by relevance, the chunk and entity budgets follow a graduated allocation: a small floor per file (so no file is invisible) plus a rank-weighted bonus that gives the top file the lion's share. This avoids starving the most important file while still preventing the second-most-important one from being squeezed out entirely.
3. **Explicit scope boundaries.** Sibling page titles and out-of-scope topics are first-class prompt inputs.
4. **Cross-language matching via `en_keywords`, not tokenizer cleverness.** The CJK tokenizer is necessary to handle CJK comments / paths / identifiers, but the real fix for the frontend collapse is to make `en_keywords` mandatory whenever `title` is non-English and to use it as an additional signal (and validation gate) in `_score_file_for_page`.
5. **Fail loudly.** No legacy fallbacks for new behaviors. If a stage exhausts its retries, the job fails with a structured error; we do not silently produce a degraded result.
6. **Skill-readiness as a constraint, not a deliverable.** Layers A and B must leave the wiki path independent of FAISS / embeddings (after B), with reusable utilities extracted under stable module paths, so a future Skill project can lift them verbatim.

### 5.2 Layer A — Planner & prompt patches

| ID | File(s) | Change |
|---|---|---|
| **A1** | `worker/pipeline/wiki_planner.py` | Extend `_SELECTION_SCHEMA` so each page's `files` becomes `array<{path: string, relevance: number /* 1-10 */}>`; the LLM emits explicit relevance scores. Update `_SYSTEM` and `_build_selection_user` to require most-representative-first ordering with relevance scoring. `_validate_selections` checks that scores are non-increasing along the array and that no file with score < 3 sits in position 0; if violated, emit a single feedback-retry to the LLM (one extra retry budget) before falling back to demote-and-log. The dataclass `WikiPageSpec.files` stays `list[str]` (unwrapped from the dict at the boundary), keeping downstream code unchanged. |
| **A2** | `worker/pipeline/page_outline.py:224-254`; `page_generator.py:266-274` | Extend `generate_page_outline` with `sibling_titles: list[str] \| None` and `out_of_scope_topics: list[str] \| None`. Inject them into the cacheable prefix as `Sibling pages (DO NOT cover their topics; reference by name only): ...` and `Out-of-scope (covered elsewhere): ...`. `generate_page_batch` computes siblings from `WikiPlan` (same `parent`) and derives `out_of_scope_topics` from sibling `purpose` first sentences. |
| **A3** | `worker/pipeline/page_draft.py:27` (`DRAFT_SYSTEM`) | Append a "Scope discipline" rule: stay within assigned files; if a topic is owned by a sibling listed in the prompt, give it ≤ 1 sentence and refer to the sibling by title. |
| **A4** | `worker/pipeline/page_generator.py:224-249` | Replace the single retrieval query with five queries: `spec.title`, `spec.purpose`, `" ".join(en_keywords)`, top-5 entity names, file stems. After `multi_search`, run `_balance_chunks(chunks, files=spec.files, k=top_k)` with a **rank-weighted quota**: `quota_i = max(floor, round(k · w_i / Σw))` where `w_i = 1 / (rank_i + 1)`. With the default `k=30, floor=2, len(files)=5`, the top file gets ~12 chunks, the second ~7, the third ~5, the fourth ~3, the fifth ~3 — graduated, not flat. Files at rank ≥ 6 receive only the floor. |
| **A5** | `worker/pipeline/page_formatters.py:41` | Refactor `_format_entity_details` to accept `files: list[str]` and use the same rank-weighted graduated quota as A4. The top-ranked file keeps the lion's share; lower-ranked files retain a small visibility floor. |
| **A6** | `worker/pipeline/page_outline.py:_build_outline_prompt` | New optional argument `signature_slices: dict[file, list[str]]`. For each `spec.file`, take 1–2 of the highest-importance entities and emit a `Signature slices` block (file:line range + first 4 lines of the entity body) into the cacheable prefix. The slices come from `FileAnalysis` plus a clone-root read. |
| **A7** | new `worker/utils/tokenize.py`; `worker/fast_report/search.py`; `worker/pipeline/wiki_planner.py` | **Extract** the CJK-aware tokenizer currently living at `worker/fast_report/search._tokenize` into `worker/utils/tokenize.py` as a public `tokenize_text(text: str) -> set[str]`. Update `worker/fast_report/search.py` to re-export the same function (no behavior change for fast-report). Replace `wiki_planner._tokenize` and the duplicate at `_score_file_for_page` with the shared helper. Audit other call sites (`_best_matching_page`, `_directory_cluster_assign`) and migrate them. Add unit tests covering CJK runs, mixed CJK+ASCII, camel case, and path segments. |
| **A8** | `worker/pipeline/wiki_planner.py:_select_files`, `_build_outline_prompt` | **Make `en_keywords` mandatory when `title` or `purpose` contains CJK characters.** Phase 1 prompt tightens the language: "If the page title or purpose contains non-Latin characters, you MUST provide 3–8 English keywords drawn from directory names, module names, or file basenames in the listing." `_validate_outline_structure` enforces this and triggers a Phase-1 retry on violation. `_score_file_for_page` weighs `en_keywords` ↔ path-segment matches at +4 per overlap (vs +0.5 for general token overlap), making them the dominant signal for cross-language pages. |
| **A9** | `worker/pipeline/wiki_planner.py:_select_files` | After validation, run `_enforce_ownership(selections, outline, dep_graph, file_infos)`: for any file owned by ≥ 2 pages, score each owner with `_score_file_for_page` and keep only the highest-scoring owner among siblings (pages sharing a `parent`); allow at most 2 non-sibling owners (architectural hubs); otherwise demote. Cap total assignments at `1.5 × len(all_repo_files)`. |
| **A10** | `worker/pipeline/wiki_planner.py:_select_files`, `_validate_selections` | Replace the static 5–8 / hard-cap-10 rule with `n_target = clamp(2, ceil(median_score / score_threshold), 8)` computed from the prefilter score distribution. Narrow topics get 2–3 files; broad topics keep 5–8. Hard upper bound stays at 10. |
| **A11** | `worker/pipeline/fast_report_index.py` → **`worker/pipeline/retrieval/repo_index.py`**; artifact `~/.autowiki/repos/{hash}/ast/fast_report_index.json` → **`repo_index.json`** | Rename the module and artifact to reflect the now-shared role (consumed by fast-report **and** wiki generation). Final module location is `worker/pipeline/retrieval/repo_index.py` per A15. Public API renames: `build_fast_report_index()` → `build_repo_index()`; `INDEX_VERSION` → `REPO_INDEX_VERSION`; loader / validator helpers in `worker/fast_report/jobs.py` (`_load_fast_report_index`, `_validate_fast_report_index_version`, `_FastReportIndexOutdatedError`) renamed to drop the `fast_report_` prefix and moved into a new `worker/pipeline/retrieval/repo_index_io.py` so wiki and fast-report load the same artifact through the same code path. The old `worker/pipeline/fast_report_index.py` is kept as a one-release deprecation shim that re-exports from the new module and emits a `DeprecationWarning`. **On-disk migration**: the loader auto-renames `fast_report_index.json` → `repo_index.json` if it finds the old name on disk; if both are absent, it forces a rebuild via Stage 2/3. |
| **A12** | new `worker/pipeline/retrieval/repo_search.py`; `worker/fast_report/search.py` | **Extract** the domain-agnostic retrieval primitives out of `worker/fast_report/search.py` into `worker/pipeline/retrieval/repo_search.py` (final location per A15): `score_file_for_query` (was `_score_file_multi_slice`), `expand_candidate_paths`, `build_slice_candidates`, `apply_token_budget`, `neighbors_for_graph`, plus the supporting dataclasses `RankedFile`, `ScoredEntity`, `SliceCandidate`. Promote them to public symbols. `worker/fast_report/search.py` keeps **only** the fast-report-specific orchestration (`retrieve_code_evidence` and the question-type plumbing) and imports primitives from the new shared module. Layer B's `KeywordIndex` (B1) and the section drafter (B2) consume `worker/pipeline/retrieval/repo_search.py` directly. The graph-walk profile registry currently inlined under `worker/fast_report/planning.expansion_graph_for(question_type)` is also lifted into `repo_search.py` as a profile-keyed function so wiki and fast-report each register their own profile (`"wiki_page"`, `"how_does_it_work"`, etc.) without import cycles. |
| **A13** | `worker/fast_report/slices.py` → **`worker/pipeline/retrieval/code_slices.py`** | Move the source-slice extractor (`extract_source_slice` and helpers) under `worker/pipeline/retrieval/` (final location per A15). Generic on-disk source-window extraction is not fast-report-specific. The old path keeps a re-export shim for one release. The Layer B section drafter consumes `worker.pipeline.retrieval.code_slices.extract_source_slice` directly. |
| **A14** | `worker/index/full.py:277`, `worker/index/refresh.py:400` | **Stop persisting `ast/file_analysis_summary.txt`.** It is a lossy text view of data already captured in `repo_index.json` (per-file class/function counts, imports, first docstring), is never read back by any code path (verified by `grep -rn "file_analysis_summary"`), and exists only as a human-debug snapshot that drifts out of sync with the actual prompt sent to Phase 1. The in-memory `FileAnalysis.to_llm_summary()` call inside `wiki_planner._build_outline_prompt` is unchanged — Phase 1 still receives the same string content. For the human-debugging use case, add an opt-in dump gated by `AUTOWIKI_DEBUG_DUMP_PROMPTS=1` that writes the *actual* Phase-1 prompt (not a stale pre-prompt summary) to `~/.autowiki/repos/{hash}/logs/phase1_prompt.txt` alongside any other prompt-debug dumps. After A14, `~/.autowiki/repos/{hash}/ast/` contains exactly two files: `repo_index.json` and `wiki_plan.json`. |
| **A15** | `worker/pipeline/**` | **Restructure `worker/pipeline/` into sub-packages by stage.** Without this, post-Layer-B the directory holds ~20 sibling files mixing analysis, indexing, retrieval, planning, and page generation. Final layout:<br><br>`worker/pipeline/`<br>&nbsp;&nbsp;`__init__.py` — re-exports public API for back-compat<br>&nbsp;&nbsp;`language.py`, `pipeline_logging.py` — cross-cutting<br>&nbsp;&nbsp;`ingestion.py`, `ast_analysis.py`, `dependency_graph.py` — Stages 1–3, kept at top level<br>&nbsp;&nbsp;`retrieval/` — `repo_index.py` (A11), `repo_index_io.py` (A11), `keyword_index.py` (B1), `repo_search.py` (A12), `code_slices.py` (A13)<br>&nbsp;&nbsp;`planner/` — `wiki_planner.py`, `outline_anchors.py`, `user_steering.py`<br>&nbsp;&nbsp;`page/` — `generator.py` (was `page_generator.py`), `outline.py` (was `page_outline.py`), `section_drafter.py` (B2 new), `formatters.py` (was `page_formatters.py`), `fact_check.py`, `diagram_post_processor.py`<br><br>Files inside `page/` drop the redundant `page_` prefix; files inside `planner/` keep their `wiki_`/full names because dropping them would collide with class names (`Planner`, `OutlineAnchors`, etc.) and existing test names. **No collision with `worker/index/`** (existing job-orchestration module): retrieval lives under `worker/pipeline/retrieval/`, not `worker/pipeline/index/`. **Back-compat**: `worker/pipeline/__init__.py` re-exports `WikiPlanner`, `WikiPageSpec`, `WikiPlan`, `generate_page`, `generate_page_batch`, `compute_generation_order`, `PageResult`, `validate_outline`, `PageOutline`, `SectionPlan`, `DiagramPlan`, `FileAnalysis`, `DependencyGraph`. One-release deprecation shim files remain at every old path (e.g. `worker/pipeline/page_generator.py` re-exports from `worker/pipeline/page/generator.py` with a `DeprecationWarning`). All test imports updated in the same PR; existing test names are preserved. |

**Naming rule going forward:** anything under `worker/fast_report/` must be fast-report-specific (orchestration, prompts, response shape). Cross-cutting infrastructure (indexing, retrieval primitives, slicing, tokenization) lives under `worker/pipeline/` or `worker/utils/`. Any future module that begins life inside `worker/fast_report/` and gains a wiki / deep-research consumer must be relocated in the same PR that adds the second consumer.

**Sub-package rule going forward:** new files added under `worker/pipeline/` must land in the matching sub-package (`retrieval/` for indexing & retrieval, `planner/` for Phase-1/2 planner concerns, `page/` for per-page generation passes); only Stages 1–3 (`ingestion.py`, `ast_analysis.py`, `dependency_graph.py`) and cross-cutting helpers (`language.py`, `pipeline_logging.py`) sit at the top level. Reviewers should reject PRs that add a new sibling module at the top level without a clear cross-cutting role.

**Combined effect on the export under analysis:**

- `依赖图谱构建` → `[dependency_graph.py, wiki_planner.py]` (A1 surfaces relevance, A9 demotes wiki_planner co-ownership)
- `内容生成引擎` → `[page_generator.py, page_outline.py]` (A9 removes `fact_check.py` / `test_page_outline.py` / `prompt_segment.py`; A1 promotes the orchestrator; A4/A5 give it dedicated chunk and entity quota)
- `质量校验与修订` → `[fact_check.py, test_fact_check.py]` (A9 removes `page_outline.py` co-ownership)
- `Mermaid 图表优化` → `[mermaid.py, diagram_post_processor.py, test_mermaid_sanitize.py]`
- `前端应用架构` → `web/app/` and `web/components/` files (A7 + A8: `en_keywords = ["web", "app", "components", "next"]` boosts paths matching those segments)
- `后端接口与服务` → `api/main.py`, `api/routers/*.py`, `api/queue.py` (A7 + A8: `en_keywords = ["api", "routers", "fastapi"]`)

### 5.3 Layer B — Section-level drafting + BM25 retrieval; FAISS removal

#### B1. Deterministic keyword index

Add `worker/pipeline/retrieval/keyword_index.py`:

```python
@dataclass
class KeywordIndex:
    chunks: list[Chunk]                    # same Chunk model as FAISSStore
    bm25: BM25Okapi                        # rank_bm25 — pure Python, no native deps
    file_to_chunks: dict[str, list[int]]
    token_idf: dict[str, float]            # built via worker.utils.tokenize.tokenize_text

    @classmethod
    def build(cls, chunks, *, repo_index): ...

    def search(
        self,
        queries: list[str],
        *,
        k: int,
        files: list[str] | None = None,
        per_file_quota: int = 2,
    ) -> list[Chunk]: ...
```

Why BM25 (`rank_bm25`) and **not** LlamaIndex: AutoWiki already owns its chunking, planner schema, and retrieval surface. `rank_bm25` is pure-Python with no native deps, plugs into the existing `Chunk` dataclass, and reuses the shared tokenizer extracted in A7. LlamaIndex would impose a different abstraction over chunking and retrieval that we do not need.

`KeywordIndex` consumes the renamed `repo_index.json` (A11) for token IDs and cross-file metadata, and uses the primitives extracted to `worker/pipeline/retrieval/repo_search.py` (A12) for graph expansion and slice building. No new index file is introduced — we widen the consumer of an existing artifact (now under a shared name).

#### B2. Outline → Skeleton → Section drafting → Stitch

Replace the single-shot Pass 2 with:

```
Pass 1   Outline                        — fast_llm, unchanged shape
Pass 2a  Skeleton (NEW)                 — fast_llm
Pass 2b  Section drafting (NEW)         — main_llm, parallel per section
Pass 2c  Stitch (NEW)                   — fast_llm
Pass 3   Fact-check                     — unchanged
Pass 4   Targeted revision              — unchanged
```

**Outline vs. Skeleton — explicit distinction:**

| | Outline (Pass 1, existing) | Skeleton (Pass 2a, new) |
|---|---|---|
| Output type | Structured JSON: `sections[]` (heading, kind, focus, diagram plan, source_files), `key_claims[]`, `out_of_scope_claims[]` | Markdown text: H1, H2 headings, one-line section purpose under each heading, no body text |
| Purpose | Decide *what topics exist and in what order* | Decide *the rendered shape of the page* — heading wording, ordering for narrative flow, where the diagrams will sit |
| Model | `fast_llm` | `fast_llm` |
| Length | ~200 lines of JSON | ~30 lines of Markdown |
| Why split | Outline answers the planner's question ("what should be on this page"); Skeleton answers the writer's question ("how should this page open, transition, close"). Splitting them lets the section drafter receive a stable Markdown frame to fill in, rather than re-deriving heading wording from JSON each time. |

`section.diagram.source_files` is **produced by the LLM in Pass 1** as part of `_OUTLINE_SCHEMA.sections[i].diagram.source_files`, restricted by `validate_outline` to be a subset of `spec.files`. The LLM is told explicitly: "for each section that warrants a diagram, list the 1–3 source files that the diagram is *most about*." This is unchanged from the existing schema; Layer B simply consumes it for retrieval scoping in Pass 2b.

**Pass 2b retrieval:** for each `SectionPlan`,

```
queries  = [section.heading, section.focus, top entity names]
scope    = spec.files ∩ (section.diagram.source_files ∪ heuristic-derived)
chunks   = KeywordIndex.search(queries, k=8, files=scope, per_file_quota=2)
```

This is what closes P2 at the root: the orchestrator section retrieves orchestrator chunks regardless of how dense the outline file is, because section scope explicitly intersects `diagram.source_files`.

**No legacy fallback.** If Pass 2a, 2b, or 2c exhausts retries, the job fails with `WikiGenerationError` and the structured retry log already in `pipeline_logging`. There is no `legacy_full_draft` escape hatch.

#### B3. `out_of_scope_claims` in outline schema

Extend `_OUTLINE_SCHEMA` with `out_of_scope_claims: array<string>`. The fact-check prompt receives this list; if a draft contains a claim matching any out-of-scope phrase, fact-check returns `verdict="fail"` and Pass 4 strips the offending sentences before LLM revision.

#### B4. Hard removal of Stage 4 and embedding provider

Layer B is a **hard cut**, not a flagged migration. There is no `AUTOWIKI_RETRIEVAL=faiss` escape hatch. Concrete deletions in this PR:

| Concern | Action |
|---|---|
| `Stage 4` (`worker/pipeline/rag_indexer.py`) | **Deleted.** No more FAISS build during indexing. `~/.autowiki/repos/{hash}/faiss.index` and `faiss.meta.pkl` are no longer produced. The 6-stage pipeline becomes 5-stage (Ingestion → AST → Dep graph → Wiki planner → Page generator). Existing on-disk artifacts can be left or `rm -rf`'d on next index. |
| `worker/pipeline/rag_indexer.py` | **Deleted.** No remaining consumer in the indexing path. |
| `worker/index/artifacts.py` | FAISS index lookup / loader removed. |
| `EmbeddingProvider` parameter | Removed from `generate_page`, `generate_page_batch`, the section drafter, and every job entrypoint in `worker/jobs.py`. |
| `make_embedding_provider` call sites in indexing | Removed. The provider is no longer constructed during indexing. |
| `LLMConfig` / `Config` embedding fields | Marked deprecated but kept (one release) so existing `autowiki.yml` files do not error out; documented as ignored. |
| `worker/embedding/*` | **Kept on disk** (so deep-research's later migration can be incremental), but no import remains in the wiki indexing path. Imports survive only inside `worker/research/*`, which is disabled — see B5. |
| Chat | Already FAISS-free (verified by grep — no `FAISSStore` / `EmbeddingProvider` imports in `worker/chat.py` or `api/`). No changes required. |

After B, **the indexing pipeline has zero FAISS / embedding dependency**. The retrieval primitives in the wiki path are `KeywordIndex` (B1) + `worker/pipeline/retrieval/repo_index.py` artifact `repo_index.json` (A11) + `worker/pipeline/retrieval/repo_search.py` (A12) + `worker/pipeline/retrieval/code_slices.py` (A13) + `worker/utils/tokenize.py` (A7) — all under the new `retrieval/` sub-package (A15). This is the Skill-readiness contract — the indexing pipeline can run inside a Skill sandbox with no embedding API key configured.

#### B5. Deep Research — temporarily disabled

Deep Research is the only feature still depending on `FAISSStore` + `EmbeddingProvider` (`worker/research/service.py:27,29,130-136,217-246`; `worker/research/jobs.py:15,33-43,80-82,122`). Since Stage 4 is deleted in B4, Deep Research has no index to query. Layer B explicitly disables the feature rather than leaving it half-broken:

| Surface | Behavior after Layer B |
|---|---|
| CLI `autowiki research` | Exits with `Deep Research is temporarily unavailable while migrating to keyword retrieval (see issue #TBD).` and a non-zero status. |
| API `POST /api/repos/{id}/research` | Returns HTTP 503 with the same message in `detail`. |
| API `GET /api/repos/{id}/research/{job_id}` | Returns HTTP 410 (gone) for any new request; existing reports already in SQLite remain readable. |
| WebSocket `/ws/repos/{id}/research/{job_id}` | Closes immediately with code 1011 + reason "feature disabled". |
| Frontend "Research" entry point | Hidden via a config flag served by `/api/repos/{id}` (`features.deep_research = false`). |
| `worker/research/jobs.py` | Job function registered but raises `FeatureDisabledError` on entry; no FAISS load attempt. |
| Tests | Existing research tests skipped via `pytest.mark.skipif(...)`; a new test asserts the 503 / CLI exit codes. |

A follow-up project will migrate Deep Research's per-step retrieval to `KeywordIndex` + 1-hop graph expansion (the same pattern fast-report uses). That work is **out of scope for this spec.** A tracking issue is filed before Layer B merges.

Documentation updates:
- `CLAUDE.md` API surface section: mark research endpoints as `(disabled — see issue #TBD)`.
- `docs/cli.md` / `docs/cli-zh.md`: same.
- `README.md`: feature list mentions Deep Research as "temporarily disabled, migrating to keyword retrieval."

### 5.4 Skill-readiness preparation (no in-tree Skill package)

Skill extraction will be a separate project. This spec only requires that Layers A and B leave the wiki path in a state from which a Skill can be built without further refactoring:

1. Tokenizer extracted to `worker/utils/tokenize.py` (A7).
2. Index builder + artifact renamed to `worker/pipeline/retrieval/repo_index.py` / `repo_index.json` (A11) — domain-neutral name signals shared ownership.
3. Retrieval primitives extracted to `worker/pipeline/retrieval/repo_search.py` (A12) — graph expansion, scoring, slice building, token-budget enforcement.
4. Source-slice extractor moved to `worker/pipeline/retrieval/code_slices.py` (A13).
5. Retrieval is `KeywordIndex` (B1) — pure Python (`rank_bm25`), no native deps, no embedding provider.
6. The section drafter (B2) takes only `(page_spec, repo_index, clone_root, llm, fast_llm)` as inputs — no FAISS, no embedding, no DB handle.
7. After B4, **no FAISS / embedding code path remains in the indexing pipeline** — the entire wiki generation flow runs without an embedding provider configured.
8. All retry / fail-loud behavior is centralized in `pipeline_logging`; no silent fallbacks remain in the wiki path.

The Skill project, when it starts, lifts items 1–4 verbatim plus a thin CLI shim. No further pipeline refactoring is required. The future Deep Research migration consumes the same shared modules.

---

## 6. Implementation Plan

### 6.1 Phasing & exit criteria

| Phase | PR(s) | LoC | Exit criteria |
|---|---|---|---|
| **A** | `feat/wiki-quality-layer-a` | ~600 | All A1–A15 patches merged (A1–A10 = behavior changes; A11–A14 = rename / extraction / artifact cleanup; A15 = directory restructure with deprecation shims); new tests in `tests/worker/test_wiki_planner.py` (relevance schema, ownership, CJK tokenizer extraction, mandatory `en_keywords`, adaptive cap), `tests/worker/test_page_generator.py` (rank-weighted chunk quota), `tests/worker/test_repo_index.py` (artifact rename + on-disk migration), `tests/worker/test_repo_search.py` (extracted primitives), `tests/worker/test_index_artifacts.py` (`file_analysis_summary.txt` no longer written; `ast/` contains exactly `repo_index.json` + `wiki_plan.json`), `tests/worker/test_pipeline_layout.py` (sub-package boundaries; back-compat re-exports; deprecation shim warnings) pass; existing fast-report tests pass unchanged via deprecation shims; smoke-index against AutoWiki produces the file-list expectations in §5.2 for all eight defective pages. |
| **B1** | `feat/keyword-index` | ~300 | `KeywordIndex.search` matches FAISS top-k recall ±10% on a 100-question fixture (run during this PR before FAISS is deleted); `rank_bm25` added to `pyproject.toml`. |
| **B2** | `feat/section-drafting-and-stage4-removal` | ~400 | Section-level drafting wired in; **Stage 4 deleted; `worker/pipeline/rag_indexer.py` deleted; `EmbeddingProvider` removed from every indexing call site**; Deep Research disabled per B5 with HTTP 503 / CLI exit / frontend hide; full self-index runs with no embedding API key configured; chat regress-clean. |

PR #40 lands first; every layer assumes its budgets. Layer B is two PRs but executed as a single sprint — B1's recall-parity check is the gate before B2 deletes FAISS.

### 6.2 Test plan

Each layer ships with deterministic tests against `tests/fixtures/simple-repo/` plus a self-index regression that asserts the file-list expectations in §5.2.

- **Layer A unit tests:**
  - `test_select_files_emits_relevance_scores` — Phase 2 result includes per-file `relevance ∈ [1,10]`, non-increasing.
  - `test_validate_selections_demotes_low_relevance_in_position_zero`.
  - `test_enforce_ownership_demotes_sibling_share` — `page_outline.py` cannot be assigned to two sibling pages.
  - `test_shared_tokenizer_handles_cjk_and_camel` — extracted `tokenize_text` covers CJK runs, camel case, paths.
  - `test_phase1_requires_en_keywords_for_cjk_titles` — Phase 1 rejects an outline whose CJK-titled page lacks `en_keywords`.
  - `test_score_file_for_page_uses_en_keywords` — boost on path-segment overlap with `en_keywords` is the dominant signal for `前端应用架构`.
  - `test_balance_chunks_rank_weighted` — top file receives the largest share; every file receives at least the floor.
  - `test_outline_prompt_includes_siblings`.
  - `test_draft_system_prompt_scope_rule`.
  - `test_repo_index_migration_renames_old_artifact` — loader auto-renames `fast_report_index.json` → `repo_index.json` on first read.
  - `test_repo_index_deprecation_shim_emits_warning` — importing `worker.pipeline.fast_report_index.build_fast_report_index` still works but warns.
  - `test_repo_search_primitives_callable_from_pipeline` — wiki and fast-report both import from `worker/pipeline/retrieval/repo_search.py` without import cycles.
  - `test_pipeline_top_level_only_has_stages_and_helpers` — top level of `worker/pipeline/` lists only `ingestion.py`, `ast_analysis.py`, `dependency_graph.py`, `language.py`, `pipeline_logging.py`, `__init__.py`, plus the three sub-package directories.
  - `test_pipeline_back_compat_reexports` — `from worker.pipeline import WikiPlanner, generate_page, FileAnalysis, DependencyGraph` etc. all resolve.
  - `test_pipeline_old_paths_emit_deprecation_warning` — `import worker.pipeline.page_generator` works but emits `DeprecationWarning`.

- **Layer B integration tests:**
  - `test_keyword_index_recall_parity` — BM25 vs FAISS recall on a fixture question set (run during B1, before FAISS is deleted).
  - `test_section_drafter_independent_retrieval` — orchestrator section receives orchestrator chunks even when outline file dominates the page.
  - `test_out_of_scope_claims_trigger_factcheck_fail`.
  - `test_indexing_runs_without_embedding_provider` — full self-index completes with no embedding API key set in `Config`.
  - `test_stage4_artifacts_not_produced` — `~/.autowiki/repos/{hash}/faiss.index` and `faiss.meta.pkl` do not exist after a fresh index.
  - `test_research_endpoint_returns_503` — `POST /api/repos/{id}/research` returns 503 with the expected message; CLI `autowiki research` exits non-zero.

- **Self-index regression:** a CI job runs `autowiki index . --reuse-index=false` against the AutoWiki repo and asserts that the eight pages listed in §5.2 contain the expected primary file as their first `files[0]`.

### 6.3 Roll-back & feature flags

- `AUTOWIKI_PLANNER_OWNERSHIP=enforce|advise|off` for A9 — default `advise` for one release, then `enforce`.

There is **no `AUTOWIKI_RETRIEVAL` flag and no draft-mode flag.** Layer B is a hard cut: FAISS / embedding code is deleted, not gated. Rollback for Layer B means reverting the PR. The earlier draft of this spec proposed a `legacy_full_draft` fallback and a FAISS escape hatch; both are removed per the design principle "fail loudly."

### 6.4 Risks & mitigations

| Risk | Mitigation |
|---|---|
| LLM emits `relevance` scores that all cluster at the high end | A1 requires non-increasing order, not a specific spread; even uniformly high scores still produce a valid rank. |
| BM25 underperforms FAISS on wiki-page section drafting | B1 ships first with a recall-parity gate against FAISS on a fixture set; B2 only deletes FAISS once the gate passes. |
| Deep Research outage during the migration window | B5 explicitly disables the surface (HTTP 503 + CLI non-zero + frontend hide) and a tracking issue commits to a follow-up migration to `KeywordIndex`. The user has accepted this trade-off. |
| Existing `~/.autowiki/repos/{hash}/faiss.*` files become orphaned | They are harmless on disk; documentation notes they can be deleted. No on-disk migration required. |
| `autowiki.yml` files reference embedding providers | Embedding fields in `Config` deprecated for one release with a warning; the indexing path simply ignores them. |
| Section drafting balloons LLM call count | Section pass uses `fast_llm` per section; `AUTOWIKI_PAGE_CONCURRENCY` already bounds parallelism; per-section context is much smaller, so wall-clock time stays comparable to single-shot. |
| Ownership enforcement starves "hub" files (e.g. `models.py`) | A9 explicitly allows up to 2 non-sibling owners and exempts top-decile in-degree files via the existing `_compute_hub_modules`. |
| Mandatory `en_keywords` makes Phase 1 reject more outlines | A8 piggybacks on the existing Phase-1 retry budget (`max_retries=3`); the failure mode is an extra retry, not a hard fail. |
| Tokenizer extraction (A7) ripples into `_directory_cluster_assign` and changes existing assignments | Tests pin behavior against the AutoWiki self-index; rolled out together with A8/A9 so behavior change is intentional. |

### 6.5 Telemetry

Use the existing `pipeline_logging.log_validation_retry` / `log_final_failure` channels. Add structured events at:

- `wiki_planner.ordering_demotion` — page, demoted_file, original_position, score.
- `wiki_planner.ownership_demotion` — file, demoted_page, primary_page, score_delta.
- `wiki_planner.en_keywords_required` — page, retry_attempt.
- `page_generator.balance_chunks` — page, files, allocated_per_file, leftover.
- `page_section_drafter.section_factcheck` — page, section, verdict, issue_count.

---

## 7. Acceptance Criteria

Layer A ships when, on a fresh AutoWiki self-index:

1. The eight pages enumerated in §5.2 carry the file lists predicted there, with `files[0]` matching the predicted primary file.
2. `质量校验与修订.md` contains no section whose primary subject is `validate_outline` or `PageOutline`.
3. `内容生成引擎.md` contains at least one of the symbols `generate_page`, `generate_page_batch`, or `compute_generation_order`.
4. `前端应用架构.md` contains at least three references to files under `web/`.
5. No source file is assigned to more than two pages, except files in the top-decile by `dep_graph` in-degree.
6. Every CJK-titled page in `wiki_plan.json` has a non-empty `en_keywords` field.

Layer B ships when, in addition:

7. Section-level fact-check pass rate ≥ the previous full-page fact-check pass rate, measured over the AutoWiki self-index plus three external fixture repos.
8. Full self-index completes with **no embedding API key configured anywhere** in `Config` / env / `autowiki.yml`.
9. `~/.autowiki/repos/{hash}/faiss.index` and `faiss.meta.pkl` are not produced by a fresh index run.
10. `worker/pipeline/rag_indexer.py` no longer exists in the tree; `grep -r "FAISSStore\|EmbeddingProvider" worker/` returns hits only inside `worker/embedding/` and `worker/research/` (the disabled, awaiting-migration code).
11. Deep Research surfaces (CLI, REST, WebSocket, frontend entry point) all return the disabled response specified in B5.
12. **Naming hygiene** (Layer A subset, but listed here as a release-gate check): no `fast_report` prefix appears on any module / artifact / public symbol that has more than one consumer; `repo_index.json` is the artifact name on disk; `worker/pipeline/retrieval/repo_index.py`, `worker/pipeline/retrieval/repo_search.py`, `worker/pipeline/retrieval/code_slices.py`, and `worker/utils/tokenize.py` exist; the `worker/pipeline/fast_report_index.py`, `worker/fast_report/slices.py` deprecation shims emit `DeprecationWarning` on import.
13. **Artifact minimalism**: after a fresh full index, `~/.autowiki/repos/{hash}/ast/` contains exactly `repo_index.json` and `wiki_plan.json` — no `file_analysis_summary.txt`, no `faiss.*`.
14. **Directory hygiene**: `ls worker/pipeline/` lists exactly `__init__.py`, `language.py`, `pipeline_logging.py`, `ingestion.py`, `ast_analysis.py`, `dependency_graph.py`, plus the three sub-package directories `retrieval/`, `planner/`, `page/`. Old sibling files survive only as deprecation shims.

---

## 8. Open Questions

- **Relevance scale.** Should A1's `relevance` be `int 1-10` or `float 0-1`? `int 1-10` is easier to read in `wiki_plan.json` and aligns with how humans rate things; `float 0-1` matches typical LLM scoring norms. Proposal: `int 1-10` for human readability.
- **Ownership timing.** A9 enforcement runs after `_validate_selections`; should it gain its own retry budget, or piggyback on the existing one? Proposal: piggyback, with one extra round only when ownership demotion empties a page entirely.
- **Skeleton ownership.** Pass 2a (Skeleton) sits between Outline and per-section drafting. Should the Skeleton be regenerated when Pass 3 fact-check fails on heading wording, or only when section-level content fails? Proposal: section-level only; heading wording is rarely the issue and a Skeleton rerun would invalidate already-drafted siblings.
- **Deep Research follow-up timing.** Should the migration to `KeywordIndex` be the very next sprint after Layer B, or deferred until after the Skill project? Proposal: file the tracking issue when B merges; sequence after the Skill project so the same retrieval pattern (per-step BM25 + 1-hop expansion via `repo_index.json` + `repo_search.py`) can be lifted from there.
