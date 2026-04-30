# AutoWiki Page Generation Quality — Design Spec

**Status:** Draft 1
**Author:** lazyxiang
**Date:** 2026-04-29
**Scope:** Wiki page generation pipeline (planner Phase 2 + page generator Pass 1–4) and a path to extracting it as a standalone Claude Skill.
**Related:** Issue #39, PR #40, `docs/superpowers/plans/2026-04-10-wiki-page-quality-redesign.md`

---

## 1. Executive Summary

Indexing AutoWiki against itself produces a wiki whose **structure is correct** but whose **page content is systematically wrong** in three distinct ways: relevant files are not ordered by relevance, orchestrator-class files are under-covered, and adjacent sibling pages bleed content into each other. A fourth, more severe failure surfaced when re-reading the actual `wiki_plan.json`: an entire frontend / backend subtree is assigned to the wrong source files because the Phase-2 file scorer is ASCII-only and goes blind on Chinese page titles.

PR #40 raises retrieval / token / edge budgets and is necessary, but **does not fix any of these defects on its own** — they are structural (ordering, ownership, scoping, tokenization), not capacity-bound.

This spec proposes a three-layer remediation, all of which can ship behind feature flags and roll back cleanly:

- **Layer A — Planner & prompt patches (1 PR, ~350 LoC).** Fix file ordering, sibling-aware outline, scope discipline in the draft system prompt, multi-query retrieval with per-file quotas, CJK-aware Phase-2 tokenization, file-ownership enforcement, and adaptive per-page file budgets. Targets P1, P3, and the frontend-collapse defect; partially mitigates P2.
- **Layer B — Section-level drafting + BM25 retrieval (2 PRs, ~600 LoC).** Replace the single-shot draft with `Skeleton → per-section draft → Stitch`, and add a deterministic BM25 retriever alongside FAISS. Targets P2 at the root.
- **Layer C — Wiki-as-Skill (1 PR + skill package, ~1000 LoC).** Drop the FAISS dependency by routing all retrieval through `fast_report_index.json` plus the BM25 retriever from Layer B, so the page generator can run in a Claude Skill sandbox.

---

## 2. Current State

### 2.1 Pipeline shape

```
Repo URL ──▶ Stage 1 Ingestion ──▶ Stage 2 AST analysis ──▶ Stage 3 Dep graph
        ──▶ Stage 4 RAG indexer (FAISS) ──▶ Stage 5 Wiki planner (2-phase LLM)
        ──▶ Stage 6 Page generator (4-pass LLM, per page, with concurrency)
```

The wiki planner is two LLM calls: Phase 1 produces an outline (titles, purposes, parents, optional `en_keywords`); Phase 2 selects 5–8 source files per page from a pre-filtered candidate set of 25–40 files. The page generator runs four passes per page: outline (fast model) → draft (main model) → fact-check (fast model) → conditional revision (main model).

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

### 2.3 Observed wiki (real data from re-export)

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
| 实时通信机制 | `[rag_indexer.py, models.py, jobs.py, page_generator.py, deep_research.py, test_deep_research.py]` |

Cross-page file frequency in the same export:
- `worker/pipeline/wiki_planner.py` appears in **7 pages**
- `worker/pipeline/page_generator.py` appears in **5 pages**
- `worker/pipeline/page_outline.py` appears in **4 pages**

The frontend pages contain zero `web/` files; the backend page contains a single frontend file.

---

## 3. Problem Statement

### P1 — File list is not ordered by relevance

`依赖图谱构建` is supposed to document `dependency_graph.py`, but the rendered page is dominated by `WikiPlanner` content. The page's `files` list places `wiki_planner.py` first; the retrieval query (`spec.title + files[:5]`) and the entity formatter both consume files in this order, so the larger, denser file wins. Users see a page whose title and content disagree.

### P2 — Orchestrator files are systematically under-covered

`内容生成引擎` should document `generate_page` and `generate_page_batch`; the rendered page barely mentions either. The page has six assigned files, FAISS retrieval has no per-file quota, and orchestrator code (mostly delegation and small helpers) loses to outline/validation code in chunk-level scoring. The result is a page that reads like a `page_outline.py` walkthrough.

### P3 — Sibling pages bleed content

