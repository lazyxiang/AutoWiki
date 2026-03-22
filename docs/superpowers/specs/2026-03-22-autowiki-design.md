# AutoWiki — Product Requirements Document

**Date:** 2026-03-22
**Status:** Approved
**Project:** AutoWiki

---

## 1. Executive Summary

AutoWiki is a self-hosted, open-source AI-powered wiki generator for software repositories. Given a GitHub repository URL, it produces a browsable, interactive wiki containing architecture overviews, module breakdowns, dependency diagrams, source-linked documentation, and a conversational Q&A interface — all running locally with user-supplied API keys.

AutoWiki is designed to close the gaps left by existing tools (DeepWiki, Zread, deepwiki-open, CodeWiki):

- **Accuracy at scale** — Tree-Sitter AST analysis + hierarchical multi-agent generation handles repos up to 1M LOC without losing architectural context.
- **Update freshness** — Incremental re-indexing triggered by GitHub webhooks or CLI keeps the wiki current without full regeneration.
- **Developer experience** — Single `docker-compose up`, minimal config, full-surface access (Web UI + MCP server + CLI).

---

## 2. Background & Competitive Analysis

### 2.1 Products Studied

| Product | Type | Core Approach | Key Strength | Key Gap |
|---|---|---|---|---|
| **DeepWiki** (Cognition AI) | Hosted SaaS | RAG + semantic hypergraph | Deep Research mode, MCP, 50K pre-indexed repos | GitHub only; no auto-sync without badge; LLM undisclosed |
| **Zread** (Zhipu AI) | Hosted SaaS | GLM-4.5 + static analysis | Community Buzz feature, Chinese-native | GitHub only; no auto-sync; MCP paywalled |
| **deepwiki-open** (AsyncFuncAI) | Self-hosted OSS | Next.js + FastAPI + AdalFlow/FAISS | 7 AI providers, GitHub/GitLab/Bitbucket | Generation blocks on large repos; no incremental update; no AST analysis |
| **CodeWiki** (FSoft-AI4Code) | CLI framework | Tree-Sitter AST + hierarchical agents | Benchmarked accuracy (68.79%), scales to 1.4M LOC | No web UI; no Q&A/chat; no MCP; CLI only |

### 2.2 Key Gaps AutoWiki Addresses

1. **No existing self-hosted tool combines AST analysis with RAG** — deepwiki-open uses RAG only (loses architectural context); CodeWiki uses AST only (no chat). AutoWiki uses both.
2. **All existing tools require full re-generation on updates** — AutoWiki introduces incremental re-indexing via file-level change detection.
3. **No self-hosted tool exposes an MCP server** — AutoWiki includes one out of the box.
4. **Generation blocking** — deepwiki-open's monolithic approach blocks on large repos. AutoWiki's Worker + API split makes generation fully async.

---

## 3. Goals & Non-Goals

### Goals

- Generate accurate, navigable wikis for GitHub repositories up to 1M LOC.
- Support multi-turn conversational Q&A and Deep Research mode against the indexed codebase.
- Keep wikis fresh via incremental re-indexing (webhook or CLI-triggered).
- Ship three access surfaces: Web UI, MCP server, CLI.
- Run as a self-hosted Docker deployment with a single `docker-compose up`.
- Be provider-agnostic: ship with Claude Sonnet 4 as the recommended default; support any OpenAI-compatible endpoint.

### Non-Goals (v1)

- GitLab and Bitbucket support (designed for later addition via platform adapter interface).
- Private repository support (architecture accommodates it; not shipped in v1).
- Hosted cloud tier.
- VS Code extension.
- Support for GitHub Issues and Pull Requests indexing.
- Real-time collaboration on wiki pages.

---

## 4. Architecture

### 4.1 System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        User Surfaces                         │
│  Browser (Next.js)  │  CLI (autowiki CLI)  │  MCP Server    │
└──────────┬──────────┴──────────┬───────────┴──────┬─────────┘
           │                     │                   │
           └─────────────────────▼───────────────────┘
                          ┌──────────────┐
                          │  API Gateway │  FastAPI — REST + WebSocket
                          └──────┬───────┘
                                 │  Redis + ARQ job queue
                    ┌────────────▼────────────┐
                    │      Worker Service      │
                    │  1. Repo Ingestion       │
                    │  2. AST Analysis         │
                    │  3. RAG Indexer          │
                    │  4. Wiki Planner         │
                    │  5. Page Generator       │
                    │  6. Diagram Synthesis    │
                    └─────────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │       Storage Layer      │
                    │  SQLite (jobs/metadata)  │
                    │  FAISS (vectors/repo)    │
                    │  Markdown files (wiki)   │
                    └─────────────────────────┘
