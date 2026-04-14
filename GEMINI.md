# GEMINI.md

This file provides guidance to GEMINI when working with code in this repository.

## Project Status

AutoWiki **Phase 1, Phase 2, and Phase 2.5 are complete**. Phase 1 tagged `v0.1.0-phase1`; Phase 2 (chat, diagrams, incremental refresh) merged via PR #4; Phase 2.5 (wiki quality enhancements) merged across PRs #15 and #17.

## What AutoWiki Is

A self-hosted, open-source AI-powered wiki generator for software repositories. Given a GitHub URL, it generates a browsable wiki with architecture overviews, module breakdowns, source-linked documentation, and a conversational Q&A interface — running locally with user-supplied API keys.

## Architecture

### Service Topology
```
User (Browser / CLI / MCP)
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
```
~/.autowiki/
  autowiki.db               ← SQLite (repos, jobs, wiki_pages)
  repos/{repo_hash}/
    clone/                  ← shallow git clone
    faiss.index             ← vector index
    faiss.meta.pkl          ← chunk metadata
    ast/
      wiki_plan.json        ← internal wiki plan with file mappings (for refresh)
    wiki/
      wiki.json             ← user-facing wiki structure (for Phase 4 steering)
      *.md                  ← generated Markdown pages
  logs/
```

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
- **Wiki plan**: two-phase LLM process — Phase 1 generates outline (hierarchy/titles/purposes), Phase 2 assigns files; each phase validates and self-retries immediately; slugs derived from titles, not stored in wiki.json
- **wiki.json format**: user-facing (title/purpose/parent/page_notes); `ast/wiki_plan.json` is internal (includes files); `Repository.wiki_structure` is API-compatible (includes derived slugs/parent_slugs for frontend)
- **FileAnalysis**: single-pass AST analysis — `analyze_all_files()` replaces both `build_enhanced_module_tree()` and `_build_file_entities()`
- **`to_llm_summary(max_files=200)`**: default 200 keeps prompts bounded; pass 0 to opt in to the 800-file safety cap; when capped, `_rank_files_by_importance()` selects the most architecturally significant files (entity count, in-degree, entry-point bonus, shallowness)
- **`reuse_index`**: `IndexRequest.reuse_index` (API) / `--reuse-index` (CLI) skips Stage 4 (FAISS rebuild) and loads the existing index instead; threaded through `enqueue_full_index` → `run_full_index`
- **`generate_batch` + bottom-up generation**: `LLMProvider.generate_batch()` runs prompts concurrently (semaphore-controlled); `compute_generation_order()` returns pages deepest-first so parents always receive finished child Markdown
- **Multi-pass page generation**: each page goes through 4 passes — Pass 1 outline (`fast_llm`), Pass 2 full draft (`llm`), Pass 3 fact-check (`fast_llm`), Pass 4 targeted revision (`llm`, only when fact-check verdict is `"fail"`); deterministic fallback strips still-flagged claims/diagrams
- **`PromptSegment`**: typed dataclass wrapping prompt parts with optional `cache_control`; all LLM providers translate `list[PromptSegment]` → provider-native format; enables Anthropic prompt caching for long system prompts
- **`fast_model` / `fast_llm`**: `LLMConfig.fast_model` configured via `AUTOWIKI_LLM_FAST_MODEL` (defaults to main model when empty); `make_fast_llm_provider()` returns main provider unchanged when models match; used for Pass 1 outline and Pass 3 fact-check to cut latency and cost; threaded through jobs → planner Phase 2 → page generator
- **`cache_ttl`**: `LLMConfig.cache_ttl` hints cache lifetime for providers that support it; Anthropic uses `"ephemeral"` cache control on system segments

## API Surface

### REST/WebSocket (Phase 1 + Phase 2)
```http
POST  /api/repos                              # Submit repo for indexing
GET   /api/repos                             # List all repos
GET   /api/repos/{repo_id}                   # Status + metadata
POST  /api/repos/{repo_id}/refresh           # Trigger incremental refresh
GET   /api/repos/{repo_id}/graph             # Dependency graph (nodes + edges)
GET   /api/repos/{repo_id}/wiki              # List wiki pages
GET   /api/repos/{repo_id}/wiki/{slug}       # Get page Markdown
POST  /api/repos/{repo_id}/chat              # Create a new chat session
GET   /api/repos/{repo_id}/chat/{session_id} # Get chat history
GET   /api/jobs/{job_id}                     # Job status + progress
WS    /ws/jobs/{job_id}                      # Stream job progress
WS    /ws/repos/{repo_id}/chat/{session_id}  # Stream chat responses
```

### CLI (Phase 1)
```bash
autowiki index github.com/owner/repo [--reuse-index]
autowiki list
autowiki serve [--port 3000] [--debug]
autowiki config show
autowiki config set <key> <value>
```

### MCP Tools (Phase 3, not yet implemented)
`read_wiki_structure`, `read_wiki_page`, `search_wiki`, `ask_question`, `deep_research`

## Model Selection

- **Planning** (architecture, design, writing plans): use `gemini-2.5-pro-preview-05-06`
- **Executing** (implementation, refactoring, code changes): use `gemini-2.5-pro-preview-05-06`

## Pre-Commit Checks (Required)

Before every commit, run and resolve all issues from:

```bash
uv run ruff check .
uv run ruff format --check .
npm run lint          # run from web/
```

All lint errors and format violations **must be fixed** before committing. Do not commit with outstanding `ruff` errors or `npm run lint` errors.

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
- **Phase 3** — Deep Research mode + MCP server
- **Phase 4** — GitHub webhooks + user steering (`.autowiki/wiki.json`)
- **Phase 5** — GitLab/Bitbucket + hybrid search
