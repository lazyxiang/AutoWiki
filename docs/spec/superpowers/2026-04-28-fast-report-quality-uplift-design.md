# Fast Report Quality Uplift

## Summary

This document specifies the next-iteration upgrade of the fast report pipeline. The goal is to lift report quality from "summarization of indexed metadata" to "explanation grounded in real implementation slices", while preserving the deterministic, no-vector-search retrieval philosophy established by the 2026-04-23 fast report redesign.

The redesign closes five concrete gaps between the prior spec and the current implementation:

1. Evidence payloads currently contain assembled metadata text (`File: …`, `signature: …`, `doc: …`, `imports: …`), not real code. The four-layer model defined in the prior spec already promised "implementation slices", but the implementation never wired them in.
2. Retrieval budgets are hardcoded as `seed=2 / depth=2 / result=4`, too small for cross-module flow questions and uniform across question types.
3. The fast report path retrieves only three of the four spec layers. The Interpretive Context Layer is missing entirely.
4. The deterministic index (`fast_report_index.json`) only carries file-level token / import / imported_by signals plus per-entity headers. It has no call sites, no exception touchpoints, no configuration touchpoints, and no repository-shape signals (full directory tree, hub modules) — so it can locate but cannot ground LLM planning or explanation.
5. Per-file scoring keeps only the single best-matching entity. Files where multiple entities are relevant to the question lose information at both the scoring step and the slice anchoring step.

This design fixes all five gaps without changing the user-facing report flow, the URL model, the 7-day TTL, the commit-SHA binding, or the frontend evidence rail components.

## Goals

- Replace metadata-shaped evidence with real source code slices read from the indexed clone.
- Upgrade `fast_report_index.json` from a file-level token graph to a symbol- and touchpoint-level graph for the highest-ROI question types, plus repository-shape signals (full directory tree, hub modules) that ground both planning and generation.
- Drive retrieval budgets, expansion graphs, slice line caps, and slices-per-file from `question_type` rather than from a single hardcoded constant.
- Allow each file to contribute multiple slices when multiple entities meaningfully match the question.
- Restore the Interpretive Context Layer as a deterministic, source-bound explanatory layer that informs generation but never independently justifies a claim.
- Harden the LLM planning step with repository-shape context and structured `question_type` enumeration so planning remains the reliable input to the now-richer downstream retrieval.
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

- `worker/pipeline/fast_report_index.py` — index schema bump to `index_version: 2`; new field extractors; `top_level_entries` removed.
- `worker/fast_report_search.py` — adaptive retrieval algorithm; replaces fixed `_SEED_LIMIT / _EXPANSION_DEPTH / _RESULT_LIMIT` constants with a per-`question_type` profile table; multi-slice per-file scoring and citation IDs; new per-graph expansion modes.
- `worker/fast_report.py` — adds the Interpretive Context Layer to `retrieve_fast_report_layers`; updates the generation prompt to embed it under explicit no-citation rules; injects `directory_tree` into the structure layer signals; emits richer `analysis_update` events.
- `worker/jobs.py` — fast report entrypoint validates `index_version` before retrieval; surfaces an actionable failure when the index is outdated; removes the legacy `top_level_entries` fallback path.

### Modules added

- `worker/fast_report_slices.py` — pure-functional source slice extractor. Reads files from the indexed clone at the report's commit SHA, returns `{snippet_start, snippet_end, full_start, full_end, code, truncated_lines}` payloads.
- `worker/fast_report_interpretive.py` — Interpretive Context Layer assembler. Pulls module docstrings, entity docstrings, leading comments, and README section bodies from `fast_report_index.json`, scores them deterministically, and returns a render-ready interpretive bundle.
- `worker/fast_report_planning.py` — planner-input assembly: derives `directory_tree`, `hub_modules`, and `readme_headings` views from the index for plan-prompt injection; defines the `question_type` enum.

### Touched data shapes

- `fast_report_index.json` — bumps to `index_version: 2`. Removes `top_level_entries`. Adds `directory_tree`, `hub_modules`, `readme_sections`, and per-file `call_sites`, `exception_touchpoints`, `config_touchpoints`, `module_docstring`, plus per-entity `leading_comment`.
- `FastReportEvidenceBlock.code` — payload becomes real source text. The dataclass shape itself does not change. `snippet_start` / `snippet_end` / `full_start` / `full_end` continue to mean the line range of the slice and its expansion bounds.
- `FastReportSectionResult` — gains an internal `interpretive_sources` field so it can be recorded in the analysis trace, but the field is not surfaced through the existing public DTO and not rendered in the evidence rail.
- `FastReportCitation.id` — citation ids for code-evidence shift from `code-{N}` to `code-{file_idx}-{entity_idx}` to support multi-slice-per-file emission.

### Untouched on purpose

- `FastReportCitation` schema fields (only the `id` formatting convention changes), `FastReportDiagram` schema, related wiki linking rules, Mermaid sanitization, language-detection rules, the canonical heading set, the arbitration rule (`code_evidence` ∪ `repository_structure` only).

## Index Schema v2