`质量校验与修订` contains a section "大纲校验与生成优化" that documents `validate_outline`, which is on the sibling page `内容生成引擎`. The cause is mechanical: `page_outline.py` is in *both* pages' `files` lists, both pages retrieve overlapping chunks, and the outline prompt does not tell either page what its sibling covers.

### P4 — Phase-2 file scorer is ASCII-only and goes blind on non-English titles

`worker/pipeline/wiki_planner.py:733-735`:

```python
def _tokenize(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3}
```

For `前端应用架构 / 介绍 Next.js 应用的路由结构与交互组件库`, this yields `{"next"}` — a single token. `_score_file_for_page` then ranks files almost entirely by dep-graph centrality and entity count, completely missing the page's topic. The Phase-2 LLM is presented with 25 candidates that have nothing to do with the frontend, and the resulting `files` list contains zero `web/` files. The same failure cascades through `Wiki 渲染组件`, `后端接口与服务`, and `实时通信机制`.

### Why PR #40 is not enough

PR #40 raises capacity (`top_k 12→30`, `max_files 200→500..800`, `max_edges 500→1500`, `entity cap 25→8·N`, `max_tokens 16k→32k`). It addresses *truncation* and *summary completeness*. It does not address ordering, ownership, scoping, or tokenization — the four levers that drive P1–P4.

### Non-goals

- Replacing FAISS or embedding-based search across the codebase. Layer C makes BM25 a viable alternative for the wiki path; chat / deep-research can keep FAISS.
- Re-architecting the planner outline (Phase 1). Phase 1 output is largely correct in the export under analysis.
- Multilingual content generation rules; this spec only addresses retrieval and routing.

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
| P4 already-fixed-elsewhere | `worker/fast_report_search.py:943-959` | The fast-report path already has a CJK-aware tokenizer with n-gram support, camel-case splitting, and path tokenization. The wiki planner does not import it. |

### 4.2 Why the ordering rule alone is insufficient

Even if Phase 2 sorted `files` by relevance, P2 would persist because the retrieval/entity stages have no per-file quota. Layer A therefore must combine A1 (sort) + A4 (per-file retrieval quota) + A5 (per-file entity quota); none of the three on its own delivers a working page for `内容生成引擎`.

### 4.3 Why outline-only fixes are insufficient

P3 cannot be solved by a smarter Phase 1 outline because the export shows the outline (titles + purposes + parents) is correct. The leakage is between Phase 2 (file selection) and Pass 2 (draft retrieval). The Layer A fixes are therefore distributed across both stages.

---

## 5. Solution Design

### 5.1 Design principles

1. **Reuse existing zero-embedding machinery.** `fast_report_index.py` (deterministic per-file index with imports, imported_by, call_sites, exception_touchpoints, external_deps, entity tokens) and `fast_report_search.py` (CJK-aware tokenizer, BM25-style scoring, adaptive graph expansion, slice extractor) already implement most of what Layers A and C need. Re-import; do not re-invent.
2. **Per-file fairness over per-query optimality.** Every assigned file should get a guaranteed minimum share of retrieval and entity budget; whatever is left after the floor is filled goes to the global ranker.
3. **Explicit scope boundaries.** Sibling page titles and out-of-scope topics are first-class prompt inputs, not hints buried in a system message.
4. **Feature flags everywhere.** `AUTOWIKI_RETRIEVAL=keyword|hybrid|faiss`, `AUTOWIKI_DRAFT_MODE=section|full`, plus per-fix flags in `LLMConfig` for fast rollback.
5. **Layer C is a port, not a rewrite.** The Skill version of the page generator must be diff-able against the in-tree version.

### 5.2 Layer A — Planner & prompt patches