```

### 4.2 Service Decomposition

**API Gateway** (`api/`) — FastAPI application. Handles all inbound requests: REST endpoints, WebSocket streaming, MCP server, and GitHub webhook. Enqueues jobs to Redis. Never performs long-running computation itself.

**Worker Service** (`worker/`) — Python process pool managed by ARQ. Executes the six-stage generation pipeline. Scales horizontally by adding worker replicas.

**Frontend** (`web/`) — Next.js 15 application. Communicates with the API Gateway only. Stateless.

**Storage** — SQLite for structured metadata; FAISS indexes persisted to disk per repository; Markdown files for wiki content.

### 4.3 Generation Pipeline (Six Stages)

| Stage | Responsibility | Key Technology |
|---|---|---|
| **1. Repo Ingestion** | Clone/fetch repo; apply file filters (`.autowikiignore` + built-in rules); detect changes via commit SHA diff | `gitpython` |
| **2. AST Analysis** | Parse source files with Tree-Sitter; extract functions, classes, imports, call graphs; build unified dependency graph + module tree | `tree-sitter` (9 languages) |
| **3. RAG Indexer** | Chunk documents with overlap; generate embeddings; build/update FAISS index; persist per `{repo_hash}` | `langchain` splitter, configurable embedding provider, `faiss-cpu` |
| **4. Wiki Planner** | Feed AST dependency graph + repo structure to LLM; produce hierarchical JSON page plan; validate + retry on malformed output (up to 3 attempts) | LLM structured output |
| **5. Page Generator** | Per page: RAG retrieval + AST graph slice injected as context; LLM generates page; recurse for large modules via sub-agents; stream results | Hierarchical agent loop |
| **6. Diagram Synthesis** | Per page: generate Mermaid diagrams (architecture, data-flow, sequence); validate syntax; embed in page Markdown | LLM + Mermaid validator |

### 4.4 Incremental Re-Indexing

Every indexed repo stores the HEAD commit SHA at index time. On refresh trigger (webhook `push` event or `autowiki refresh`):

1. Fetch current HEAD SHA from GitHub API.
2. Diff against stored SHA to identify changed files.
3. Determine affected **modules**: a module is a top-level package directory (e.g., `src/auth/`, `src/api/`). A changed file belongs to the module whose directory path is the longest prefix match of the file path. Files at the repo root belong to a synthetic `root` module.
4. Re-run stages 1–6 only for affected modules.
5. Update FAISS index for changed chunks only (delete-and-insert by chunk ID).
6. Update stored commit SHA.

This is the primary freshness differentiator over all competing products.

### 4.5 Supported Languages (AST Analysis)

Python, JavaScript, TypeScript, Java, Go, Rust, C, C++, C# — 9 languages via Tree-Sitter grammars. Files in unsupported languages are still indexed via RAG (text-only, no AST graph).

---

## 5. Data Models

### 5.1 SQLite Schema

```sql
repositories (
  id            TEXT PRIMARY KEY,   -- sha256(platform:owner/repo)
  owner         TEXT NOT NULL,
  name          TEXT NOT NULL,
  platform      TEXT DEFAULT 'github',
  last_commit   TEXT,               -- HEAD SHA at last index
  status        TEXT,               -- pending | indexing | ready | error
  indexed_at    DATETIME,
  wiki_path     TEXT                -- absolute path to wiki Markdown dir
)

jobs (
  id            TEXT PRIMARY KEY,   -- UUID
  repo_id       TEXT REFERENCES repositories(id),
  type          TEXT,               -- full_index | refresh | page_regen
  status        TEXT,               -- queued | running | done | failed
  progress      INTEGER DEFAULT 0,  -- 0–100
  error         TEXT,
  created_at    DATETIME,
  finished_at   DATETIME
)

