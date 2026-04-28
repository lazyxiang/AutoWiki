# Fast Report Quality Uplift

## Summary

This document specifies the next-iteration upgrade of the fast report pipeline. The goal is to lift report quality from "summarization of indexed metadata" to "explanation grounded in real implementation slices", while preserving the deterministic, no-vector-search retrieval philosophy established by the 2026-04-23 fast report redesign.

The redesign closes four concrete gaps between the prior spec and the current implementation:

1. Evidence payloads currently contain assembled metadata text (`File: …`, `signature: …`, `doc: …`, `imports: …`), not real code. The four-layer model defined in the prior spec already promised "implementation slices", but the implementation never wired them in.
2. Retrieval budgets are hardcoded as `seed=2 / depth=2 / result=4`, which is too small for cross-module flow questions and too uniform across question types.
3. The fast report path retrieves only three of the four spec layers. The Interpretive Context Layer is missing entirely.
4. The deterministic index (`fast_report_index.json`) only carries file-level token / import / imported_by signals plus per-entity headers. It has no call sites, no exception touchpoints, and no configuration touchpoints — so it can locate but cannot explain.

This design fixes all four gaps without changing the user-facing report flow, the URL model, the 7-day TTL, the commit-SHA binding, or the frontend evidence rail components.

## Goals

- Replace metadata-shaped evidence with real source code slices read from the indexed clone.
- Upgrade `fast_report_index.json` from a file-level token graph to a symbol- and touchpoint-level graph for the highest-ROI question types.
- Drive retrieval budgets and expansion graphs from `question_type` rather than from a single hardcoded constant.
- Restore the Interpretive Context Layer as a deterministic, source-bound explanatory layer that informs generation but never independently justifies a claim.
- Keep all changes within the deterministic retrieval path — no embeddings, no FAISS, no vector similarity.
- Preserve the existing `FastReportEvidenceBlock` schema, the WebSocket contract, the report URL model, and the frontend rail components.

## Non-Goals

- Reintroduce vector search or embedding-based retrieval into the fast report path.
- Provide symbol-level name resolution (reference sites and test assertion points are explicitly deferred to a future iteration).
- Maintain backward compatibility with `index_version: 1` indexes. The new path is strict; old indexes are rejected with an actionable error.
- Change the report URL model, the 7-day TTL behavior, the four-layer retrieval framing, or the canonical heading set.
- Persist source code slices inside `fast_report_index.json`. Slices are extracted at generation time and persisted only inside the report record itself.
- Add small-context-model support knobs (env-var token budget overrides). The default model is Sonnet 4.6 with a 200k context; smaller models are out of scope for this iteration.

## Architecture Delta

The change is contained inside the worker pipeline. The frontend, API DTOs, persistence schema for `FastReportEvidenceBlock`, and WebSocket event types are unchanged.

### Modules touched

- `worker/pipeline/fast_report_index.py` — index schema bump to `index_version: 2`; new field extractors.
- `worker/fast_report_search.py` — adaptive retrieval algorithm; replaces the fixed `_SEED_LIMIT / _EXPANSION_DEPTH / _RESULT_LIMIT` constants with a per-`question_type` profile table; new per-graph expansion modes.
- `worker/fast_report.py` — adds the Interpretive Context Layer to `retrieve_fast_report_layers`; updates the generation prompt to embed it under explicit no-citation rules; emits richer `analysis_update` events.
- `worker/jobs.py` — fast report entrypoint validates `index_version` before retrieval; surfaces an actionable failure when the index is outdated.

### Modules added

- `worker/fast_report_slices.py` — pure-functional source slice extractor. Reads files from the indexed clone at the report's commit SHA, returns `{snippet_start, snippet_end, full_start, full_end, code, truncated_lines}` payloads.
- `worker/fast_report_interpretive.py` — Interpretive Context Layer assembler. Pulls module docstrings, entity docstrings, leading comments, and README section bodies from `fast_report_index.json`, scores them deterministically, and returns a render-ready interpretive bundle.

### Touched data shapes

