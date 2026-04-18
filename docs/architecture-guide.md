# AutoWiki Architecture Guide

A step-by-step reading guide for architects and developers who want to understand
the project's design decisions, trace the implementation flow, and locate key
information quickly.

---

## 1. What AutoWiki Is

A self-hosted, open-source AI-powered wiki generator for software repositories.
Given a GitHub URL, it produces a browsable, multi-level wiki — architecture
overviews, module breakdowns, dependency diagrams, source-linked documentation,
and conversational Q&A — running locally with user-supplied API keys.

The three headline design goals that shape every architecture decision:

1. **Accuracy** — Tree-Sitter AST + dependency graphs + multi-pass LLM generation
   with a fact-check pass beats RAG-only or AST-only approaches.
2. **Async generation** — jobs are enqueued and run in a background worker;
   the API never blocks on LLM calls.
3. **Incremental refresh** — only pages whose source files changed need to be
   regenerated.

---

## 2. Where to Start: Recommended Reading Order

| Step | Document | What it establishes |
|------|----------|---------------------|
| 1 | `docs/superpowers/specs/2026-03-22-autowiki-design.md` | Product goals, competitive context, phased delivery roadmap, original API surface, storage layout |
| 2 | `CLAUDE.md` (project root) | Authoritative current architecture: service topology, 6-stage pipeline, storage layout, all key implementation notes |
| 3 | `docs/superpowers/specs/2026-04-08-wiki-planner-improvements-design.md` | Two-phase planner, richer file summaries, bottom-up generation, `generate_batch` — the Phase 2.5 planner redesign |
| 4 | `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md` | 4-pass page generation (outline → draft → fact-check → revision), `PromptSegment` caching, fast-model split, `doc_k` retrieval downweighting |

For implementation details of each phase, follow with the corresponding plan doc
(see §4 below).

> **Quick orientation**: If you only read two documents, read `CLAUDE.md` for the
> current state and `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md`
> for the most recent and most detailed design rationale.

---

## 3. System Architecture at a Glance

```text
User (Browser / CLI / MCP)
    ↓
API Gateway (FastAPI)  ←→  Redis
    ↓
Worker Service (ARQ job queue)
    ↓
Storage (~/.autowiki/): SQLite + FAISS + Markdown files
```

### 6-Stage Generation Pipeline

```text
Stage 1  Ingestion          shallow clone, file filtering, commit SHA
Stage 2  AST Analysis       single-pass Tree-Sitter → FileAnalysis (entities, counts)
Stage 3  Dependency Graph   file-level import edges → connected-component clusters
Stage 4  RAG Indexer        LangChain chunking → FAISS IndexFlatIP; skippable with --reuse-index
Stage 5  Wiki Planner       two-phase LLM: Phase 1 outline (titles/hierarchy), Phase 2 file assignment
Stage 6  Page Generator     bottom-up, 4-pass per page: outline → draft → fact-check → revision
```

**What was Stage 7?** A diagram synthesis stage (`diagram_synthesis.py`) was
briefly added in Phase 2 and removed in Phase 2.5 — the page generator's prompt
already produces architecture Mermaid diagrams, making it redundant.

---

## 4. Document Map: Chronological Evolution

Each document below is annotated with its status and what it introduced or
superseded.

### Original Design

| Document | Status | Introduced |
|----------|--------|------------|
| `docs/superpowers/specs/2026-03-22-autowiki-design.md` | Approved (Phases 1–2 complete, 3–5 pending) | Full product PRD: goals, competitive analysis, API surface, phased delivery |
| `docs/superpowers/specs/2026-03-29-autowiki-frontend-design.md` | Complete | Visual design spec: three-column layout, light-mode palette, component inventory |

### Phase 1 — Core MVP

| Document | Status | Introduced |
|----------|--------|------------|
| `docs/superpowers/plans/2026-03-22-phase1-core-mvp.md` | Complete (stale: `build_module_tree`, 5-stage count) | Step-by-step implementation: Docker Compose, FastAPI, ARQ, SQLite, FAISS, Next.js UI |
| `docs/2026-03-25-improve-wiki-quality-plan.md` | Complete (superseded: `build_enhanced_module_tree` API) | Dependency graph, enhanced AST, architecture diagrams, entity context for planner |
| `docs/2026-03-25-improve-wiki-ux-plan.md` | Complete | `status_description` on jobs, hierarchical sidebar, Markdown CSS prose styles |
| `docs/2026-03-27-improve-llm-retry-plan.md` | Complete (stale: "5-stage" reference) | Exponential backoff retry, `async_retry`, `OnRetryCallback`, `TRANSIENT_EXCEPTIONS` |
| `docs/2026-03-27-improve-logging-plan.md` | Complete | `LoggingLLMProvider`, `--debug` flag, `error.log` / `task.log` / `llm.log` |
| `docs/2026-03-28-pipeline-refactoring-plan.md` | Complete (stale: Stage 7 section, some helper names) | `FileAnalysis` single-pass AST, `WikiPlan`, `wiki_plan.json`, removal of `module_tree.json` |
| `docs/2026-03-28-simplify-code-plan.md` | Complete (stale: helper names superseded by refactoring) | Decomposed `jobs.py` into stage helpers, deduplication |

