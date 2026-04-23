# AGENTS.md

This file provides guidance to Codex and other coding agents when working with code in this repository.

## Project Status

AutoWiki **Phases 1, 2, 2.5, 3, 4, 4.5, 4.6, and 5 are complete**. Phase 1 tagged `v0.1.0-phase1`; Phase 2 (chat, diagrams, incremental refresh) merged via PR #4; Phase 2.5 (wiki quality enhancements) merged across PRs #15 and #17; Phase 3 (Deep Research) and Phase 4 (User Steering) merged via PR #20; Phase 4.5 (planner robustness hardening: Layer C1, validate-plan, feedback retries, Mermaid fixes, Docker startup fixes) merged via PR #22; Phase 4.6 (page-centric file selection) merged via PR #23; Phase 5 (GitLab/Bitbucket multi-platform support + private repos + homepage search) merged via PR #30.

## What AutoWiki Is

A self-hosted, open-source AI-powered wiki generator for software repositories. Given a supported GitHub, GitLab, or Bitbucket repository URL, it generates a browsable wiki with architecture overviews, module breakdowns, source-linked documentation, and a conversational Q&A interface — running locally with user-supplied API keys.

## Architecture

### Service Topology
```text
User (Browser / CLI)
    ↓
API Gateway (FastAPI)  ←→  Redis
    ↓
Worker Service (ARQ job queue)
    ↓
Storage (SQLite + FAISS + Markdown files at ~/.autowiki/)
```

### Core Components
- **API Gateway** (`api/`) — FastAPI, REST + WebSocket endpoints, job enqueuing via ARQ
- **Worker Service** (`worker/`) — ARQ background jobs, 6-stage generation pipeline
- **Frontend** (`web/`) — Next.js 16.2.1 + TypeScript + Tailwind v4 + shadcn/ui, stateless SPA
- **Storage** — SQLite for metadata, FAISS for vector index, Markdown files for wiki pages

### Generation Pipeline (6 Stages)
1. **Repo Ingestion** (`worker/pipeline/ingestion.py`) — shallow clone, file filtering, commit SHA
2. **AST Analysis** (`worker/pipeline/ast_analysis.py`) — single-pass Tree-Sitter entity extraction → `FileAnalysis`
3. **Dependency Graph** (`worker/pipeline/dependency_graph.py`) — file-level import graph + BFS-split clusters
4. **RAG Indexer** (`worker/pipeline/rag_indexer.py`) — LangChain chunking, FAISS IndexFlatIP (skippable with `reuse_index=True`); `doc_k` param downweights pure documentation files
5. **Wiki Planner** (`worker/pipeline/wiki_planner.py`) — two-phase LLM plan: Phase 1 outline (hierarchy/titles/purposes) + Phase 2 file assignment; each phase validates and self-retries → `WikiPlan`
6. **Page Generator** (`worker/pipeline/page_generator.py`) — bottom-up 4-pass orchestrator: Pass 1 outline (fast model), Pass 2 draft (main model), Pass 3 fact-check (fast model), Pass 4 targeted revision (main model, conditional); post-processing applies diagram headers and Mermaid sanitization

Supported AST languages: Python, JavaScript/JSX, TypeScript/TSX, Java, Go, Rust, C, C++, C#

### Data Storage Layout
```text
~/.autowiki/
  autowiki.db               ← SQLite (repos, jobs, wiki_pages)
  repos/{repo_hash}/
    clone/                  ← shallow git clone
    faiss.index             ← vector index
    faiss.meta.pkl          ← chunk metadata
    ast/
      wiki_plan.json        ← internal wiki plan with file mappings (for refresh)
    wiki/
      wiki.json             ← generated user-facing wiki structure/API output
      *.md                  ← generated Markdown pages
  logs/
```

The generated `~/.autowiki/repos/{repo_hash}/wiki/wiki.json` is distinct from
the user-authored `.autowiki/wiki.json` inside a cloned repository. Phase 4
steering reads the user-authored file, and its `modules` schema applies only to
that steering config, not to the generated wiki output.

### Key SQLite Tables
- `repositories` — repo metadata, status, last-indexed commit SHA
- `jobs` — indexing job tracking with 0–100 progress
- `wiki_pages` — hierarchical page structure with slugs and parent refs

## Configuration

Config discovery order (highest to lowest precedence):
1. Environment variables (`ANTHROPIC_API_KEY`, `AUTOWIKI_LLM_PROVIDER`, etc.)
2. `autowiki.yml` in current working directory
3. `~/.autowiki/autowiki.yml`
4. Built-in defaults

Default LLM: `claude-sonnet-4-6`. Supported providers: `anthropic`, `openai`, `openai-compatible`, `ollama`, `google`.

## Key Implementation Notes