`fast_report_index.json` bumps to `index_version: 2`. New fields are additive. One legacy field (`top_level_entries`) is removed because it is a strict subset of the new `directory_tree`.

### Top-level fields

```jsonc
{
  "index_version": 2,

  "directory_tree": "api/\n  main.py\n  jobs.py\n  routes/\n    repos.py\nworker/\n  fast_report.py\n  ...\n",

  "hub_modules": [
    {
      "path": "shared/fast_report_types.py",
      "in_degree": 14,
      "purpose": "Shared dataclasses for fast report citations, evidence blocks, ..."
    }
  ],

  "readme_headings": [
    "AutoWiki",
    "Architecture",
    "Generation Pipeline",
    "Deployment"
  ],

  "readme_sections": [
    {
      "heading": "Architecture",
      "body": "AutoWiki uses a 6-stage pipeline ..."   // capped to ~800 chars
    }
  ],

  "files": { ... }
}
```

### Removed: `top_level_entries`

`top_level_entries` is the literal first level of `directory_tree`. Keeping both invites drift. Removed from the schema entirely. Code paths that previously read it (worker/jobs.py structure layer; worker/pipeline/fast_report_index.py) now derive from `directory_tree` or are removed when no longer needed.

### `directory_tree`

A single string holding a compact nested representation of the repository's filtered file tree.

- **Source**: every path in `_collect_rel_paths` after applying gitignore + standard exclusion list (see below).
- **Format**: nested indent format, two spaces per level. Directories end with `/`. Files appear as plain leaves. Sorted alphabetically per directory.
- **Exclusions**: `.git`, `node_modules`, `dist`, `build`, `target`, `__pycache__`, `.next`, `.turbo`, `.venv`, `venv`, `.cache`, `.pytest_cache`, `coverage`, `.mypy_cache`, `.ruff_cache`, `*.pyc`, `*.lock`, `*.min.js`, plus anything matching `.gitignore`.
- **Soft target**: ≤ 15k tokens (≈ 60k characters). For most repositories this is well under target.
- **Hard cap and degradation**: if the formatted tree exceeds 25k tokens, fall back to depth-3-only mode (directories at depth ≤ 3 listed; below depth 3, only file leaves whose path appears in `hub_modules` are retained). If still over 25k, drop sparse subdirectories (those with the fewest entities per the index) until under cap.
- **Stored once** in the index; consumed by both the plan prompt and the generation prompt's structure layer (free reuse).

### `hub_modules`

A small list of central modules ranked by `in_degree` (length of `imported_by`). These are the most-depended-upon files in the repository.

- **Computation**: sort all files in the index by `len(imported_by)` descending; take the top 20 with `in_degree >= 2`.
- **Per-entry fields**: `path` (relative path), `in_degree` (integer), `purpose` (first sentence of `module_docstring`, capped at 120 chars; null when no docstring is available).
- **Why hubs and not entry points**: true entry points are high out-degree, low in-degree files (e.g., `api/main.py`, `worker/jobs.py`). Detecting them reliably across nine languages requires per-language heuristics (`if __name__ == "__main__"`, `[project.scripts]`, `package.json` `bin`, Go `func main()`) of marginal accuracy. Hub modules are computed from a single uniform metric across all languages and are equally informative for grounding LLM planning.

### `readme_sections`

```jsonc
{
  "readme_sections": [
    {
      "heading": "Architecture",
      "body": "AutoWiki uses a 6-stage pipeline ..."
    }
  ]
}
```

`body` is the natural-language paragraph body following each heading, up to the next heading or end-of-file. Each body is capped at **800 characters** during indexing. Cumulative `readme_sections` payload is bounded at **10k tokens**; sections beyond that are dropped during indexing in heading order (later sections drop first).

`readme_headings` continues to exist as a separate field. It is the full ordered list of all README heading strings (no bodies). This is used in the plan prompt (full overview of README structure), distinct from `readme_sections` (top-N selected by token overlap with the question).

### Per-file fields

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
- **`symbol_path`** is `<rel_path with dots replacing slashes, extension stripped>.<entity_name>`. For `worker/fast_report.py` + `plan_fast_report_search` → `worker.fast_report.plan_fast_report_search`. This is **not** the language-native qualified name; it is a uniform synthetic identifier suitable for substring matching across all supported languages. The plan prompt explicitly tells the LLM to use this convention when emitting `retrieval_focus` hints.

### Build cost expectations

The new fields ride on the existing single-pass AST analysis. `directory_tree` and `hub_modules` are derived after AST analysis from already-collected paths and import edges. Expected indexing-time impact on `tests/fixtures/simple-repo`: +20% to +40%. Expected `fast_report_index.json` size impact: +30% to +60%, dominated by `readme_sections`, `directory_tree`, and `call_sites`.

## Plan Step Inputs and Hardening

The LLM planning step is the single point that converts a natural-language question into the structured intent that drives the now-richer downstream retrieval (budget profile, expansion graph, slice line caps, slices-per-file). Its output quality bounds the rest of the pipeline. This section specifies the inputs the planner sees and the hardening rules that make its output reliable.