- `fast_report_index.json` — new top-level `index_version` field; new top-level `readme_sections` array; new per-file fields `call_sites`, `exception_touchpoints`, `config_touchpoints`, `module_docstring`; new per-entity `leading_comment` field.
- `FastReportEvidenceBlock.code` — payload becomes real source text. The dataclass shape itself does not change. `snippet_start` / `snippet_end` / `full_start` / `full_end` continue to mean the line range of the slice and its expansion bounds.
- `FastReportSectionResult` — gains an internal `interpretive_sources` field so it can be recorded in the analysis trace, but the field is not surfaced through the existing public DTO and not rendered in the evidence rail.

### Untouched on purpose

- `FastReportCitation` schema, `FastReportDiagram` schema, related wiki linking rules, Mermaid sanitization, language-detection rules, the canonical heading set, the arbitration rule (`code_evidence` ∪ `repository_structure` only).

## Index Schema v2

`fast_report_index.json` bumps to `index_version: 2`. New fields are additive. Old fields are preserved.

### New top-level fields

```jsonc
{
  "index_version": 2,
  "top_level_entries": [...],
  "readme_headings": [...],
  "readme_sections": [
    {
      "heading": "Architecture",
      "body": "AutoWiki uses a 6-stage pipeline ..."  // capped to ~400 chars
    }
  ],
  "files": { ... }
}
```

`readme_sections.body` is the natural-language paragraph body that follows each heading, up to the next heading or end-of-file. Each body is capped at ~400 characters during indexing to avoid runaway README sizes. The cumulative `readme_sections` payload is bounded at ~3k tokens; sections beyond that are dropped during indexing in heading order (later sections drop first).

### New per-file fields

For every file entry under `files[<rel_path>]`:

```jsonc
{
  "path": "...",
  "tokens": [...],
  "imports": [...],
  "imported_by": [...],
  "external_deps": [...],
  "entities": [ /* now with leading_comment */ ],
  "is_test": false,
  "is_config": false,

  "module_docstring": "Service for ...",        // or null

  "call_sites": [
    {
      "caller_symbol_path": "worker.jobs.run_fast_report",
      "callee_name": "plan_fast_report_search",
      "line": 412
    }
  ],

  "exception_touchpoints": [
    {
      "kind": "raise" | "throw" | "try" | "except" | "catch",
      "symbol_path": "worker.jobs.run_fast_report",
      "line": 437,
      "message": "Repository index is outdated"   // or null
    }
  ],

  "config_touchpoints": [
    {
      "kind": "read" | "write",
      "config_key": "AUTOWIKI_LLM_PROVIDER",
      "line": 88,
      "scope": "module" | "function"
    }
  ]
}
```

`entities[i]` gains:

```jsonc
{
  "name": "...",
  "type": "...",
  "start_line": ...,
  "end_line": ...,
  "symbol_path": "...",
  "signature": "...",
  "docstring": "...",
  "leading_comment": "Single-pass AST analyzer ..."   // or null
}
```

### Field semantics

- **`call_sites`** are AST-level call expressions resolved against the file's local symbol table. The callee is recorded as a name (not a fully resolved symbol path) because AutoWiki does not run cross-file name resolution. This name is sufficient for fan-out: at retrieval time, candidate target files are looked up by matching `callee_name` against `entity.name` across the index.
- **`exception_touchpoints`** record `try` / `raise` / `throw` / `except` / `catch` AST nodes. The `message` field captures the literal-string argument when present (e.g., `raise ValueError("...")`); for non-literal messages, it is null. Test-only exception sites (inside `is_test` files) are emitted but tagged via the file's `is_test` flag — the retrieval layer uses this to keep test-driven expansions out of production-flow answers.
- **`config_touchpoints`** are detected via a small per-language whitelist of config-bearing modules and call patterns: `os.environ.get` / `os.getenv` (Python), `process.env.X` access (JS/TS), `viper.GetString` (Go), `System.getenv` (Java), and reads from any module recognized as `is_config` by the existing heuristic. The `config_key` is the literal string argument when present.
- **`leading_comment`** is the contiguous comment block immediately preceding the entity's start line, with no blank-line gap. Only block-style comments and language-native docstrings count; in-body comments do not.
- **`module_docstring`** is the file-level docstring (Python `"""..."""` at top of file, JS/TS `/** ... */` JSDoc at file head, Go package comment). Null for languages or files where it does not apply.