- **pydantic-settings v2**: sub-model env_prefix isolation — no `env_nested_delimiter` on parent `Config`
- **SQLAlchemy 2.0 async** with aiosqlite; use `datetime.now(timezone.utc)` not `datetime.utcnow()`
- **Tree-Sitter ≥0.23 API**: `Language(tspython.language())` + `Parser(lang)` constructor style
- **Next.js 16.2.1**: Tailwind v4 (CSS-only, no `tailwind.config.ts`), `@base-ui/react` not `@radix-ui/react`
- **Gemini providers**: `google-generativeai` is deprecated; both files have Phase 2 migration notes for `google-genai`
- **ARQ worker**: blocking I/O must use `run_in_executor`; `clone_or_fetch` already wrapped
- **Wiki plan**: two-phase LLM process — Phase 1 generates outline (hierarchy/titles/purposes), Phase 2 selects 5–8 representative source files per page; each phase validates and self-retries immediately; slugs derived from titles, not stored in wiki.json
- **wiki.json format**: user-facing (title/purpose/parent/page_notes); `ast/wiki_plan.json` is internal (includes files); `Repository.wiki_structure` is API-compatible (includes derived slugs/parent_slugs for frontend)
- **FileAnalysis**: single-pass AST analysis — `analyze_all_files()` replaces both `build_enhanced_module_tree()` and `_build_file_entities()`
- **`to_llm_summary(max_files=200)`**: default 200 keeps prompts bounded; pass 0 to opt in to the 800-file safety cap; when capped, `_rank_files_by_importance()` selects the most architecturally significant files (entity count, in-degree, entry-point bonus, shallowness)
- **`reuse_index`**: `IndexRequest.reuse_index` (API) / `--reuse-index` (CLI) skips Stage 4 (FAISS rebuild) and loads the existing index instead; threaded through `enqueue_full_index` → `run_full_index`
- **`generate_batch` + bottom-up generation**: `LLMProvider.generate_batch()` runs prompts concurrently (semaphore-controlled); `compute_generation_order()` returns pages deepest-first so parents always receive finished child Markdown
- **Multi-pass page generation**: each page goes through 4 passes — Pass 1 outline (`fast_llm`), Pass 2 full draft (`llm`), Pass 3 fact-check (`fast_llm`), Pass 4 targeted revision (`llm`, only when fact-check verdict is `"fail"`); deterministic fallback strips still-flagged claims/diagrams
- **`PromptSegment`**: typed dataclass wrapping prompt parts with optional `cache_control`; all LLM providers translate `list[PromptSegment]` → provider-native format; enables Anthropic prompt caching for long system prompts
- **`fast_model` / `fast_llm`**: `LLMConfig.fast_model` configured via `AUTOWIKI_LLM_FAST_MODEL` (defaults to main model when empty); `make_fast_llm_provider()` returns main provider unchanged when models match; used for Pass 1 outline and Pass 3 fact-check to cut latency and cost; threaded through jobs → planner Phase 2 → page generator
- **`cache_ttl`**: `LLMConfig.cache_ttl` hints cache lifetime for providers that support it; Anthropic uses `"ephemeral"` cache control on system segments
- **Pipeline observability**: every retry loop over LLM structured-output calls in `worker/pipeline/` must log validation failures via `pipeline_logging.log_validation_retry` and log final fallback invocations via `pipeline_logging.log_final_failure`. Silent `except (ValueError, json.JSONDecodeError, KeyError): pass` is a bug. See `worker/pipeline/pipeline_logging.py`.
- **Planner page-centric file selection**: Phase 2 (`_select_files`) selects 5–8 representative files per page (max 10) rather than assigning every file to one page. `_prefilter_candidates` scores all repo files per page and passes the top 25 as candidates to the LLM. Orphan enforcement removed — unselected files are intentionally omitted.
- **Planner fallback semantics**: when `_select_files` exhausts retries, `_heuristic_select_files` preserves valid pages from the partial LLM result and fills the remainder via scoring; `_directory_cluster_assign` is retained as a last resort.
- **Planner batched selection**: `_select_files_in_batches` processes pages in batches of 12, reusing a cacheable system segment (outline + file summary + dep info) across batches for Anthropic prompt caching.
- **`WikiPlan.all_repo_files`**: persisted in `ast/wiki_plan.json`; incremental refresh reads this to detect added/removed files (each page only carries 5–10 files, so the per-page union is too small to derive the full file list).
- **Outline anchors (Layer C1)**: worker/pipeline/outline_anchors.py synthesises a directory tree, package-entry docstrings, and README headings, injected into the Phase-1 outline prompt to reduce cross-page fragmentation.
- **Mermaid sanitization** (`worker/utils/mermaid.py`): `sanitize_mermaid` quotes node/edge labels containing `(){}|<>/`, handles compound shapes, strips code fences, and removes orphaned `end` keywords (an `end` with no matching `subgraph` opening) that LLMs sometimes emit when using a node definition instead of a proper `subgraph … end` block.

## API Surface