wiki_pages (
  id            TEXT PRIMARY KEY,
  repo_id       TEXT REFERENCES repositories(id),
  slug          TEXT,
  title         TEXT,
  content       TEXT,               -- Markdown
  page_order    INTEGER,
  parent_slug   TEXT,               -- null for top-level pages
  updated_at    DATETIME
)

chat_sessions (
  id            TEXT PRIMARY KEY,
  repo_id       TEXT REFERENCES repositories(id),
  created_at    DATETIME
)

chat_messages (
  id            TEXT PRIMARY KEY,
  session_id    TEXT REFERENCES chat_sessions(id),
  role          TEXT,               -- user | assistant
  content       TEXT,
  created_at    DATETIME
)
```

### 5.2 File System Layout

```
~/.autowiki/
  repos/
    {repo_hash}/
      clone/              ← shallow git clone
      faiss.index         ← FAISS vector index
      faiss.meta.pkl      ← chunk metadata (file, line range, slug)
      wiki/
        index.md
        {slug}.md         ← one file per wiki page
      ast/
        dep_graph.json    ← unified dependency graph
        module_tree.json  ← hierarchical module structure
  autowiki.db             ← SQLite database
  logs/
    worker.log
    api.log
```

### 5.3 LLM Provider Configuration

Default provider: **Anthropic Claude Sonnet 4** (`claude-sonnet-4-6`). Override via `autowiki.yml` or environment variables:

```yaml
# autowiki.yml
llm:
  provider: google             # anthropic, google, openai, openai-compatible, ollama
  model: gemini-1.5-pro       # or gemini-1.5-flash, claude-sonnet-4-6, gpt-4o, etc.
  api_key: ${GOOGLE_API_KEY}

embedding:
  provider: google             # openai, google, ollama
  model: text-embedding-004
  api_key: ${GOOGLE_API_KEY}
```

**Phase 1 provider support:** `anthropic`, `google` (Gemini), `openai`, `openai-compatible` (any OpenAI-compatible base URL), `ollama` (local). Both generation and embedding are supported for these providers in Phase 1.

Any OpenAI-compatible endpoint works via `provider: openai-compatible` + `base_url`. Switching embedding providers requires re-indexing (incompatible vector spaces — this is surfaced clearly in the UI/CLI).

**Config discovery and precedence (highest to lowest):**
1. Environment variables (e.g., `AUTOWIKI_LLM_PROVIDER`, `ANTHROPIC_API_KEY`)
2. `autowiki.yml` in the current working directory
3. `~/.autowiki/autowiki.yml` (user-global config)
4. Built-in defaults

Both the API service and the Worker service read config from the same source at startup. In Docker Compose, environment variables in each container's `environment:` block take precedence over any mounted `autowiki.yml`.

---

## 6. API Design

### 6.1 REST Endpoints

```
POST   /api/repos                              Submit repo for indexing
GET    /api/repos/{repo_id}                    Repo status + metadata
GET    /api/repos/{repo_id}/wiki               List all wiki pages
GET    /api/repos/{repo_id}/wiki/{slug}        Get single wiki page (Markdown)
POST   /api/repos/{repo_id}/refresh            Trigger incremental re-index *(Phase 2)*
GET    /api/jobs/{job_id}                      Poll job status + progress (0–100)

POST   /api/repos/{repo_id}/chat               Create chat session
GET    /api/repos/{repo_id}/chat/{session_id}  Get chat history
WS     /ws/repos/{repo_id}/chat/{session_id}   Streaming chat (WebSocket)
POST   /api/repos/{repo_id}/research           Start Deep Research job
WS     /ws/repos/{repo_id}/research/{job_id}   Stream research progress
WS     /ws/jobs/{job_id}                       Stream job progress 0–100 (used by JobProgressBar)

