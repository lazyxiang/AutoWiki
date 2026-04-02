# GEMINI.md

This file provides guidance to GEMINI when working with code in this repository.

## Project Status

AutoWiki **Phase 1 and Phase 2 are complete**. Phase 1 tagged `v0.1.0-phase1`; Phase 2 (chat, diagrams, incremental refresh) merged via PR #4.

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
- **Worker Service** (`worker/`) — ARQ background jobs, 7-stage generation pipeline
- **Frontend** (`web/`) — Next.js 16.2.1 + TypeScript + Tailwind v4 + shadcn/ui, stateless SPA
- **Storage** — SQLite for metadata, FAISS for vector index, Markdown files for wiki pages

### Generation Pipeline (7 Stages)
1. **Repo Ingestion** (`worker/pipeline/ingestion.py`) — shallow clone, file filtering, commit SHA
2. **AST Analysis** (`worker/pipeline/ast_analysis.py`) — single-pass Tree-Sitter entity extraction → `FileAnalysis`
3. **Dependency Graph** (`worker/pipeline/dependency_graph.py`) — file-level import graph + clusters
4. **RAG Indexer** (`worker/pipeline/rag_indexer.py`) — LangChain chunking, FAISS IndexFlatIP
5. **Wiki Planner** (`worker/pipeline/wiki_planner.py`) — LLM generates logical page tree with file assignments → `WikiPlan`
6. **Page Generator** (`worker/pipeline/page_generator.py`) — RAG retrieval + LLM per-page Markdown
7. **Architecture Diagram** (`worker/pipeline/diagram_synthesis.py`) — Mermaid diagram from wiki plan

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
      architecture.mmd      ← Mermaid architecture diagram
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
- **Wiki plan**: LLM generates logical page hierarchy with file assignments; slugs derived from titles, not stored in wiki.json
- **wiki.json format**: user-facing (title/purpose/parent/page_notes); `ast/wiki_plan.json` is internal (includes files); `Repository.wiki_structure` is API-compatible (includes derived slugs/parent_slugs for frontend)
- **FileAnalysis**: single-pass AST analysis — `analyze_all_files()` replaces both `build_enhanced_module_tree()` and `_build_file_entities()`

## API Surface

### REST/WebSocket (Phase 1)
```http
POST  /api/repos                              # Submit repo for indexing
GET   /api/repos                             # List all repos
GET   /api/repos/{repo_id}                   # Status + metadata
GET   /api/repos/{repo_id}/wiki              # List wiki pages
GET   /api/repos/{repo_id}/wiki/{slug}       # Get page Markdown
GET   /api/jobs/{job_id}                     # Job status + progress
WS    /ws/jobs/{job_id}                      # Stream job progress
```

### CLI (Phase 1)
```bash
autowiki index github.com/owner/repo
autowiki list
autowiki serve [--port 3000] [--debug]
autowiki config show
autowiki config set <key> <value>
```

### MCP Tools (Phase 3, not yet implemented)
`read_wiki_structure`, `read_wiki_page`, `search_wiki`, `ask_question`, `deep_research`

## Model Selection

- **Planning** (architecture, design, writing plans): use `gemini-3.1-pro-preview`
- **Executing** (implementation, refactoring, code changes): use `gemini-3.1-pro-preview`

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
- **Phase 3** — Deep Research mode + MCP server
- **Phase 4** — GitHub webhooks + user steering (`.autowiki/wiki.json`)
- **Phase 5** — GitLab/Bitbucket + hybrid search