### Current planner inputs (audit)

The current `plan_fast_report_search` prompt (worker/fast_report.py:241-250) gives the LLM only four pieces of information:

| Input                  | Source                              |
|------------------------|-------------------------------------|
| Repository name        | The `repo_name` argument            |
| Question               | The user's natural-language string  |
| Output language hint   | Locally detected before LLM call    |
| JSON schema fields     | Embedded in the prompt instructions |

The LLM has **no view** of the repository's file tree, modules, README, symbols, or any indexed signal. It guesses paths and symbols from the question text and from any prior knowledge of the repository it may have. This is the root cause of imprecise `retrieval_focus` hints today.

### Plan output fields and downstream consumers

`FastReportQuestionIntent` carries seven fields. Each has at least one downstream consumer; misses or hallucinations at this step propagate into retrieval.

| Field             | Example                                                | Downstream consumers                                                                                                                                                  |
|-------------------|--------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `language`        | `"en"` / `"zh"`                                        | `assemble_fast_report_markdown` (canonical heading translation); `_build_generation_prompt` (output language hint).                                                   |
| `question_type`   | `"execution_flow"`                                     | **Drives the entire adaptive retrieval profile**: budget table, expansion graph, slice line cap, `slices_per_file`. Also enters `query_tokens` for file/entity scoring. |
| `target`          | `"fast report retrieval pipeline"`                     | Enters `query_tokens`; surfaced verbatim in the generation prompt.                                                                                                    |
| `answer_shape`    | `"step-by-step explanation with code anchors"`         | Enters `query_tokens`; surfaced verbatim in the generation prompt.                                                                                                    |
| `evidence_shape`  | `"function bodies showing call chain"`                 | Enters `query_tokens`; activates config-file whitelist when it contains `"config"`.                                                                                   |
| `search_terms`    | `["retrieve", "code evidence", "expansion"]`           | Each term is tokenized and unioned into `query_tokens`. Surfaced in the generation prompt.                                                                            |
| `retrieval_focus` | `["worker.fast_report.retrieve_fast_report_layers"]`   | **Highest-impact field**: triggers `_focus_hint_score` (+4 to +14 score boost), forces test/config files in via `_is_low_signal_entry` override. Surfaced in prompt.    |

`question_type` and `retrieval_focus` are the two fields whose correctness most strongly determines retrieval quality.

### Hardening v1

Three changes lift planner reliability:

#### 1. `question_type` enum

The schema constrains `question_type` to a fixed set:

```python
"question_type": {
    "type": "string",
    "enum": [
        "architecture",
        "execution_flow",
        "dependency",
        "error_handling",
        "configuration",
        "testing",
        "implementation_location",
        "unknown",
    ],
},
```

Provider-side structured-output enforcement (Anthropic / OpenAI / Gemini) treats this as a hard constraint. The planner can no longer return ad-hoc strings like `"general"` or `"how_X_works"` that bypass the budget profile table.

#### 2. Repository-shape context injection

The plan prompt gains four new derived sections, all sourced from the index — zero extra work at request time:

| Plan prompt block       | Source                          | Purpose                                                                                          |
|-------------------------|---------------------------------|--------------------------------------------------------------------------------------------------|
| `Directory tree:`       | `index.directory_tree`          | Real path-level visibility so `retrieval_focus` can name actual files and modules.               |
| `README headings:`      | `index.readme_headings` (top 12)| Self-described section structure of the repository.                                              |
| `Hub modules:`          | `index.hub_modules` (top 20)    | Names and one-line purposes of the most-depended-upon files; helps select the right subsystem.   |
| `Symbol path convention:` | static instruction              | Explicit instruction: "Use `module.path.symbol_name` (path slashes → dots, extension stripped) for `retrieval_focus`." |

Estimated added prompt size: ~3k–18k tokens depending on repo size. The planner uses `fast_llm` (Haiku-class), which has a 200k context — even a 50k-line directory tree fits comfortably.

#### 3. Single-shot feedback retry

When the parsed plan output is degenerate — `question_type == "unknown"` **and** both `search_terms` and `retrieval_focus` are empty — re-prompt the planner once with feedback:

```text
Your previous plan returned no question_type and no retrieval hints. The
repository has the following structure:

{directory_tree summary}

{readme_headings}

Re-plan the search. Choose one of the enumerated question_type values and
return at least one retrieval_focus hint pointing at a real path or symbol.
```

Single-shot only. If the second attempt is still degenerate, fall through with the original parsed result; the deterministic retrieval layer is robust to thin intent thanks to question-text token overlap.

The retry path uses the same prompt-caching boundary as the initial call (the directory tree is in a cacheable system segment).

## Adaptive Retrieval

The current code retrieves four files via `seed=2 / depth=2 / result=4` and walks `imports + imported_by` regardless of question type. The new path is parameterized by `question_type`.

### Budget profiles