| ID | File(s) | Change |
|---|---|---|
| **A1** | `worker/pipeline/wiki_planner.py` | Add an `ordered_by_relevance` description to `_SELECTION_SCHEMA.files`. Update `_SYSTEM` and `_build_selection_user` to require most-representative-first ordering. In `_validate_selections`, score each page's `files` with `_score_file_for_page`; if the LLM ordering correlates negatively with the score, re-sort. Apply the same sort to `_heuristic_select_files` and `_directory_cluster_assign`. |
| **A2** | `worker/pipeline/page_outline.py:224-254`; `page_generator.py:266-274` | Extend `generate_page_outline` with `sibling_titles: list[str] \| None` and `out_of_scope_topics: list[str] \| None`. Inject them into the cacheable prefix as `Sibling pages (DO NOT cover their topics; reference by name only): ...` and `Out-of-scope (covered elsewhere): ...`. `generate_page_batch` computes siblings from `WikiPlan` (same `parent`) and derives `out_of_scope_topics` from sibling `purpose` first sentences. |
| **A3** | `worker/pipeline/page_draft.py:27` (`DRAFT_SYSTEM`) | Append a "Scope discipline" rule: stay within assigned files; if a topic is owned by a sibling listed in the prompt, give it ≤ 1 sentence and refer to the sibling by title; do not re-document validation, diagram post-processing, chunking, etc. that are explicitly assigned elsewhere. |
| **A4** | `worker/pipeline/page_generator.py:224-249` | Replace the single retrieval query with five queries: `spec.title`, `spec.purpose`, `" ".join(en_keywords or _derive_keywords(spec))`, top-5 entity names, file stems. After `multi_search`, run `_balance_chunks(chunks, files=spec.files, k=top_k, floor_per_file=2)` so each assigned file is guaranteed at least two chunks before any global tail is added. |
| **A5** | `worker/pipeline/page_formatters.py:41` | Refactor `_format_entity_details` to accept `files: list[str]` and `max_entities`. Compute `per_file = max(3, max_entities // len(files))`, take per-file slices first (sorted by importance), then fill the remainder globally. Plumb `files=spec.files` from both call sites. |
| **A6** | `worker/pipeline/page_outline.py:_build_outline_prompt` | New optional argument `signature_slices: dict[file, list[str]]`. For each `spec.file`, take 1–2 of the highest-importance entities and emit a `Signature slices` block (file:line range + first 4 lines of the entity body) into the cacheable prefix. The slices come from `FileAnalysis` plus a clone-root read. |
| **A7** | `worker/pipeline/wiki_planner.py:733-735` | Replace the ASCII-only `_tokenize` with `from worker.fast_report_search import _tokenize as _cjk_tokenize`. Audit other call sites (`_score_file_for_page`, `_best_matching_page`, `_directory_cluster_assign`) and migrate them to the same tokenizer. |
| **A8** | `worker/pipeline/wiki_planner.py:_select_files` | After validation, run `_enforce_ownership(selections, outline, dep_graph, file_infos)`: count per-file occurrences; for any file owned by ≥ 2 pages, score each owner with `_score_file_for_page` and keep only the highest-scoring owner among siblings (pages sharing a `parent`); allow at most 2 non-sibling owners (architectural hubs); otherwise demote. Cap total assignments at `1.5 × len(all_repo_files)`. |
| **A9** | `worker/pipeline/wiki_planner.py:_select_files`, `_validate_selections` | Replace the static 5–8 / hard-cap-10 rule with `n_target = clamp(2, ceil(median_score / score_threshold), 8)` computed from the prefilter score distribution. Narrow topics get 2–3 files; broad topics keep 5–8. Hard upper bound stays at 10. |

**Combined effect on the export under analysis:**

- `依赖图谱构建` → `[dependency_graph.py, wiki_planner.py]` (A1 + A8 demotes wiki_planner to a hub citation)
- `内容生成引擎` → `[page_generator.py, page_outline.py]` (A8 removes `fact_check.py` / `test_page_outline.py` / `prompt_segment.py`; A1 promotes the orchestrator; A4/A5 give it dedicated chunk and entity quota)
- `质量校验与修订` → `[fact_check.py, test_fact_check.py]` (A8 removes `page_outline.py` co-ownership)
- `Mermaid 图表优化` → `[mermaid.py, diagram_post_processor.py, test_mermaid_sanitize.py]`
- `前端应用架构` → web/app and web/components files (A7 fixes prefilter scoring)
- `后端接口与服务` → `api/main.py`, `api/routers/*.py`, `api/queue.py` (A7)

### 5.3 Layer B — Section-level drafting + BM25 retrieval

#### B1. Deterministic keyword index

Add `worker/pipeline/keyword_index.py`:

```python
@dataclass
class KeywordIndex:
    chunks: list[Chunk]                    # same Chunk model as FAISSStore
    bm25: BM25Okapi                        # rank_bm25 — pure Python, no native deps
    file_to_chunks: dict[str, list[int]]
    token_idf: dict[str, float]            # reuses tokens from fast_report_index

    @classmethod
    def build(cls, chunks, *, fast_report_index): ...

    def search(
        self,
        queries: list[str],
        *,
        k: int,
        files: list[str] | None = None,
        per_file_quota: int = 2,
    ) -> list[Chunk]: ...

    def hybrid_search(
        self,
        queries: list[str],
        *,
        k: int,
        vec_store: FAISSStore | None = None,
        alpha: float = 0.5,
    ) -> list[Chunk]: ...
```