### Build cost expectations

The new fields ride on the existing single-pass AST analysis. Expected indexing-time impact on `tests/fixtures/simple-repo`: +20% to +40%. Expected `fast_report_index.json` size impact: +30% to +60%, dominated by `readme_sections` and `call_sites`.

## Adaptive Retrieval

The current code retrieves four files via `seed=2 / depth=2 / result=4` and walks `imports + imported_by` regardless of question type. The new path is parameterized by `question_type`.

### Budget profiles

| `question_type`           | seed | depth | result_limit | code_evidence token budget |
|---------------------------|------|-------|--------------|----------------------------|
| `architecture`            | 4    | 3     | 12           | 35k                        |
| `execution_flow`          | 3    | 3     | 10           | 35k                        |
| `dependency`              | 3    | 2     | 10           | 30k                        |
| `error_handling`          | 2    | 2     | 8            | 25k                        |
| `configuration`           | 3    | 2     | 8            | 25k                        |
| `testing`                 | 2    | 1     | 6            | 30k                        |
| `implementation_location` | 2    | 1     | 4            | 20k                        |
| _(default / unknown)_     | 2    | 2     | 6            | 30k                        |

`result_limit` is advisory. The token budget is the final guard: once the cumulative tokenized payload of selected slices exceeds the budget, slices are dropped in ascending `score` order until the payload fits. Dropped slices are not downgraded to metadata — they are removed entirely. The number of dropped slices is recorded in the analysis trace.

Token estimates use a coarse `len(text) / 4` approximation. Tiktoken is not required.

### Expansion graphs

The seed file ranking algorithm (`_score_file` in `worker/fast_report_search.py`) is unchanged. What changes is how seeds are expanded into the final selection set.

| `question_type`           | primary expansion graph                    | secondary fallback                  |
|---------------------------|--------------------------------------------|-------------------------------------|
| `architecture`            | `imports + imported_by`                    | sibling files in the same package    |
| `execution_flow`          | `call_sites` (callee → caller, both ways)  | `imports`                            |
| `error_handling`          | `exception_touchpoints` co-occurring files | `imports`                            |
| `configuration`           | `config_touchpoints` (matching key)        | `is_config` files in repo           |
| `dependency`              | `imports + imported_by`                    | `external_deps` overlap              |
| `testing`                 | sibling files + token overlap              | `imports`                            |
| `implementation_location` | `imports` (one hop only)                   | none                                 |
| _(default / unknown)_     | `imports + imported_by`                    | none                                 |

Expansion is a BFS bounded by `depth`. At each hop, the algorithm pulls neighbors from the primary graph first; if the neighbor pool is empty, it consults the secondary fallback. Once `result_limit` candidate files are collected, expansion stops.

### Per-graph expansion mechanics

- **`imports + imported_by`** — current behavior, preserved.
- **`call_sites`** — for each seed file's `call_sites`, look up files where any `entity.name` matches the `callee_name`; reverse direction (find files whose `call_sites.callee_name` matches a seed's entity) covers caller fan-out.
- **`exception_touchpoints`** — files that contain `raise` / `throw` / `try` blocks referencing the same symbol path or message tokens as the seed's exception touchpoints. Test files are excluded unless the question itself is in `testing`.
- **`config_touchpoints`** — files that read or write the same `config_key` as a seed's config touchpoints, plus any `is_config` file that defines that key.
- **sibling files + token overlap** — files in the same directory as a seed whose tokens overlap with the question.

### Slice line caps

After the file selection set is finalized, each file's primary entity is sliced. The slice line cap is also `question_type`-driven:

| `question_type`           | per-slice line cap |
|---------------------------|--------------------|
| `implementation_location` | 120                |
| `testing`                 | 60                 |
| `execution_flow`          | 50                 |
| `error_handling`          | 40                 |
| `configuration`           | 30                 |
| `dependency`              | 30                 |
| `architecture`            | 25                 |
| _(default)_               | 50                 |

The slice always starts at the entity's `start_line`. If the entity's body exceeds the cap, the slice ends at `start_line + cap` and a single trailing comment line is appended in the slice text:

```text
# … 47 more lines truncated
```

The `±3` lines of context attached on each side (per the prior spec) are preserved. `FastReportEvidenceBlock.full_start` and `full_end` continue to expose the +15-line expansion bounds the frontend uses.

For files with no entities (e.g., a pure config file matched by a `config_touchpoint`), the slice anchors on the touchpoint line, with the same line cap centered on it.

## Real Source Slice Extraction

A new module `worker/fast_report_slices.py` owns slice extraction.

### Public API

```python
@dataclass(frozen=True)
class SliceResult:
    snippet_start: int        # 1-based, inclusive
    snippet_end: int          # 1-based, inclusive
    full_start: int           # snippet_start - 3, clamped to 1
    full_end: int             # snippet_end + 3, clamped to file length
    code: str                 # real source, not metadata
    truncated_lines: int      # 0 if full entity fits the slice cap

def extract_source_slice(
    *,
    clone_root: Path,
    rel_path: str,
    anchor_start: int,
    anchor_end: int,
    line_cap: int,
    context_lines: int = 3,
) -> SliceResult | None: ...
```

### Behavior

- Reads `clone_root / rel_path` as UTF-8 with `errors="replace"`.
- If the file does not exist or read fails (binary, permissions), returns `None`. The caller drops the citation entirely; the report continues without that evidence block. This is the **only** failure mode that causes a citation to be dropped silently.
- Computes the effective slice as `[anchor_start, min(anchor_end, anchor_start + line_cap - 1)]`.
- If the entity body exceeds the cap, sets `truncated_lines = anchor_end - (anchor_start + line_cap - 1)` and appends a single language-appropriate trailing line of the form `<comment_token> … {N} more lines truncated` to the returned `code`. Comment tokens: `#` for Python / Ruby / Bash; `//` for JS / TS / Java / Go / Rust / C / C++ / C#.
- Tabs in the source are preserved. No reformatting, normalization, or syntax highlighting is performed at this layer — the frontend already handles syntax highlighting on real source.

### Commit-SHA binding

Slice extraction reads from the **single working tree** at `~/.autowiki/repos/{repo_hash}/clone/`. The clone is at the latest indexed commit. There is no per-SHA worktree per report.

At generation time, the clone always matches the report's `commit_sha` because generation runs immediately after indexing. Slices captured into the report record are therefore consistent with the indexed commit. Once the repository is reindexed at a newer commit, the clone advances but the persisted slices remain frozen on the original SHA. The invalidation rule below decides what to do with reopened reports in that case.

## Interpretive Context Layer

A new module `worker/fast_report_interpretive.py` owns this layer.

### Sources

The layer pulls only from `fast_report_index.json` — never from raw source. The following are the only allowed sources in v1:

1. **Entity docstrings** of every file/entity selected by the code evidence layer.
2. **Module docstrings** (`module_docstring`) of every selected file.
3. **Leading comments** (`leading_comment`) of every selected entity.
4. **README section bodies** ranked against the question.

`docs/` directory scans, in-body comments, and design-doc files are explicitly out of scope for v1.

### Selection rules

For each entity selected by the code evidence layer, the interpretive layer **automatically attaches** that entity's `docstring`, `leading_comment`, and the file's `module_docstring` if any of them are non-empty. No scoring runs for the auto-attached set; the binding is structural.

In addition, README section bodies are ranked independently:

- Tokens for ranking come from `intent.search_terms ∪ intent.retrieval_focus ∪ tokens(question)`.
- Score = `|tokens ∩ tokens(heading + body)|`.
- Top 3 sections are kept. Each body is hard-capped at 400 characters. Cumulative section payload is hard-capped at 3k tokens.