| `question_type`           | seed | depth | result_limit | code_evidence token budget | per-slice line cap | `slices_per_file` |
|---------------------------|------|-------|--------------|----------------------------|--------------------|-------------------|
| `architecture`            | 4    | 3     | 12           | 50k                        | 40                 | 3                 |
| `execution_flow`          | 3    | 3     | 10           | 50k                        | 50                 | 2                 |
| `dependency`              | 3    | 2     | 10           | 40k                        | 30                 | 1                 |
| `error_handling`          | 2    | 2     | 8            | 35k                        | 40                 | 2                 |
| `configuration`           | 3    | 2     | 8            | 35k                        | 30                 | 2                 |
| `testing`                 | 2    | 1     | 6            | 40k                        | 60                 | 2                 |
| `implementation_location` | 2    | 1     | 4            | 25k                        | 200                | 1                 |
| _(default / unknown)_     | 2    | 2     | 6            | 40k                        | 50                 | 1                 |

`result_limit` caps the number of distinct files entering the slice extraction stage. `slices_per_file` caps the number of slices any single file may contribute (see Multi-slice scoring below). `code_evidence token budget` is the final guard: once cumulative slice tokens exceed it, slices are dropped in ascending `score` order until the payload fits. Dropped slices are removed entirely (not downgraded to metadata). The drop count surfaces in the analysis trace.

Token estimates use a coarse `len(text) / 4` approximation. Tiktoken is not required.

### Expansion graphs

The seed file ranking algorithm (`_score_file` in `worker/fast_report_search.py`, plus the multi-slice scoring extension below) is the same across all question types. What changes is how seeds are expanded into the final selection set.

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

### Multi-slice scoring

The current `_score_file` keeps only the single best-matching entity per file (via `if candidate_score > entity_score`). Files where multiple entities are relevant lose information at both scoring (only the best entity contributes to file score) and slice anchoring (only one entity becomes `matched_entity`).

The new algorithm:

1. Compute the per-entity score for every entity in the file using the existing formula:
   `entity_score = 2 * |query_tokens ∩ _entity_tokens(entity)| + _focus_hint_score(entity, ...)`.