Why BM25 (`rank_bm25`) instead of LlamaIndex: AutoWiki already owns its chunking, planner schema, and retrieval surface. `rank_bm25` plus `fast_report_search._tokenize` (CJK n-grams, camel case, path splitting) lands a working multilingual retriever in roughly 50 lines with no native dependencies, and stays inside the Skill sandbox. If LlamaIndex is preferred later, wrap its `BM25Retriever` + `QueryFusionRetriever` behind `KeywordIndex.search` rather than letting the abstraction leak into the pipeline.

#### B2. Section-level drafting

Replace the single-shot Pass 2 with:

```
Pass 1   Outline                        — fast_llm, unchanged
Pass 2a  Skeleton (NEW)                 — fast_llm; emits H1 + section headings only
Pass 2b  Section drafting (NEW, parallel) — for each SectionPlan:
         retrieval = section.heading + section.focus + entity names
         scope     = spec.files ∩ (section.diagram.source_files ∪ retrieval-derived)
         budget    = 5–10 chunks, 250–600 words
Pass 2c  Stitch (NEW)                   — fast_llm; concatenates sections, adds
                                          transition sentences, no factual rewrite
Pass 3   Fact-check                     — unchanged
Pass 4   Targeted revision              — unchanged
```

Add `worker/pipeline/page_section_drafter.py`. Keep a `legacy_full_draft` entry point in `page_draft.py` so `AUTOWIKI_DRAFT_MODE=full` rolls back instantly.

Section-level drafting is what closes P2 at the root: each section gets independent retrieval scoped to its source files, so the orchestrator section retrieves orchestrator chunks regardless of how dense the outline file is.

#### B3. `out_of_scope_claims` in outline schema

Extend `_OUTLINE_SCHEMA` with `out_of_scope_claims: array<string>`. The fact-check prompt receives this list; if a draft contains a claim matching any out-of-scope phrase, fact-check returns `verdict="fail"` and Pass 4 strips the offending sentences before LLM revision.

### 5.4 Layer C — Wiki-as-Skill

#### C1. Skill package layout

```
skills/autowiki-page/
  SKILL.md                          # frontmatter + how-to
  references/
    prompt_outline.md               # Pass 1 system prompt (no embedding refs)
    prompt_section.md               # Pass 2b system prompt
    prompt_factcheck.md             # Pass 3 system prompt
  src/
    keyword_index.py                # = Layer B's KeywordIndex
    page_section_drafter.py         # = Layer B's drafter
    fast_report_index_loader.py     # wraps worker/pipeline/fast_report_index.py
    cli.py                          # python -m skill.cli generate <page_slug>
  scripts/
    build_index.sh                  # invokes worker.cli to reuse ingest+ast+dep
```

#### C2. Public contract

```python
def generate_wiki_page(
    *,
    page_spec: dict,                  # title, purpose, files, parent, siblings, out_of_scope
    fast_report_index: dict,          # ast/fast_report_index.json
    clone_root: Path,                 # cloned source root
    llm: LLMProvider,                 # main model
    fast_llm: LLMProvider,            # outline / fact-check / stitch
    wiki_language: Literal["en", "zh"] = "en",
) -> PageResult: ...
```

`fast_report_index` already carries every signal the Skill needs: `directory_tree`, `hub_modules`, `readme_sections`, and per-file `imports / imported_by / entities / call_sites / exception_touchpoints / external_deps`. No FAISS, no embedding provider.

#### C3. Per-section retrieval

```python
def retrieve_for_section(section, *, page_files, index, clone_root, profile):
    # 1) seed = page_files ∩ section.diagram.source_files
    # 2) if seeds insufficient, expand via BM25 over page_files
    # 3) one-hop expansion through imports / imported_by / call_sites,
    #    bounded by token_budget
    # 4) extract entity-level slices via fast_report_slices.extract_source_slice
    return CodeEvidence(snippets=..., citations=..., evidence_blocks=...)
```