### REST/WebSocket (Phase 1 + Phase 2 + Phase 3 + Phase 5)
```http
POST  /api/repos                              # Submit repo for indexing
GET   /api/repos                             # List all repos
GET   /api/repos/{repo_id}                   # Status + metadata
POST  /api/repos/{repo_id}/refresh           # Trigger incremental refresh
GET   /api/repos/{repo_id}/wiki              # List wiki pages
GET   /api/repos/{repo_id}/wiki/{slug}       # Get page Markdown
POST  /api/repos/{repo_id}/chat              # Create a new chat session
GET   /api/repos/{repo_id}/chat/{session_id} # Get chat history
POST  /api/repos/{repo_id}/research          # Start deep research → {job_id, report_id}
GET   /api/repos/{repo_id}/research/{job_id} # Get research report (plan, findings, Markdown)
GET   /api/jobs/{job_id}                     # Job status + progress
WS    /ws/jobs/{job_id}                      # Stream job progress
WS    /ws/repos/{repo_id}/chat/{session_id}  # Stream chat responses
WS    /ws/repos/{repo_id}/research/{job_id}  # Stream research events
GET   /api/settings/tokens                   # List PAT storage status (masked)
PUT   /api/settings/tokens/{platform}        # Store/update PAT for private repos
DELETE /api/settings/tokens/{platform}       # Delete stored PAT
```

### CLI (Phase 1 + Phase 3)
```bash
autowiki index github.com/owner/repo [--reuse-index]
autowiki list
autowiki serve [--port 3000] [--debug]
autowiki research github.com/owner/repo "<question>"
autowiki validate-plan <repo>       # Offline planner diagnostic — reads ast/wiki_plan.json and reports coverage, page-size distribution, locality scores, and validation status
autowiki config show
autowiki config set <key> <value>
```

### Research API (Phase 3)
```http
POST  /api/repos/{repo_id}/research                   # Start deep research → {job_id, report_id}
GET   /api/repos/{repo_id}/research/{job_id}          # Get report (plan, findings, Markdown)
WS    /ws/repos/{repo_id}/research/{job_id}           # Stream research events
```

## Workflow Rules

- **New Conversations (Required)**: When starting a new task or conversation, suggest using git worktree to create an isolated environment for implementation. This keeps the main workspace clean and ensures changes are isolated until ready for integration.
- **Pre-Commit Checks (Required)**: Before every commit, run and resolve all issues from:
  ```bash
  uv run ruff check .
  uv run ruff format --check .
  npm run lint          # run from web/
  ```
  All lint errors and format violations **must be fixed** before committing. Do not commit with outstanding `ruff` errors or `npm run lint` errors.
- **PR Review Workflow (Required)**: After fixing issues raised in a pull request review, commit and push the fixes first. Then reply to each inline review comment thread describing what was fixed and include the corresponding commit id. Mark each thread as resolved via the GraphQL API (since REST has no resolve endpoint). Use the node IDs from the review comments.

## Testing

- **Framework**: pytest with `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed)
- **Coverage target**: ≥80% on `worker/` and `api/` — currently at 80%
- **Run**: `pytest tests/ --ignore=tests/e2e` AND `npm test --prefix web`
- **Fixtures**: `mock_llm`, `mock_embedding` in `tests/conftest.py`; fixture repo at `tests/fixtures/simple-repo/`

## Deployment

```bash
docker-compose up          # starts api, worker, web, redis
```

Non-Docker: `autowiki serve` spawns API + worker + Next.js as subprocesses.

## Phased Delivery

- **Phase 1** ✅ — Core pipeline (index + static wiki + REST API + web UI + CLI)
- **Phase 2** ✅ — Incremental refresh + Q&A chat + dependency diagrams (merged PR #4)
- **Phase 2.5** ✅ — Wiki quality enhancements: two-phase planner with per-phase validation, bottom-up child-synthesis generation, 4-pass page orchestrator, prompt caching, fast model, RAG doc_k tuning, diagram post-processing (PRs #15 and #17)
- **Phase 3** ✅ — Deep Research mode: multi-step RAG investigation, LLM planner, per-step AST context, synthesized report; `autowiki research` CLI; REST + WebSocket API (PR #20)
- **Phase 4** ✅ — User-steered wiki structure via `.autowiki/wiki.json`: override page hierarchy, assign modules to pages, inject repo/page notes into generation (PR #20)
- **Phase 4.5** ✅ — Planner robustness hardening (PR #22): architectural anchors in Phase-1 outline prompt (Layer C1), `autowiki validate-plan` offline diagnostic CLI, feedback-retry loop in `_assign_files`, various bug fixes (Gemini JSON, Mermaid, Docker)
- **Phase 4.6** ✅ — Page-centric file selection (PR #23): Phase 2 replaced from file-centric assignment to page-centric selection (5–8 files per page); scoring-based pre-filter + fallback (`_score_file_for_page`, `_heuristic_select_files`); `WikiPlan.all_repo_files` for correct refresh coverage; orphan enforcement removed
- **Phase 5** ✅ — GitLab/Bitbucket support (public + private repos, full API metadata) + homepage project search (PR #30)
- **Phase 6** — Hybrid search (keyword + semantic BM25/FAISS fusion)