### Prompt placement

The generation prompt gains a new section between the existing `Code evidence layer:` and `Curated knowledge layer:` blocks:

```text
Interpretive context layer:
- Module docstring (worker/fast_report.py): Fast report domain service.
- Entity leading comment (FastReportPipeline): Bottom-up bottom-up child-synthesis ...
- Entity docstring (plan_fast_report_search): Plan fast-report retrieval ...
- README section "Architecture": AutoWiki uses a 6-stage pipeline ...

Use this layer only to explain or connect code evidence. Never cite this layer
as primary support for a claim. Final claims must still cite repository_structure
or code_evidence.
```

### Citation policy

- The interpretive layer **does not produce `FastReportCitation` records**.
- `available_citation_ids` passed to `arbitrate_report_claims` does **not** include any interpretive identifiers.
- Therefore any claim that names only an interpretive id will be dropped during arbitration. This preserves the prior spec's arbitration rule (`code_evidence` ∪ `repository_structure` only).
- The evidence rail does not render interpretive content. No new frontend component is added.

### Why no rendering

Showing interpretive sources in the rail invites users to treat them as evidence equivalent to code, which contradicts the arbitration rule. Interpretive content is generation fuel, not user-facing evidence.

## Persistence and Invalidation

This is the only behavioral change that affects users.

### Existing rules (unchanged)

- Reports persist for 7 days.
- Reports record `commit_sha`.
- Reopening within TTL re-renders without an LLM call.

### New rule: SHA-mismatch invalidation

When a report is opened (`GET /api/repos/{repo_id}/fast/{report_id}`):

- Compare `report.commit_sha` to `repository.last_indexed_commit_sha`.
- If they match: render normally from persisted state.
- If they do not match: return the same expired-state response that 7-day TTL expiration returns (HTTP 410, frontend renders the expired state with a regenerate affordance). The persisted report record is **not deleted** — the API simply refuses to serve it.
- This rule applies regardless of whether the report's `expires_at` has been reached.

### Why this is conservative-by-design

Persisted slices remain stable because they were captured at generation time. The user could in principle keep reading a stale-but-coherent report. But once the repo is reindexed at a newer commit, the report's narrative may reference behaviors that no longer exist in HEAD. The user explicitly asked for hard invalidation in this case so that "this report is fresh" is a property the URL guarantees.

### Implications

- A reindex (full or incremental) invalidates **all** outstanding fast reports for that repository.
- Reports are not garbage-collected on reindex — they are simply hidden behind the expired state until the 7-day TTL sweeper removes them. This keeps the persistence layer simple.
- The frontend renders a single expired state for both expiration causes (TTL elapsed and SHA mismatched). The expired state surfaces a "Regenerate" CTA. Distinguishing the two causes in copy is out of scope for v1.

## Migration: index_version v2

### Hard cutover

There is no parallel-path support for `index_version: 1`.

### Detection

`worker/jobs.py` `run_fast_report` (or its equivalent entrypoint) loads `fast_report_index.json` early and inspects `index_version`:

- Missing or `< 2`: short-circuit. Return a structured error of the form:

  ```json
  {
    "error": "fast_report_index_outdated",
    "message": "Repository index is outdated for fast reports. Run `autowiki index <repo>` to upgrade.",
    "actionable_command": "autowiki index <repo>"
  }
  ```

  The WebSocket pushes a single `error` event with this payload, then closes. The REST POST returns HTTP 409 Conflict.

### Existing pipelines unaffected

`fast_report_index.json` is consumed only by the fast report path. Wiki generation, deep research, chat, refresh, and validate-plan do not read it. So an outdated index does not block any other product surface — only fast reports are gated.

### Reindex flow

The user runs `autowiki index github.com/owner/repo` to upgrade. Because `--reuse-index` only skips the FAISS rebuild and does not skip AST analysis, a normal reindex regenerates `fast_report_index.json` automatically. No new flag is required.

## Observability

The `analysis_update` WebSocket event gains structured phases. The event schema is unchanged; only the values surface more detail.