POST   /webhook/github                         GitHub push webhook *(Phase 4)*
```

**Key endpoint schemas:**

`POST /api/repos` — Request:
```json
{ "url": "https://github.com/owner/repo" }
```
Response `202 Accepted`:
```json
{ "repo_id": "abc123", "job_id": "uuid-...", "status": "queued" }
```

`POST /api/repos/{repo_id}/chat` — Creates a new session (no body required).
Response `201 Created`:
```json
{ "session_id": "uuid-..." }
```
The caller then opens a WebSocket at `/ws/repos/{repo_id}/chat/{session_id}` and sends/receives JSON messages: `{ "role": "user"|"assistant", "content": "..." }`.

`POST /webhook/github` — Requires `X-Hub-Signature-256` HMAC header (SHA-256 of the raw payload, signed with the webhook secret configured in `autowiki.yml` under `webhook.github_secret`). Requests failing signature validation return `401`. This prevents unauthorized refresh triggers when the API is exposed on a public address.

### 6.2 CLI (`autowiki`)

```bash
# Phase 1
autowiki index github.com/owner/repo           # Index a repository
autowiki index github.com/owner/repo --force   # Force full re-index
autowiki list                                  # List indexed repos + status
autowiki serve [--port 3000]                   # Start full stack (API + worker + web UI)
autowiki config show                           # Show current config
autowiki config set llm.provider anthropic     # Update config value

# Phase 2
autowiki refresh github.com/owner/repo         # Incremental refresh (commit-SHA diff)
autowiki chat github.com/owner/repo "..."      # Terminal Q&A (multi-turn not supported in CLI)

# Phase 3
autowiki research github.com/owner/repo "..."  # Trigger Deep Research; print report to stdout
```

`autowiki serve` starts the full stack in a single foreground process: it spawns the FastAPI API server, an ARQ worker, and the Next.js frontend as subprocesses. It is the non-Docker entry point. In Docker Compose, each service runs independently; `autowiki serve` is not used.

### 6.3 MCP Server Tools

| Tool | Description |
|---|---|
| `read_wiki_structure` | Returns full page hierarchy for a repo |
| `read_wiki_page` | Returns Markdown content of a page by slug |
| `search_wiki` | Semantic search across wiki + codebase |
| `ask_question` | Single-turn RAG Q&A |
| `deep_research` | Multi-step investigation; returns structured research report |

Transport: `stdio` (local) or `SSE` (remote). No authentication required for local use. Configured via standard `mcp.json`.

---

## 7. Q&A and Deep Research

### 7.1 Multi-Turn Chat

- RAG retrieval per turn: top-k chunks from FAISS index, ranked by cosine similarity.
- Conversation history injected into LLM context (sliding window, last **10 turns** by default; configurable via `chat.history_window` in `autowiki.yml`).
- Responses streamed via WebSocket.
- Source citations included: every response references source file + line range.
- Session history persisted in SQLite (`chat_sessions` / `chat_messages`).
- The MCP `ask_question` tool is single-turn (stateless); multi-turn context is only maintained in WebSocket sessions.

### 7.2 Deep Research Mode

```
User question
      │
      ▼
Research Planner (LLM)
  → produces: Research Plan (JSON, 3–5 investigation steps)
      │
      ▼ (loop, up to 5 rounds)
Investigator Agent
  → RAG retrieval + AST graph traversal per step
  → streams intermediate findings to client
      │
      ▼
Synthesizer (LLM)
  → Final Conclusion: summary + source references + confidence level
