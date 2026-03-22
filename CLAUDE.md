# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

AutoWiki is currently in the **design/specification phase** — no source code has been written yet. The repository contains only the PRD and design document at `docs/superpowers/specs/2026-03-22-autowiki-design.md`. All architecture described below is planned, not yet implemented.

## What AutoWiki Is

A self-hosted, open-source AI-powered wiki generator for software repositories. Given a GitHub URL, it generates a browsable wiki with architecture overviews, module breakdowns, dependency diagrams, source-linked documentation, and a conversational Q&A interface — running locally with user-supplied API keys.

## Planned Architecture

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
- **API Gateway** (`api/`) — FastAPI, REST + WebSocket + MCP endpoints, job enqueuing
- **Worker Service** (`worker/`) — ARQ background jobs, 6-stage generation pipeline
- **Frontend** (`web/`) — Next.js 15 + TypeScript + Tailwind + shadcn/ui, stateless SPA
- **Storage** — SQLite for metadata, FAISS for vector index, Markdown files for wiki pages

### Generation Pipeline (6 Stages)
1. **Repo Ingestion** — shallow clone, file filtering, change detection via commit SHA
2. **AST Analysis** — Tree-Sitter parsing across 8 languages, dependency graph construction
3. **RAG Indexer** — chunking (LangChain), embeddings, FAISS indexing
4. **Wiki Planner** — LLM call producing hierarchical page plan (structured JSON output)
5. **Page Generator** — hierarchical agent loop; recursive sub-agents for large modules
6. **Diagram Synthesis** — Mermaid generation (architecture, data-flow, sequence diagrams)

Supported AST languages: Python, JavaScript, TypeScript, Java, Go, C, C++, C#

### Data Storage Layout
```
~/.autowiki/
  autowiki.db               ← SQLite (repos, jobs, wiki_pages, chat sessions)
  repos/{repo_hash}/
    clone/                  ← shallow git clone
    faiss.index             ← vector index
    faiss.meta.pkl          ← chunk metadata
    wiki/                   ← Markdown pages
    ast/                    ← dependency & module graphs
  logs/
```

### Key SQLite Tables
- `repositories` — repo metadata, status, last-indexed commit SHA
- `jobs` — indexing/refresh job tracking with 0–100 progress
- `wiki_pages` — hierarchical page structure with slugs and parent refs
- `chat_sessions` / `chat_messages` — conversation history

## Configuration

Config discovery order (highest to lowest precedence):
1. Environment variables (`ANTHROPIC_API_KEY`, `AUTOWIKI_LLM_PROVIDER`, etc.)
2. `autowiki.yml` in current working directory
3. `~/.autowiki/autowiki.yml`
4. Built-in defaults

Default LLM: `claude-sonnet-4-6`. Phase 1 providers: anthropic, openai, openai-compatible, ollama.

## API Surface

### REST/WebSocket
```
POST  /api/repos                              # Submit repo for indexing
GET   /api/repos/{repo_id}                   # Status + metadata
GET   /api/repos/{repo_id}/wiki              # List wiki pages
GET   /api/repos/{repo_id}/wiki/{slug}       # Get page Markdown
POST  /api/repos/{repo_id}/refresh           # Incremental re-index (Phase 2)
WS    /ws/jobs/{job_id}                      # Stream job progress
WS    /ws/repos/{repo_id}/chat/{session_id}  # Streaming chat
```

### MCP Tools (Phase 3)
`read_wiki_structure`, `read_wiki_page`, `search_wiki`, `ask_question`, `deep_research`

### CLI (Phase 1)
```
autowiki index github.com/owner/repo
autowiki list
autowiki serve [--port 3000]
```

## Testing Strategy (Per Spec)

When implementing, tests go in:
- `worker/` and `api/` — pytest, ≥80% line coverage target
- `web/` — vitest + React Testing Library + Playwright E2E

Test types:
- **Unit:** AST parsers, file filters, chunk splitter, Mermaid validator
- **Integration:** pytest with fixture repos
- **API:** FastAPI `TestClient` with mocked LLM responses
- **LLM regression:** Golden-file tests against fixture repos (nightly only, not blocking)

## Deployment

Single `docker-compose up` spins up four services: `api`, `worker`, `web`, `redis` with shared `autowiki_data` volume. Non-Docker: `autowiki serve` spawns all three as subprocesses.

## Phased Delivery

- **Phase 1:** Core pipeline (index + static wiki + web UI)
- **Phase 2:** CLI + incremental refresh + Q&A chat
- **Phase 3:** Deep Research mode + MCP server
- **Phase 4:** GitHub webhooks + user steering (`.autowiki/wiki.json`)
- **Phase 5:** GitLab/Bitbucket + Google provider + hybrid search