### New phase identifiers

| `phase`                       | emitted with                                                        |
|-------------------------------|---------------------------------------------------------------------|
| `index_check`                 | `{ index_version }`                                                 |
| `search_plan`                 | `{ question_type, search_terms[], retrieval_focus[] }` (existing)   |
| `code_evidence_seed`          | `{ files: [{path, score}] }` for the seeded set                     |
| `code_evidence_expansion`     | `{ files: [{path, role, score}], graph: "call_sites" \| ... }`      |
| `slice_extraction`            | `{ files: [{path, lines, truncated_lines}], dropped_due_to_budget }`|
| `interpretive_layer`          | `{ entity_docs, module_docs, readme_sections }` counts              |
| `generation`                  | `{ prompt_token_estimate }`                                         |
| `arbitration`                 | `{ claims_kept, claims_dropped }`                                   |

The persisted `analysis_trace` on `report_section` records the full ordered event list, so reopened reports show the same trace they showed during generation. No live re-emission on reopen.

### Logging

Every retry, every fallback, every dropped slice goes through `worker/pipeline/pipeline_logging.py` (per CLAUDE.md's "Pipeline observability" rule). Silent `except: pass` is forbidden.

## Generation Prompt Changes

The prompt builder (`_build_generation_prompt` in `worker/fast_report.py`) gains the interpretive block and a small wording change to direct the model toward real-source reading:

```text
Code evidence layer:
{format_retrieved_chunks_for_prompt(layers.code_evidence.snippets)}

Interpretive context layer:
{interpretive bullet list}

Use this interpretive layer ONLY to explain or connect code evidence.
Never cite it as primary support. Final claims must cite repository_structure
or code_evidence ids.
```

`format_retrieved_chunks_for_prompt` is already source-text-aware (it accepts `{file, start_line, end_line, text}`), so wiring real source through it requires no upstream change.

The arbitration step (`arbitrate_report_claims`) is unchanged.

## Testing Strategy

### Unit tests

- `fast_report_slices.extract_source_slice`
  - happy path: returns real source within range
  - file missing: returns `None`
  - line range exceeds file length: clamps to file length, no exception
  - over-cap entity: returns truncated slice with trailing `… N more lines truncated` marker for each supported language
  - context bounds: `full_start = max(1, anchor_start - 3)`, `full_end = min(file_len, anchor_end + 3)`
- `fast_report_index` v2 build
  - schema includes `index_version: 2`, all new fields
  - call_sites populated from a fixture with one file calling another
  - exception touchpoints captured for `raise ValueError(...)` and `try/except`
  - config touchpoints captured for `os.getenv("X")`
  - leading_comment captures the comment block immediately above an entity, ignores blank-line-separated comments
  - readme_sections respects the 400-char-per-section and 3k-token cumulative caps
- `fast_report_search` adaptive
  - profile lookup table returns expected `(seed, depth, result, token_budget, line_cap)` for each `question_type`
  - `execution_flow` expansion uses `call_sites` and ignores `imported_by`
  - `error_handling` expansion uses `exception_touchpoints` and excludes test files
  - `configuration` expansion uses `config_touchpoints` matching by `config_key`
  - over-budget eviction drops lowest-scored slices and reports the drop count
- `fast_report_interpretive`
  - auto-attached docstrings/comments include only entities present in the code-evidence layer
  - README section ranking uses token overlap; top-3 are returned; over-budget sections are dropped
  - interpretive payload produces zero `FastReportCitation` records
- `worker/jobs` index_version gate
  - missing version → 409 with `actionable_command`
  - `index_version: 1` → 409
  - `index_version: 2` → proceeds
- Persistence
  - reopen with matching `commit_sha` returns persisted markdown without LLM call
  - reopen with mismatched `commit_sha` returns the expired state (matches existing TTL-expired path)

### Integration tests

- `tests/fixtures/simple-repo` is reindexed at v2; report is generated for an `execution_flow` question and the evidence rail payload contains real source lines (not `File: …` metadata).
- A second report is generated for an `architecture` question; verify wider expansion is exercised (more than 4 files in the slice extraction trace event).
- A third report is generated for an `error_handling` question; verify exception_touchpoints expansion is taken (expansion graph trace value is `exception_touchpoints`).
- After reindexing the same repo at a different commit (simulated by mutating SHA in the DB), reopening any prior report returns the expired state.

### Coverage target

`worker/` and `api/` coverage stays at the existing ≥80% threshold. The new modules (`fast_report_slices.py`, `fast_report_interpretive.py`) ship with ≥85% line coverage to make up for the slightly more complex AST extraction code in `fast_report_index.py`.

## Risks

- **Indexing time regression**. Touchpoint extraction adds AST traversal cost. Mitigation: every new extractor reuses the AST tree already produced by the single-pass analyzer; no second parse. Acceptance threshold: ≤50% wall-clock regression on `tests/fixtures/simple-repo`.
- **Index size inflation**. `readme_sections` and `call_sites` are the largest contributors. Mitigation: per-section caps (400 chars), cumulative cap (3k tokens), `call_sites` records names not bodies.
- **Tree-Sitter touchpoint fidelity varies by language**. Python has the cleanest extraction; C/C++/C# may yield lower-fidelity exception and config touchpoints. v1 ships with best-effort extractors per language. Languages where extraction yields zero touchpoints fall back to the secondary expansion graph automatically — no crash, just less precision.
- **Slice extraction depends on the live clone**. If the clone is missing or partially corrupt, slices fail individually. The pipeline must continue with the surviving citations. Failure to load the clone at all is a hard error returned to the user (matches today's behavior).
- **Reindex invalidates outstanding reports**. This is the user-asked-for invariant. Surfacing it well in the UI is a frontend concern; v1 reuses the existing TTL-expired state.
- **Prompt token cost rises**. Real source slices are heavier than metadata. The per-`question_type` token budgets cap the increase; the budget table is tuned to keep the median report under ~40k input tokens.

## Acceptance Criteria

- `fast_report_index.json` written by the upgraded indexer carries `index_version: 2` and populates `call_sites`, `exception_touchpoints`, `config_touchpoints`, `module_docstring`, per-entity `leading_comment`, and `readme_sections`.
- A fast report generated against `tests/fixtures/simple-repo` produces evidence blocks whose `code` field contains real source lines, verified by spot-checking that the rendered text matches the file content at the cited line range.
- Reports for `architecture` or `execution_flow` questions select more than four files (i.e., the old hardcoded `_RESULT_LIMIT = 4` is no longer the ceiling).
- Reports for `error_handling`, `configuration`, and `execution_flow` questions are observed to use the matching expansion graph (`exception_touchpoints`, `config_touchpoints`, `call_sites`) in their `analysis_trace`.
- The Interpretive Context Layer appears in generation prompts and produces no `FastReportCitation` records; arbitration drops any claim whose only support is interpretive.
- Indexes without `index_version: 2` cause `POST /api/repos/{repo_id}/fast` to return HTTP 409 with the actionable error payload, and the WebSocket emits a single `error` event before closing.
- A report generated against commit SHA `X` returns the expired state when reopened after the repository has been reindexed at SHA `Y ≠ X`, regardless of the 7-day TTL.
- Indexing wall-clock regression on `tests/fixtures/simple-repo` is ≤50%.
- Generated reports for the same question on the same commit are byte-stable for the deterministic layers (search plan, retrieval, slice extraction). The LLM-generated narrative is naturally non-deterministic, but the citation set, expansion path, and slice line ranges are reproducible.

## Open Questions

- Whether to expose the expansion graph choice (e.g., `analysis_trace.code_evidence_expansion.graph`) to the frontend evidence rail header. Out of scope for v1; revisit if user feedback requests it.
- Whether to record per-call-site direction (caller-of vs callee-of) in the analysis trace for richer debugging. Defer until a real debugging need surfaces.
- Whether to allow the LLM planner to override the budget profile via a single `wants_broader_context: bool` flag (option C from brainstorming). Deferred — start strict, relax later if needed.