```

Deep Research is available in the Web UI (ResearchPanel), CLI (`autowiki research github.com/owner/repo "..."`), and MCP (`deep_research` tool).

---

## 8. Frontend (Web UI)

### 8.1 Tech Stack

- Next.js 15 (App Router) + TypeScript
- Tailwind CSS
- shadcn/ui (component primitives)
- Mermaid.js (diagram rendering)
- D3 or react-flow (interactive dependency graph)

### 8.2 Routes

```
/                          Home: URL input, index a new repo
/repos                     All indexed repos + status
/{owner}/{repo}            Wiki index page + sidebar navigation
/{owner}/{repo}/{slug}     Individual wiki page
/{owner}/{repo}/chat       Multi-turn chat interface
/{owner}/{repo}/research   Deep Research interface
/{owner}/{repo}/graph      Interactive dependency graph
```

### 8.3 Key Components

| Component | Description |
|---|---|
| `IndexForm` | GitHub URL input; triggers index job on submit |
| `JobProgressBar` | Real-time progress during indexing; fed by `WS /ws/jobs/{job_id}` (streams 0–100 integer progress events) |
| `WikiSidebar` | Hierarchical page tree, collapsible, keyboard-navigable |
| `WikiPage` | Markdown renderer with syntax highlighting + Mermaid blocks |
| `ChatPanel` | Streaming multi-turn chat with source citations |
| `ResearchPanel` | Progressive reveal: Research Plan → findings → Final Conclusion |
| `DependencyGraph` | Interactive force-directed module relationship graph |

### 8.4 UX Principles

1. **Single command to start** — `docker-compose up`; UI live at `localhost:3000`; API key prompted on first index.
2. **Progressive rendering** — Wiki pages appear as they are generated (streamed); users see content in < 60 seconds for small repos.
3. **Source citations** — Every generated paragraph links to the source file + line range it was derived from.
4. **Diagram-first** — Architecture diagrams appear at the top of every major page; Mermaid source always expandable/copyable.
5. **Dark mode default** — Light mode toggle available.

---

## 9. User-Facing Configuration

### 9.1 `.autowikiignore` *(Phase 2)*

Repos may include `.autowikiignore` in their root (`.gitignore` syntax) to control what AutoWiki indexes. In Phase 1, only built-in exclusion rules apply; `.autowikiignore` support ships in Phase 2.

```
# .autowikiignore
node_modules/
dist/
*.test.ts
*.spec.py
fixtures/
__pycache__/
migrations/
```

AutoWiki also applies built-in exclusion rules for common non-source directories (`node_modules`, `vendor`, `.git`, build outputs, binary files, etc.).

### 9.2 `.autowiki/wiki.json` (Steerability) *(Phase 4)*

Repos may include `.autowiki/wiki.json` to steer wiki generation (inspired by DeepWiki's `.devin/wiki.json`). This ships in Phase 4; prior phases use the LLM-generated page plan exclusively.

```json
{
  "repo_notes": [
    "This project uses a custom event bus defined in src/core/bus.ts — treat it as the central communication backbone.",
    "The `legacy/` directory is intentionally excluded from documentation."
  ],
  "pages": [
    { "title": "Architecture Overview", "modules": ["src/core", "src/api"] },
    { "title": "Authentication System", "modules": ["src/auth"] },
    { "title": "Data Pipeline", "modules": ["src/pipeline", "src/workers"] }
  ]
}
```

If `pages` is defined, it overrides the LLM-generated page plan. `repo_notes` are injected into every LLM call as additional context.

---

## 10. Error Handling

| Failure | Behavior |
|---|---|
| LLM API rate limit / timeout | Exponential backoff with jitter (3 retries); job marked `failed` with actionable error after exhaustion |
| Malformed LLM output (bad JSON page plan) | Structured output validation + corrective retry prompt (up to 3 attempts); fallback to flat page structure |
| Repo exceeds size limit (>500K files) | Pre-clone file count check; reject with clear message and suggestion to use `.autowikiignore` (available Phase 2+) or reduce scope via `autowiki.yml` file filters |
| FAISS index corruption | Auto-detect on load; delete and trigger re-index with user notification |
| GitHub API rate limit (webhook) | Queue webhook jobs; process with delay; surface rate limit status in UI |
| Embedding provider unavailable | Block new index jobs; serve existing cached wiki; surface error clearly |
| Tree-Sitter parse failure (unsupported language) | Skip AST analysis for that file; continue with text-only RAG; log warning |
| Worker crash mid-job | ARQ retries job up to 2 times; marks `failed` with last known error on exhaustion |

---

## 11. Testing Strategy

| Layer | Approach | Tooling |
|---|---|---|
| **Unit** | AST parsers, file filters, chunk splitter, Mermaid validator — pure function tests | `pytest` |
| **Integration** | Full Worker pipeline against a small fixture repo committed to the test suite; assert page count, diagram presence, no crashes | `pytest` + fixture repo |
| **API** | All REST endpoints; mock LLM responses with recorded fixtures | FastAPI `TestClient` |
| **Frontend** | Component rendering; critical user flows (index → wiki view → chat) | `vitest` + React Testing Library + Playwright |
| **LLM regression** | Golden-file tests: pinned fixture repo + pinned model; diff output against stored baseline | Custom pytest plugin |

**Coverage targets:**
- Unit + Integration + API: ≥ 80% line coverage on `worker/` and `api/` Python packages.
- Frontend: no hard coverage target; Playwright E2E tests must cover all critical paths.

**CI pipeline (GitHub Actions):**
- Unit, integration, and API tests run on every pull request (no LLM API keys required — all LLM calls are mocked).
- Frontend tests (vitest + Playwright) run on every PR.
- LLM regression (golden-file) tests run on a scheduled nightly job only, using repository secrets for API keys. Failures create a GitHub issue automatically but do not block merges.

---

## 12. Non-Functional Requirements

| Requirement | Target |
|---|---|
| Time to first wiki page (≤ 50K LOC repo) | < 3 minutes |
| Incremental refresh latency (single changed file) | < 60 seconds |
| Chat first-token latency | < 2 seconds |
| Deep Research completion | < 3 minutes |
| Maximum supported repo size | 1M LOC (with `.autowikiignore`) |
| Storage per indexed repo | < 500MB |
| Total storage at scale | ~500MB × N repos; no automatic eviction in v1 — users must manually delete repos via `autowiki list` + `autowiki delete`. Disk exhaustion causes new index jobs to fail with a `DISK_FULL` error surfaced in the UI and job status. |
| Docker image size (combined) | < 2GB |
| Startup time (`docker-compose up` to ready) | < 30 seconds |
| AST-supported languages | 9 (Python, JS, TS, Java, Go, Rust, C, C++, C#) |

---

## 13. Docker Deployment

```yaml
# docker-compose.yml (illustrative)
services:
  api:
    build: ./api
    ports: ["3001:3001"]
    environment:
      - REDIS_URL=redis://redis:6379
      - DATABASE_PATH=/data/autowiki.db
    volumes:
      - autowiki_data:/data

  worker:
    build: ./worker
    environment:
      - REDIS_URL=redis://redis:6379
      - DATABASE_PATH=/data/autowiki.db
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    volumes:
      - autowiki_data:/data

  web:
    build: ./web
    ports: ["3000:3000"]
    environment:
      - NEXT_PUBLIC_API_URL=http://api:3001

  redis:
    image: redis:7-alpine