This re-uses `worker/fast_report_search.py:_expand_candidate_paths`, `_build_slice_candidates`, and `_apply_token_budget` directly. A new `expansion_graph_for("wiki_page")` profile supplies the wiki-tuned defaults.

#### C4. Rich code context in section prompts

Each section prompt receives, in order:

```
## Directory tree (focused)
worker/pipeline/
  page_generator.py         ← page_files
  page_outline.py           ← page_files
  fact_check.py             ← page_files
  page_draft.py             ← imported_by page_generator (1-hop)
...

## Hub modules
- worker/pipeline/page_generator.py  in_degree=4
  "Stage 6 of the generation pipeline..."

## Call chain (for section "Multi-pass orchestration")
generate_page → generate_page_outline (page_outline.py:257)
generate_page → generate_draft       (page_draft.py:266)
generate_page → run_fact_check       (fact_check.py:149)
generate_page → run_targeted_revision (fact_check.py:323)

## Code slices (file:line, full source)
[code-1-0] worker/pipeline/page_generator.py:190-360
   async def generate_page(spec, store, llm, fast_llm, ...): ...
[code-2-0] worker/pipeline/page_generator.py:130-163
   def compute_generation_order(plan) -> list[list[WikiPageSpec]]: ...
```

The "Call chain" block is sourced from `fast_report_index.json`'s `call_sites` — a signal the wiki path currently does not consume. Layer C requires the wiki pipeline to consume it, both inside the Skill and in-tree.

#### C5. Migration path

1. Keep the FAISS path as default; add `AUTOWIKI_RETRIEVAL=keyword|hybrid|faiss`.
2. `page_generator.generate_page_batch` selects retriever from env: `FAISSStore` ↔ `KeywordIndex`.
3. `skills/autowiki-page` calls the same `page_section_drafter`, but holds only `KeywordIndex`.
4. Phase 6 ("Hybrid search") naturally becomes the `hybrid` option once both retrievers ship.

---

## 6. Implementation Plan

### 6.1 Phasing & exit criteria

| Phase | PR(s) | LoC | Exit criteria |
|---|---|---|---|
| **A** | `feat/wiki-quality-layer-a` | ~350 | All A1–A9 patches merged; new tests in `tests/worker/test_wiki_planner.py` (ordering, ownership, CJK tokenization, adaptive cap) and `tests/worker/test_page_generator.py` (per-file chunk floor) pass; smoke-index against AutoWiki produces the file-list expectations in §5.2 for all eight defective pages. |
| **B1** | `feat/keyword-index` | ~300 | `KeywordIndex.search` matches FAISS top-k recall ±10% on a 100-question fixture; `rank_bm25` added to `pyproject.toml`. |
| **B2** | `feat/section-drafting` | ~300 | `AUTOWIKI_DRAFT_MODE=section` produces pages with section-level fact-check pass rate ≥ current full-page rate; legacy mode reachable via env. |
| **C** | `feat/wiki-skill` | ~1000 | `python -m skills.autowiki-page.cli generate <slug>` produces a page diff-equivalent (within fact-check verdict parity) to Phase B's output for the AutoWiki self-index, running with no embedding provider configured. |

PR #40 lands first; every layer assumes its budgets.

### 6.2 Test plan

Each layer ships with deterministic tests against `tests/fixtures/simple-repo/` plus a self-index regression that asserts the file-list expectations in §5.2.

- **Layer A unit tests:**
  - `test_select_files_orders_by_relevance` — Phase 2 returns `dependency_graph.py` first for a Chinese-titled "依赖图谱" page.
  - `test_enforce_ownership_demotes_sibling_share` — `page_outline.py` cannot be assigned to two sibling pages.
  - `test_cjk_tokenize_prefilter` — `_score_file_for_page` ranks `web/components/*.tsx` highest for `前端应用架构`.
  - `test_balance_chunks_floor` — every `spec.file` receives ≥ 2 chunks when `k ≥ 2·len(files)`.
  - `test_outline_prompt_includes_siblings` — sibling titles + out-of-scope topics appear in the cacheable prefix.
  - `test_draft_system_prompt_scope_rule` — the new "Scope discipline" rule is present.

- **Layer B integration tests:**
  - `test_keyword_index_recall_parity` — BM25 vs FAISS recall on a fixture question set.
  - `test_section_drafter_independent_retrieval` — orchestrator section receives orchestrator chunks even when outline file dominates the page.
  - `test_out_of_scope_claims_trigger_factcheck_fail`.