### Phase 2 — Chat, Diagrams & Refresh

| Document | Status | Introduced |
|----------|--------|------------|
| `docs/superpowers/plans/2026-03-23-phase2-chat-diagrams-refresh.md` | Complete (stale: `diagram_synthesis.py`, `module_tree.json`, `get_affected_modules`) | Multi-turn chat, incremental refresh, `.autowikiignore`, dependency graph UI |
| `docs/superpowers/plans/2026-03-29-frontend-redesign.md` | Complete | Three-column wiki layout, ReactFlow dependency graph, ChatDrawer, RefreshButton |
| `docs/2026-04-03-wiki-generation-language-plan.md` | Complete (stale: Step 4–5 reference Stage 7) | `wiki_language` param (EN/ZH) threaded from frontend → API → worker → LLM prompts |

### Phase 2.5 — Wiki Quality Enhancements

| Document | Status | Introduced |
|----------|--------|------------|
| `docs/superpowers/specs/2026-04-08-wiki-planner-improvements-design.md` | Implemented, merged to main (see implementation notes at top) | Two-phase planner, `generate_batch`, richer file summaries, `_suggest_page_range`, bottom-up generation, per-phase validation |
| `docs/superpowers/plans/2026-04-08-wiki-planner-improvements.md` | Complete | Step-by-step task list for the above; implementation deviations documented at top |
| `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md` | Implemented, merged to main (PRs #15, #17) | 4-pass page generation, `PromptSegment` / prompt caching, `fast_model` split, `doc_k` downweighting, diagram header enforcement |
| `docs/superpowers/plans/2026-04-10-wiki-page-quality-redesign.md` | Complete | Step-by-step task list for the above |

### Phase 3 & 4 — Deep Research and User Steering

| Document | Status | Introduced |
|----------|--------|------------|
| `docs/superpowers/plans/2026-04-14-phase3&4-deep-research-and-steering.md` | Complete (PR #20) | Multi-step RAG research, LLM planner, synthesized report, `autowiki research` CLI, user-steering via `wiki.json` |

### Phase 4.5 — Planner Robustness Hardening

| Document | Status | Introduced |
|----------|--------|------------|
| `docs/2026-04-15-wiki-planner-robustness-investigation.md` | Complete | Investigation findings: outline fragmentation root causes, three candidate fixes |
| `docs/superpowers/plans/2026-04-15-wiki-planner-robustness.md` | Complete | Implementation plan for investigation-recommended fixes applied in pre-PR #22 commits |
| `docs/superpowers/plans/2026-04-16-deferred-wiki-planner-robustness.md` | **Complete (PR #22)** | Layer C1 outline anchors, Layer C2 `secondary_files` multi-page assignment, `autowiki validate-plan` offline harness |

---

## 5. Tracing a Feature End-to-End

### How a wiki page gets generated

1. **Request enters** — `POST /api/repos` in `api/routers/repos.py` enqueues a job
   via `api/queue.py` → Redis.

2. **Worker picks up** — `run_full_index()` in `worker/jobs.py` is the top-level
   orchestrator. Read it to understand stage sequencing and progress reporting.

3. **Stages 1–4** (`worker/pipeline/ingestion.py`, `ast_analysis.py`,
   `dependency_graph.py`, `rag_indexer.py`) build the evidence the planner needs.
   Key output: a `FileAnalysis` object and a `FAISSStore`.

4. **Stage 5 — Planner** (`worker/pipeline/wiki_planner.py`):
   - Phase 1: `_build_outline_prompt()` (with architectural anchors from `outline_anchors.py`) → LLM call → `_validate_outline_structure()`; self-retries with feedback up to `max_retries` times
   - Phase 2: `_assign_files_in_batches()` (40-file chunks, cacheable system prompt) → `_validate_assignments()`; self-retries with feedback; on final failure falls back to `_directory_cluster_assign()` (locality-preserving heuristic)
   - Result: a `WikiPlan` (list of `WikiPageSpec`, each with title, purpose, `files`, `secondary_files`, parent)
   - Offline diagnostics: `autowiki validate-plan <repo>` reads `ast/wiki_plan.json`
   - Design rationale: `docs/superpowers/specs/2026-04-08-wiki-planner-improvements-design.md` §5

5. **Stage 6 — Page Generator** (`worker/pipeline/page_generator.py`):
   - `compute_generation_order(plan)` returns pages deepest-first (leaves before parents)
   - For each depth level, `generate_page_batch()` runs pages concurrently
   - Each page goes through `generate_page()`:
     - Pass 1: `generate_page_outline()` via fast model (`worker/pipeline/page_outline.py`)
     - Pass 2: `generate_draft()` via main model (`worker/pipeline/page_draft.py`)
     - Pass 3: `run_fact_check()` via fast model (`worker/pipeline/fact_check.py`)
     - Pass 4: `run_targeted_revision()` via main model, only if fact-check verdict is `"fail"`
     - Post-processing: `ensure_diagram_headers()` (`worker/pipeline/diagram_post_processor.py`)
   - Design rationale: `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md` §5

6. **Storage** — pages written to `~/.autowiki/repos/{hash}/wiki/*.md` and to
   the `wiki_pages` SQLite table. `wiki_plan.json` saved to `ast/`.

### How parent pages differ from leaf pages

Parent pages (those with child pages in the hierarchy) are generated last — after
all their children. `generate_page()` receives `child_contents: list[PageResult]`
which are the already-fact-checked child Markdown strings. The parent's prompt
uses this child content as its primary evidence rather than raw RAG chunks.
See `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md` §6.

### How incremental refresh works

`run_refresh_index()` in `worker/jobs.py` loads the saved `wiki_plan.json`,
identifies files changed since the last commit SHA, and re-runs only the affected
pages through the Stage 6 pipeline. Unchanged pages are read from disk and can
supply `child_contents` for parent pages that do need regeneration.

---

## 6. Key Design Decisions — Where to Find the Rationale

| Decision | Where documented |
|----------|-----------------|
| Two-phase planner (outline then file assignment) | `docs/superpowers/specs/2026-04-08-wiki-planner-improvements-design.md` §5 |
| Bottom-up generation (children before parents) | `docs/superpowers/specs/2026-04-08-wiki-planner-improvements-design.md` §7 |
| 4-pass page generation | `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md` §3, §5 |
| `PromptSegment` and Anthropic prompt caching | `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md` §8 |
| `doc_k` downweighting stale design docs | `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md` §4 |
| `fast_model` / `fast_llm` split | `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md` §8.4 |
| Stage 7 removal (diagram synthesis) | `docs/superpowers/specs/2026-04-08-wiki-planner-improvements-design.md` implementation notes |
| `FileAnalysis` single-pass AST (replaced `build_module_tree`) | `docs/2026-03-28-pipeline-refactoring-plan.md` |
| `wiki_plan.json` vs old `module_tree.json` | `docs/2026-03-28-pipeline-refactoring-plan.md` |
| `reuse_index` / `--reuse-index` | `CLAUDE.md` implementation notes; `docs/superpowers/specs/2026-04-08-wiki-planner-improvements-design.md` implementation notes |
| Async retry / `OnRetryCallback` | `docs/2026-03-27-improve-llm-retry-plan.md` |
| Incremental refresh logic | `docs/superpowers/plans/2026-03-23-phase2-chat-diagrams-refresh.md` |

---

## 7. Still-Planned Work (Not Yet Implemented)

> **Note:** Deep Research mode (Phase 3) and user steering via `.autowiki/wiki.json` (Phase 4) are both **implemented** — shipped in PR #20. The items below are features still pending.

| Phase | Feature | Reference |
|-------|---------|-----------|
| Phase 5 | MCP server (`read_wiki_structure`, `read_wiki_page`, `search_wiki`, `ask_question`, `deep_research`) | `CLAUDE.md` API Surface |
| Phase 5 | GitLab / Bitbucket support | `docs/superpowers/specs/2026-03-22-autowiki-design.md` |
| Phase 5 | Hybrid search (BM25 + vector) | `docs/superpowers/specs/2026-03-22-autowiki-design.md` |
| Deferred | GitHub webhooks for auto-refresh | `docs/superpowers/specs/2026-03-22-autowiki-design.md` §9 |
| Stub | `cache_ttl: long` (1-hour Anthropic cache) | `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md` implementation notes |