2. Sort entities by `entity_score` descending.
3. Define `K = profile.slices_per_file` for the question's profile.
4. Select the top-K entities **subject to** `entity_score >= 0.5 * top_entity_score` (drop weakly-matched entities even if they're in the top-K).
5. **File score** = `file_level_score + sum(selected_entity_scores)`. The file's ranking thus reflects combinatorial relevance, not just the best single match.
6. **Slice emission**: each selected entity produces one `SliceResult`, anchored at that entity's `start_line` / `end_line`, with the question_type's per-slice line cap. Each slice gets its own citation id of the form `code-{file_idx}-{entity_idx}` (file_idx in selection order, entity_idx in score order within the file).

For files with no entities (e.g., a pure config file matched by a `config_touchpoint`), one slice is emitted, anchored on the touchpoint line.

The token-budget guard runs across **all slices** (not per file), so a file with three slices contributes three rows to the budget accounting.

### Slice extraction parameters

After the file selection set is finalized, each selected entity is sliced. The slice always starts at the entity's `start_line`. If the entity's body exceeds the question_type's per-slice line cap, the slice ends at `start_line + cap` and a single trailing comment line is appended:

```text
# … 47 more lines truncated
```

Each slice is wrapped with **±5 lines of context** above and below (changed from ±3 in the prior spec). `FastReportEvidenceBlock.full_start` and `full_end` continue to expose the +15-line expansion bounds the frontend uses.

## Real Source Slice Extraction

A new module `worker/fast_report_slices.py` owns slice extraction.

### Public API

```python
@dataclass(frozen=True)
class SliceResult:
    snippet_start: int        # 1-based, inclusive
    snippet_end: int          # 1-based, inclusive
    full_start: int           # snippet_start - 5, clamped to 1
    full_end: int             # snippet_end + 5, clamped to file length
    code: str                 # real source, not metadata
    truncated_lines: int      # 0 if full entity fits the slice cap

def extract_source_slice(
    *,
    clone_root: Path,
    rel_path: str,
    anchor_start: int,
    anchor_end: int,
    line_cap: int,
    context_lines: int = 5,
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

1. **Entity docstrings** of every entity selected by the multi-slice scoring step (not just one per file).
2. **Module docstrings** (`module_docstring`) of every selected file.
3. **Leading comments** (`leading_comment`) of every selected entity.
4. **README section bodies** ranked against the question.

`docs/` directory scans, in-body comments, and design-doc files are explicitly out of scope for v1.

### Selection rules

For each entity selected by the code evidence layer (potentially multiple per file under the multi-slice rule), the interpretive layer **automatically attaches** that entity's `docstring`, `leading_comment`, and the file's `module_docstring` if any of them are non-empty. No scoring runs for the auto-attached set; the binding is structural.

The cumulative payload of auto-attached docstrings + leading comments is bounded at **8k tokens**. When the cumulative total exceeds this cap, attached items are dropped in ascending `entity_score` order until under cap. This protects the prompt from a single 200-line module docstring crowding out everything else.

In addition, README section bodies are ranked independently:

- Tokens for ranking come from `intent.search_terms ∪ intent.retrieval_focus ∪ tokens(question)`.
- Score = `|tokens ∩ tokens(heading + body)|`.
- Top **5** sections are kept (changed from 3 in the prior spec). Each body is hard-capped at **800 characters** (changed from 400). Cumulative section payload is hard-capped at **10k tokens** (changed from 3k).

### Prompt placement

The generation prompt gains a new section between the existing `Code evidence layer:` and `Curated knowledge layer:` blocks:

```text
Interpretive context layer:
- Module docstring (worker/fast_report.py): Fast report domain service.
- Entity leading comment (FastReportPipeline): Bottom-up child-synthesis ...
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

## Generation Prompt Changes

The prompt builder (`_build_generation_prompt` in `worker/fast_report.py`) gains an enriched structure layer, the interpretive block, and a small wording change to direct the model toward real-source reading.

### Structure layer enrichment

The structure layer signals expand from the prior three lines (top_level_entries / readme_headings[:6] / README first 160 chars) to:

```text
Repository structure layer:
- Directory tree:
  api/
    main.py
    ...
- README headings: AutoWiki, Architecture, Generation Pipeline, Deployment, ...
- README first paragraph: AutoWiki is a self-hosted, open-source AI-powered wiki ...   (capped at 400 chars)
- Hub modules:
  - shared/fast_report_types.py — Shared dataclasses for fast report ...
  - worker/llm/base.py — Abstract base for LLM provider implementations ...
  ...
```

The directory tree and hub modules **contribute context, not citations**. `RepositoryStructureLayer.citations` continues to emit a single citation anchored at README.md (struct-1). LLM claims that name a path from the directory tree without an attached code-evidence citation are dropped during arbitration, exactly as today.

### Curated layer wording changes

- Wiki page summary truncation increases from 200 to 400 characters in `_curated`.

### Final prompt structure

```text
Repository structure layer:
{directory_tree, readme_headings, readme first paragraph, hub_modules}

Code evidence layer:
{format_retrieved_chunks_for_prompt(layers.code_evidence.snippets)}
   (now containing real source slices, including multiple slices per file
    where slices_per_file > 1)

Interpretive context layer:
{auto-attached docstrings/leading_comments + top-5 README sections}

Use this interpretive layer ONLY to explain or connect code evidence.
Never cite it as primary support. Final claims must cite repository_structure
or code_evidence ids.

Curated knowledge layer:
{up to 3 wiki pages, summaries up to 400 chars}
```

`format_retrieved_chunks_for_prompt` is already source-text-aware (it accepts `{file, start_line, end_line, text}`), so wiring real source through it requires no upstream change.

The arbitration step (`arbitrate_report_claims`) is unchanged.

## Observability

The `analysis_update` WebSocket event gains structured phases. The event schema is unchanged; only the values surface more detail.

### New phase identifiers

| `phase`                       | emitted with                                                                          |
|-------------------------------|---------------------------------------------------------------------------------------|
| `index_check`                 | `{ index_version }`                                                                   |
| `search_plan`                 | `{ question_type, search_terms[], retrieval_focus[], plan_retried: bool }`            |
| `code_evidence_seed`          | `{ files: [{path, score}] }` for the seeded set                                       |
| `code_evidence_expansion`     | `{ files: [{path, role, score}], graph: "call_sites" \| ... }`                        |
| `slice_extraction`            | `{ files: [{path, slices: [{entity, lines, truncated_lines}]}], dropped_due_to_budget }` |
| `interpretive_layer`          | `{ entity_docs, module_docs, readme_sections }` counts and dropped_due_to_cap         |
| `generation`                  | `{ prompt_token_estimate }`                                                           |
| `arbitration`                 | `{ claims_kept, claims_dropped }`                                                     |

`slice_extraction` carries nested per-slice information so multi-slice-per-file events are visible.

The persisted `analysis_trace` on `report_section` records the full ordered event list, so reopened reports show the same trace they showed during generation. No live re-emission on reopen.

### Logging

Every retry, every fallback, every dropped slice goes through `worker/pipeline/pipeline_logging.py` (per CLAUDE.md's "Pipeline observability" rule). Silent `except: pass` is forbidden.

## Testing Strategy

### Unit tests

- `fast_report_slices.extract_source_slice`
  - happy path: returns real source within range
  - file missing: returns `None`
  - line range exceeds file length: clamps to file length, no exception
  - over-cap entity: returns truncated slice with trailing `… N more lines truncated` marker for each supported language
  - context bounds: `full_start = max(1, anchor_start - 5)`, `full_end = min(file_len, anchor_end + 5)`
- `fast_report_index` v2 build
  - schema includes `index_version: 2`, all new fields, and **no** `top_level_entries`
  - `directory_tree` is a non-empty nested string with the documented exclusions applied
  - `directory_tree` falls back to depth-3-only mode when over the 25k-token hard cap
  - `hub_modules` ranks by `len(imported_by)` and includes `module_docstring` first sentence as `purpose`
  - call_sites populated from a fixture with one file calling another
  - exception touchpoints captured for `raise ValueError(...)` and `try/except`
  - config touchpoints captured for `os.getenv("X")`
  - leading_comment captures the comment block immediately above an entity, ignores blank-line-separated comments
  - readme_sections respects the 800-char-per-section and 10k-token cumulative caps and emits top 5
- `fast_report_planning`
  - `question_type` enum is enforced — non-enum values cause provider-level rejection (mocked)
  - degenerate plan output triggers exactly one feedback retry
  - directory_tree, hub_modules, and readme_headings appear in the constructed plan prompt
- `fast_report_search` adaptive
  - profile lookup table returns expected `(seed, depth, result, token_budget, line_cap, slices_per_file)` for each `question_type`
  - `execution_flow` expansion uses `call_sites` and ignores `imported_by`
  - `error_handling` expansion uses `exception_touchpoints` and excludes test files
  - `configuration` expansion uses `config_touchpoints` matching by `config_key`
  - **multi-slice scoring**: a file with three high-scoring entities under `architecture` (slices_per_file=3) emits three slices with citation ids `code-{i}-0`, `code-{i}-1`, `code-{i}-2`
  - **multi-slice threshold**: an entity scoring < 50% of the file's best entity is dropped from the slice set even when slices_per_file allows more
  - over-budget eviction drops lowest-scored slices and reports the drop count
- `fast_report_interpretive`
  - auto-attached docstrings/comments include only entities present in the code-evidence layer (potentially multiple per file)
  - cumulative auto-attached payload is capped at 8k tokens; over-cap items dropped in ascending entity_score order
  - README section ranking uses token overlap; top-5 are returned; over-budget sections are dropped
  - interpretive payload produces zero `FastReportCitation` records
- `worker/fast_report._build_generation_prompt`
  - structure layer signals contain `directory_tree`, `readme_headings`, README first paragraph (≤400 chars), and `hub_modules`
  - structure layer emits exactly one citation (struct-1, README) regardless of how many directory entries are listed
- `worker/jobs` index_version gate
  - missing version → 409 with `actionable_command`
  - `index_version: 1` → 409
  - `index_version: 2` → proceeds
- Persistence
  - reopen with matching `commit_sha` returns persisted markdown without LLM call
  - reopen with mismatched `commit_sha` returns the expired state (matches existing TTL-expired path)

### Integration tests

- `tests/fixtures/simple-repo` is reindexed at v2; report is generated for an `execution_flow` question and the evidence rail payload contains real source lines (not `File: …` metadata).
- A second report is generated for an `architecture` question; verify wider expansion is exercised (more than 4 files in the slice extraction trace event) and that at least one file emits multiple slices (slices_per_file=3).
- A third report is generated for an `error_handling` question; verify exception_touchpoints expansion is taken (expansion graph trace value is `exception_touchpoints`).
- The plan prompt for any of the three reports contains `Directory tree:` block.
- After reindexing the same repo at a different commit (simulated by mutating SHA in the DB), reopening any prior report returns the expired state.

### Coverage target

`worker/` and `api/` coverage stays at the existing ≥80% threshold. The new modules (`fast_report_slices.py`, `fast_report_interpretive.py`, `fast_report_planning.py`) ship with ≥85% line coverage to make up for the slightly more complex AST extraction code in `fast_report_index.py`.

## Risks

- **Indexing time regression**. Touchpoint extraction adds AST traversal cost. `directory_tree` and `hub_modules` are derived after AST analysis. Mitigation: every new extractor reuses the AST tree already produced by the single-pass analyzer; no second parse. Acceptance threshold: ≤50% wall-clock regression on `tests/fixtures/simple-repo`.
- **Index size inflation**. `readme_sections`, `directory_tree`, and `call_sites` are the largest contributors. Mitigation: per-section caps (800 chars), cumulative caps (10k tokens), `directory_tree` adaptive degradation above 25k tokens.
- **Tree-Sitter touchpoint fidelity varies by language**. Python has the cleanest extraction; C/C++/C# may yield lower-fidelity exception and config touchpoints. v1 ships with best-effort extractors per language. Languages where extraction yields zero touchpoints fall back to the secondary expansion graph automatically — no crash, just less precision.
- **Slice extraction depends on the live clone**. If the clone is missing or partially corrupt, slices fail individually. The pipeline must continue with the surviving citations. Failure to load the clone at all is a hard error returned to the user (matches today's behavior).
- **Reindex invalidates outstanding reports**. This is the user-asked-for invariant. Surfacing it well in the UI is a frontend concern; v1 reuses the existing TTL-expired state.
- **Prompt token cost rises significantly**. Real source slices, multi-slice-per-file emission, larger interpretive payloads, and structure-layer enrichment combine to push the typical generation prompt to ~80k–100k tokens. The per-`question_type` token budgets cap the increase. With Sonnet 4.6 at $3/M input tokens, the per-report cost rises to roughly $0.30 — a deliberate quality/cost trade.
- **Multi-slice citation ids change format**. Existing persisted reports use `code-{N}` ids; new reports use `code-{file_idx}-{entity_idx}`. There is no migration concern because the report record is self-contained — old reports keep old ids in their persisted markdown, new reports use new ids; the frontend matches ids by string equality and does not interpret the format.

## Acceptance Criteria

- `fast_report_index.json` written by the upgraded indexer carries `index_version: 2`, populates `directory_tree`, `hub_modules`, `readme_sections`, `call_sites`, `exception_touchpoints`, `config_touchpoints`, `module_docstring`, per-entity `leading_comment`, and **does not** contain `top_level_entries`.
- A fast report generated against `tests/fixtures/simple-repo` produces evidence blocks whose `code` field contains real source lines, verified by spot-checking that the rendered text matches the file content at the cited line range.
- Reports for `architecture` or `execution_flow` questions select more than four files (i.e., the old hardcoded `_RESULT_LIMIT = 4` is no longer the ceiling).
- An `architecture`-class report on a fixture with multi-entity files emits at least one file with three slices, each with a distinct citation id of the form `code-{file_idx}-{entity_idx}`.
- Reports for `error_handling`, `configuration`, and `execution_flow` questions are observed to use the matching expansion graph (`exception_touchpoints`, `config_touchpoints`, `call_sites`) in their `analysis_trace`.
- The plan prompt contains a `Directory tree:` block, a `README headings:` block, and a `Hub modules:` block; the plan output's `question_type` is one of the eight enum values.
- The generation prompt's structure layer signals contain a `Directory tree:` block, and the structure layer continues to emit exactly one citation (anchored at README).
- The Interpretive Context Layer appears in generation prompts and produces no `FastReportCitation` records; arbitration drops any claim whose only support is interpretive.
- Indexes without `index_version: 2` cause `POST /api/repos/{repo_id}/fast` to return HTTP 409 with the actionable error payload, and the WebSocket emits a single `error` event before closing.
- A report generated against commit SHA `X` returns the expired state when reopened after the repository has been reindexed at SHA `Y ≠ X`, regardless of the 7-day TTL.
- Indexing wall-clock regression on `tests/fixtures/simple-repo` is ≤50%.
- Generated reports for the same question on the same commit are byte-stable for the deterministic layers (search plan, retrieval, slice extraction). The LLM-generated narrative is naturally non-deterministic, but the citation set, expansion path, and slice line ranges are reproducible.

## Open Questions

- Whether to expose the expansion graph choice (e.g., `analysis_trace.code_evidence_expansion.graph`) to the frontend evidence rail header. Out of scope for v1; revisit if user feedback requests it.
- Whether to record per-call-site direction (caller-of vs callee-of) in the analysis trace for richer debugging. Defer until a real debugging need surfaces.
- Whether to allow the LLM planner to override the budget profile via a single `wants_broader_context: bool` flag (option C from brainstorming). Deferred — start strict, relax later if needed.
- Whether `slices_per_file` should also drive the interpretive auto-attach budget (currently auto-attach is bounded only by the 8k cumulative cap). Defer until we observe whether interpretive payloads cluster around large multi-slice files.

## Appendix A: Tokenizer Rules

The tokenizer (`_tokenize` in `worker/fast_report_search.py`) is shared by file-level token computation, entity-level token computation, query-token assembly, README-section ranking, and wiki-page ranking. Its behavior is therefore load-bearing across the retrieval path. This appendix documents the rules in full so they can be referenced from any test or implementation discussion.

### Inputs and outputs

- Input: any string.
- Output: `set[str]` of normalized lowercase tokens with length ≥ 2.

### CJK rules

- A "CJK run" is a maximal contiguous substring matching `[㐀-䶿一-鿿豈-﫿぀-ヿ가-힯]+`.
- For each CJK run:
  - The whole run is added as a token.
  - Every contiguous bigram within the run is added.
  - Every contiguous trigram within the run is added.

### ASCII rules

- The string is lowercased and the punctuation `/` and `.` are pre-replaced with spaces (so `worker.fast_report.X` → `worker fast report X`).
- The string is split by `[^A-Za-z0-9]+` (any non-alphanumeric run, including `_`, `-`, ` `, `,`, `(`, `)`, etc.).
- Each resulting part is further split at camelCase boundaries (`(?<=[a-z0-9])(?=[A-Z])`).
- Each split piece is lowercased and stripped; pieces of length < 2 are dropped.

### Examples

| Input                                                  | Tokens emitted                                                                |
|--------------------------------------------------------|-------------------------------------------------------------------------------|
| `retrieve_fast_report_layers`                          | `retrieve`, `fast`, `report`, `layers`                                        |
| `getUserConfig`                                        | `get`, `user`, `config`                                                       |
| `worker.fast_report.retrieve_fast_report_layers`       | `worker`, `fast`, `report`, `retrieve`, `layers`                              |
| `os.environ.get("API_KEY")`                            | `os`, `environ`, `get`, `api`, `key`                                          |
| `path/to/file.py`                                      | `path`, `to`, `file`, `py`                                                    |
| `配置加载`                                             | `配置加载`, `配置`, `置加`, `加载`, `配置加`, `置加载`                       |
| `RetryStrategy`                                        | `retry`, `strategy`                                                           |
| `HTTPClient`                                           | `httpclient` (no break between consecutive uppercase letters)                 |

### Token sources used during scoring

- **File-level tokens** (precomputed in `_file_tokens`): tokens of `rel_path` ∪ tokens of every entity's `name + symbol_path + signature`. **Docstrings are not included.**
- **Entity-level tokens** (computed at retrieval in `_entity_tokens`): tokens of `entity.name + symbol_path + signature + docstring`. **Docstrings are included** at this layer for entity-level scoring.
- **Query tokens** (`_query_tokens`): tokens of `question` ∪ tokens of `intent.question_type` ∪ tokens of `intent.target` ∪ tokens of `intent.answer_shape` ∪ tokens of `intent.evidence_shape` ∪ tokens of every entry in `intent.search_terms` ∪ tokens of every entry in `intent.retrieval_focus`.

The asymmetric inclusion of docstrings (entity-level only, not file-level) is intentional. File-level token sets are stored on disk; including docstrings would inflate the index. Entity-level tokens are computed in memory at scoring time, so docstring inclusion costs only CPU, not storage.

## Appendix B: Token Budget Summary

A single source of truth for every numeric cap in the fast report path. When tuning, edit this table first and propagate the change to the implementing code.

### Index-time caps

| Cap                                                | Value           | Where applied                                |
|----------------------------------------------------|-----------------|----------------------------------------------|
| `readme_sections` body per section                 | 800 chars       | `fast_report_index._extract_readme_sections` |
| `readme_sections` cumulative                       | 10k tokens      | same                                         |
| `directory_tree` soft target                       | 15k tokens      | `fast_report_index._build_directory_tree`    |
| `directory_tree` hard cap (triggers degradation)   | 25k tokens      | same                                         |
| `hub_modules` count                                | top 20          | `fast_report_index._compute_hub_modules`     |
| `hub_modules` purpose first-sentence cap           | 120 chars       | same                                         |

### Plan prompt caps

| Cap                                | Value         | Where applied                              |
|------------------------------------|---------------|--------------------------------------------|
| `readme_headings` injected         | top 12        | `fast_report_planning.build_plan_prompt`   |
| `directory_tree` injected          | full (≤25k)   | same                                       |
| `hub_modules` injected             | top 20        | same                                       |

### Generation prompt caps (per question_type)

See the **Adaptive Retrieval / Budget profiles** table for the canonical values:

| `question_type`           | code_evidence token | per-slice line cap | slices_per_file |
|---------------------------|---------------------|--------------------|-----------------|
| `architecture`            | 50k                 | 40                 | 3               |
| `execution_flow`          | 50k                 | 50                 | 2               |
| `dependency`              | 40k                 | 30                 | 1               |
| `error_handling`          | 35k                 | 40                 | 2               |
| `configuration`           | 35k                 | 30                 | 2               |
| `testing`                 | 40k                 | 60                 | 2               |
| `implementation_location` | 25k                 | 200                | 1               |
| _default_                 | 40k                 | 50                 | 1               |

### Slice extraction caps

| Cap                                | Value     | Where applied                              |
|------------------------------------|-----------|--------------------------------------------|
| Slice context lines (each side)    | ±5        | `fast_report_slices.extract_source_slice`  |
| Frontend expansion increment       | +15 lines | (frontend, unchanged from prior spec)      |

### Interpretive layer caps

| Cap                                              | Value         | Where applied                                |
|--------------------------------------------------|---------------|----------------------------------------------|
| README sections selected for prompt              | top 5         | `fast_report_interpretive.select_sections`   |
| README section body in prompt                    | 800 chars     | same                                         |
| README sections cumulative in prompt             | 10k tokens    | same                                         |
| Auto-attached docstrings/comments cumulative     | 8k tokens     | `fast_report_interpretive.attach_to_entities`|

### Curated layer caps

| Cap                       | Value     | Where applied            |
|---------------------------|-----------|--------------------------|
| Wiki summary truncation   | 400 chars | `worker/jobs.py:_curated`|
| Wiki pages selected       | top 3     | same                     |

### Structure layer caps (generation prompt)

| Cap                              | Value     | Where applied                          |
|----------------------------------|-----------|----------------------------------------|
| README first paragraph injected  | 400 chars | `worker/jobs.py:_repository_structure` |
| `readme_headings` injected       | top 12    | same                                   |

### Aggregate per-report estimate

```
Plan prompt total:        ~18k tokens
Plan output:              ~500 tokens

Generation prompt total:
  Structure layer:        ~16k (mostly directory_tree)
  Code evidence:          25k–50k (per question_type)
  Interpretive:           ~18k (8k auto-attached + 10k README sections)
  Curated:                ~2k
  Scaffolding:            ~3k
  Generation output room: ~10k
  ───────────────────────────────────
  Total:                  ~74k–99k tokens

Per-report budget total:  ~92k–117k tokens
```

200k-context models retain ~50% headroom; 1M-context models are not budget-constrained. Per-report cost on Sonnet 4.6 is roughly $0.25–$0.40 — the deliberate quality/cost trade.
