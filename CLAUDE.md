# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

AutoWiki **Phase 1 is complete** (tagged `v0.1.0-phase1`). The full source code is implemented and tested. Phase 2 planning is next.

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
- **Worker Service** (`worker/`) — ARQ background jobs, 5-stage generation pipeline
- **Frontend** (`web/`) — Next.js 16.2.1 + TypeScript + Tailwind v4 + shadcn/ui, stateless SPA
- **Storage** — SQLite for metadata, FAISS for vector index, Markdown files for wiki pages

### Generation Pipeline (5 Stages — Phase 1)
1. **Repo Ingestion** (`worker/pipeline/ingestion.py`) — shallow clone, file filtering, commit SHA
2. **AST Analysis** (`worker/pipeline/ast_analysis.py`) — Tree-Sitter entity extraction, module tree
3. **RAG Indexer** (`worker/pipeline/rag_indexer.py`) — LangChain chunking, FAISS IndexFlatIP
4. **Wiki Planner** (`worker/pipeline/wiki_planner.py`) — LLM → hierarchical JSON page plan, retry + fallback
5. **Page Generator** (`worker/pipeline/page_generator.py`) — RAG retrieval + LLM per-page Markdown

Supported AST languages: Python, JavaScript/JSX, TypeScript/TSX, Java, Go, Rust, C, C++, C#

### Data Storage Layout
```
~/.autowiki/
  autowiki.db               ← SQLite (repos, jobs, wiki_pages)
  repos/{repo_hash}/
    clone/                  ← shallow git clone
    faiss.index             ← vector index
    faiss.meta.pkl          ← chunk metadata
    wiki/                   ← Markdown pages
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

## API Surface

### REST/WebSocket (Phase 1)
```
POST  /api/repos                              # Submit repo for indexing
GET   /api/repos                             # List all repos
GET   /api/repos/{repo_id}                   # Status + metadata
GET   /api/repos/{repo_id}/wiki              # List wiki pages
GET   /api/repos/{repo_id}/wiki/{slug}       # Get page Markdown
GET   /api/jobs/{job_id}                     # Job status + progress
WS    /ws/jobs/{job_id}                      # Stream job progress
```

### CLI (Phase 1)
```
autowiki index github.com/owner/repo
autowiki list
autowiki serve [--port 3000]
autowiki config show
autowiki config set <key> <value>
```

### MCP Tools (Phase 3, not yet implemented)
`read_wiki_structure`, `read_wiki_page`, `search_wiki`, `ask_question`, `deep_research`

## Testing

- **Framework**: pytest with `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed)
- **Coverage target**: ≥80% on `worker/` and `api/` — currently at 80%
- **Run**: `pytest tests/ --ignore=tests/e2e`
- **Fixtures**: `mock_llm`, `mock_embedding` in `tests/conftest.py`; fixture repo at `tests/fixtures/simple-repo/`

## Deployment

```bash
docker-compose up          # starts api, worker, web, redis
```

Non-Docker: `autowiki serve` spawns API + worker + Next.js as subprocesses.

## Phased Delivery

- **Phase 1** ✅ — Core pipeline (index + static wiki + REST API + web UI + CLI)
- **Phase 2** — Incremental refresh + Q&A chat + `.autowikiignore` + diagram synthesis
- **Phase 3** — Deep Research mode + MCP server
- **Phase 4** — GitHub webhooks + user steering (`.autowiki/wiki.json`)
- **Phase 5** — GitLab/Bitbucket + hybrid search