- **Layer C smoke tests:**
  - `test_skill_cli_generates_without_faiss` — runs the Skill CLI in a temp dir with no FAISS provider configured.
  - `test_skill_consumes_call_chain` — section prompts contain a `Call chain` block sourced from `call_sites`.

- **Self-index regression:** a CI job runs `autowiki index . --reuse-index=false` against the AutoWiki repo and asserts that the eight pages listed in §5.2 contain the expected primary file as their first `files[0]`.

### 6.3 Roll-back & feature flags

- `AUTOWIKI_RETRIEVAL=keyword|hybrid|faiss` (default `faiss` until Layer B lands; flips to `hybrid` after).
- `AUTOWIKI_DRAFT_MODE=section|full` (default `full` until Layer B exits; flips to `section` after).
- `AUTOWIKI_PLANNER_OWNERSHIP=enforce|advise|off` for A8 (default `advise` for one release, then `enforce`).
- `AUTOWIKI_PHASE2_TOKENIZER=cjk|ascii` for A7 (default `cjk` immediately; `ascii` retained for one release as escape hatch).

### 6.4 Risks & mitigations

| Risk | Mitigation |
|---|---|
| BM25 underperforms FAISS on natural-language queries (chat / deep-research) | Layer B keeps FAISS for non-wiki paths; `hybrid_search` interpolates when both are available. |
| Section drafting balloons LLM call count | Section pass uses `fast_llm`; `AUTOWIKI_PAGE_CONCURRENCY` already bounds parallelism; per-section context is much smaller, so wall-clock time stays comparable to single-shot. |
| Ownership enforcement starves "hub" files (e.g. `models.py`) | A8 explicitly allows up to 2 non-sibling owners and exempts top-decile in-degree files via the existing `_compute_hub_modules`. |
| CJK tokenizer change ripples into `_directory_cluster_assign` and changes existing assignments unexpectedly | Tests pin the new behavior against the AutoWiki self-index expectations; the `AUTOWIKI_PHASE2_TOKENIZER` flag provides instant rollback. |
| Skill drift from in-tree pipeline | Layer C ports modules verbatim; CI runs the same fixture through both entry points and diffs the rendered Markdown. |

### 6.5 Telemetry

Use the existing `pipeline_logging.log_validation_retry` / `log_final_failure` channels. Add structured events at:

- `wiki_planner.ownership_demotion` — file, demoted_page, primary_page, score_delta.
- `page_generator.balance_chunks` — page, files, allocated_per_file, leftover.
- `page_section_drafter.section_factcheck` — page, section, verdict, issue_count.

These let us measure whether Layer A's ordering and ownership rules actually fire on real repositories rather than guessing from log spelunking.

---

## 7. Acceptance Criteria

Layer A ships when, on a fresh AutoWiki self-index:

1. The eight pages enumerated in §5.2 carry the file lists predicted there.
2. `质量校验与修订.md` contains no section whose primary subject is `validate_outline` or `PageOutline`.
3. `内容生成引擎.md` contains at least one of the symbols `generate_page`, `generate_page_batch`, or `compute_generation_order`.
4. `前端应用架构.md` contains at least three references to files under `web/`.
5. No source file is assigned to more than two pages, except files in the top-decile by `dep_graph` in-degree.

Layer B ships when, in addition:

6. Section-level fact-check pass rate is ≥ the previous full-page fact-check pass rate, measured over the AutoWiki self-index plus three external fixture repos.

Layer C ships when, in addition:

7. `python -m skills.autowiki-page.cli generate <slug>` running with no embedding provider produces output that is identical (modulo whitespace) to the in-tree Layer B output for the same slug, on the AutoWiki self-index.

---

## 8. Open Questions

- Should A8 ownership enforcement run before or after `_validate_selections`? Running before risks rejecting an LLM result that would otherwise satisfy the schema; running after risks re-opening validation. The proposal is "after, with one extra retry budget."
- Do we want section drafts to retain their own `repo_notes` block, or let the stitch pass inject notes once at the top? Proposed: stitch-only, to keep section prompts focused on code.
- Layer C section prompts vs. Layer B section prompts — keep them in lockstep, or fork once Skill-specific constraints (e.g., file-system-only) demand it? Proposed: keep in lockstep until a concrete divergence appears.