volumes:
  autowiki_data:
```

---

## 14. Phased Delivery

### Phase 1 — Core (MVP)
- Worker pipeline: stages 1–5 (ingestion, AST, RAG, planning, generation)
- API Gateway: index, status, wiki CRUD endpoints
- Web UI: IndexForm, JobProgressBar, WikiSidebar, WikiPage
- CLI: `index`, `list`, `serve`
- Docker Compose deployment

### Phase 2 — Chat, Diagrams & Refresh
- Stage 6: Diagram Synthesis
- Multi-turn chat (WebSocket streaming)
- ChatPanel in UI
- DependencyGraph view
- `autowiki chat` CLI command
- `autowiki refresh` CLI command (incremental re-index, commit-SHA-based; no webhook yet)
- `.autowikiignore` support

### Phase 3 — Research & MCP
- Deep Research mode
- ResearchPanel in UI
- MCP server (all 5 tools)
- `autowiki research` CLI command

### Phase 4 — Webhook & Steerability
- GitHub webhook endpoint (`POST /webhook/github`) with HMAC signature validation
- `.autowiki/wiki.json` steerability
- Auto-refresh on `push` events (no user action required)

### Phase 5 — Polish & Extensibility
- Platform adapter interface (GitLab, Bitbucket stubs)
- Private repo support (GitHub PAT)
- Golden-file LLM regression tests
- Performance profiling and optimization

---

## 15. Open Questions

1. **Embedding provider default** — OpenAI `text-embedding-3-small` requires a separate OpenAI API key even when using Anthropic for generation. Should AutoWiki default to a locally-runnable embedding model (e.g., via Ollama) to reduce provider dependency?
2. **FAISS vs. alternatives** — FAISS is in-process and zero-infrastructure but does not support hybrid keyword+semantic search. Should v1 consider `sqlite-vec` (SQLite vector extension) to reduce the dependency footprint?
3. **Page limit** — Should AutoWiki enforce a max page count per wiki (like DeepWiki's 30/80 limits) to bound generation cost and time? Or leave it unbounded and let `.autowikiignore` do the work?

**Resolved:**

4. ~~Auth for the web UI~~ — **Resolved:** The API and web UI bind to `127.0.0.1` by default (localhost only). Users who need network exposure must explicitly set `server.host: 0.0.0.0` in `autowiki.yml`. When `host` is set to a non-loopback address, the startup log emits a prominent warning. An optional bearer-token auth layer (`server.auth_token`) is provided for users who expose the service over a network; it is not required for local use.
