# AutoWiki

Self-hosted, open-source AI-powered wiki generator for GitHub repositories. Point it at a repo and get a browsable, source-linked wiki in minutes — running entirely on your own machine with your own API keys.

## What it does

1. Clones the repository (shallow)
2. Parses source files with Tree-Sitter (Python, JS/TS, Java, Go, Rust, C/C++, C#)
3. Chunks and embeds code into a FAISS vector index
4. Asks an LLM to plan a hierarchical wiki structure
5. Generates each wiki page with RAG-retrieved context

The result is served via a REST API and displayed in a Next.js web UI with sidebar navigation.

---

## Quick start

### Local

**Requirements:** Python 3.12+, Node.js 22+, Redis, an Anthropic or OpenAI API key

```bash
# 1. Install Python packages
pip install .

# 2. Build the web UI
cd web && npm install && npm run build && cd ..

# 3. Set your API keys
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...        # used for embeddings (text-embedding-3-small)

# 4. Start Redis if it isn't already running
redis-server --daemonize yes

# 5. Start everything
autowiki serve
```

Open http://localhost:3000, paste a GitHub URL, and click **Generate Wiki**.

To use a different LLM or embedding provider, set the relevant variables before step 5:

```bash
# Ollama (fully local, no API keys needed)
export AUTOWIKI_LLM_PROVIDER=ollama
export AUTOWIKI_LLM_MODEL=llama3.2
export AUTOWIKI_EMBEDDING_PROVIDER=ollama
export AUTOWIKI_EMBEDDING_MODEL=nomic-embed-text
autowiki serve

# OpenAI for everything
export AUTOWIKI_LLM_PROVIDER=openai
export AUTOWIKI_LLM_MODEL=gpt-4o
export OPENAI_API_KEY=sk-...
autowiki serve

# Google Gemini
export AUTOWIKI_LLM_PROVIDER=google
export AUTOWIKI_LLM_MODEL=gemini-1.5-pro
export AUTOWIKI_EMBEDDING_PROVIDER=google
export AUTOWIKI_EMBEDDING_MODEL=models/text-embedding-004
export GOOGLE_API_KEY=AIzaSy...
autowiki serve
```

### Docker Compose

**Requirements:** Docker, an Anthropic or OpenAI API key

```bash
# Anthropic LLM + OpenAI embeddings (default)
ANTHROPIC_API_KEY=sk-ant-... OPENAI_API_KEY=sk-... docker-compose up

# OpenAI for everything
AUTOWIKI_LLM_PROVIDER=openai AUTOWIKI_LLM_MODEL=gpt-4o \
  OPENAI_API_KEY=sk-... docker-compose up

# Fully local with Ollama (point OLLAMA_HOST at your running instance)
AUTOWIKI_LLM_PROVIDER=ollama AUTOWIKI_LLM_MODEL=llama3.2 \
AUTOWIKI_EMBEDDING_PROVIDER=ollama AUTOWIKI_EMBEDDING_MODEL=nomic-embed-text \
OLLAMA_HOST=http://host.docker.internal:11434 docker-compose up
```

- Web UI: http://localhost:3000
- API: http://localhost:3001

Persistent data (SQLite, FAISS index, clones, wiki Markdown) is stored in the `autowiki_data` Docker volume.

---

## Configuration

AutoWiki resolves config in this order (highest wins):

1. Environment variables
2. `autowiki.yml` in the current directory
3. `~/.autowiki/autowiki.yml`
4. Built-in defaults

### Key environment variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Anthropic API key (used when `AUTOWIKI_LLM_PROVIDER=anthropic`) |
| `OPENAI_API_KEY` | — | OpenAI API key (LLM and/or embeddings) |
| `GOOGLE_API_KEY` | — | Google API key (Gemini LLM and/or embeddings) |
| `AUTOWIKI_LLM_PROVIDER` | `anthropic` | `anthropic` · `openai` · `openai-compatible` · `ollama` · `google` |
| `AUTOWIKI_LLM_MODEL` | `claude-sonnet-4-6` | Model name for the configured provider |
| `AUTOWIKI_LLM_API_KEY` | — | API key override. Required if provider-specific key (e.g. `ANTHROPIC_API_KEY`) is not set or if using a custom base URL. |
| `AUTOWIKI_LLM_BASE_URL` | — | Base URL for `openai-compatible` or `ollama` providers |
| `AUTOWIKI_EMBEDDING_PROVIDER` | `openai` | `openai` · `ollama` · `google` |
| `AUTOWIKI_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model name |
| `AUTOWIKI_EMBEDDING_API_KEY` | — | API key override. Required if provider-specific key (e.g. `OPENAI_API_KEY`) is not set or if using a custom base URL. |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection string |
| `DATABASE_PATH` | `~/.autowiki/autowiki.db` | SQLite database path |
| `AUTOWIKI_DATA_DIR` | `~/.autowiki` | Root directory for clones, indexes, and wiki files |

### YAML config file

```yaml
# autowiki.yml (or ~/.autowiki/autowiki.yml)
llm:
  provider: anthropic          # anthropic | openai | openai-compatible | ollama | google
  model: claude-sonnet-4-6
  api_key: ${ANTHROPIC_API_KEY}
  # base_url: http://localhost:11434/v1   # openai-compatible / ollama only

embedding:
  provider: openai             # openai | ollama | google
  model: text-embedding-3-small
  api_key: ${OPENAI_API_KEY}
```

Manage via CLI:

```bash
autowiki config show
autowiki config set llm.provider ollama
autowiki config set llm.model llama3.2
autowiki config set embedding.provider ollama
autowiki config set embedding.model nomic-embed-text
```

---

## CLI

```bash
# Index a repository
autowiki index github.com/owner/repo

# List all indexed repositories
autowiki list

# Start the full stack (API + worker + web UI)
autowiki serve [--port 3000] [--api-port 3001]

# Show or update config
autowiki config show
autowiki config set <key> <value>
```

---

## API

```
POST  /api/repos                         Submit a repo for indexing → {repo_id, job_id}
GET   /api/repos                         List all repos
GET   /api/repos/{repo_id}               Repo status and metadata
GET   /api/repos/{repo_id}/wiki          List wiki pages (ordered)
GET   /api/repos/{repo_id}/wiki/{slug}   Get a wiki page (Markdown + metadata)
GET   /api/jobs/{job_id}                 Job status and progress (0–100)
WS    /ws/jobs/{job_id}                  Stream {progress, status} until done/failed
```

Example:

```bash
# Submit a repo
curl -s -X POST http://localhost:3001/api/repos \
  -H "Content-Type: application/json" \
  -d '{"url": "https://github.com/psf/requests"}' | jq .
# → {"repo_id": "a3f8...", "job_id": "uuid...", "status": "queued"}

# Poll progress
curl -s http://localhost:3001/api/jobs/<job_id> | jq .progress

# Read a wiki page
curl -s http://localhost:3001/api/repos/<repo_id>/wiki/overview | jq .content
```

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ --ignore=tests/e2e

# Run with coverage
pytest tests/ --ignore=tests/e2e \
  --cov=worker --cov=api --cov=shared --cov-report=term-missing
```

---

## Project structure

```
AutoWiki/
├── api/                    # FastAPI gateway
│   ├── routers/            # REST endpoints (repos, jobs, wiki)
│   └── ws/                 # WebSocket job progress
├── worker/                 # ARQ background worker
│   ├── pipeline/           # 5-stage generation pipeline
│   ├── llm/                # LLM provider adapters
│   └── embedding/          # Embedding provider adapters
├── shared/                 # Config, SQLAlchemy models, database
├── cli/                    # Typer CLI (index, list, serve, config)
├── web/                    # Next.js 16 frontend
└── tests/                  # pytest suite (63 tests, 80% coverage)
```

---

## Supported providers

| Provider | LLM | Embeddings |
|---|---|---|
| Anthropic | ✅ | — |
| OpenAI / compatible | ✅ | ✅ |
| Ollama | ✅ | ✅ |
| Google (Gemini) | ✅ | ✅ |

---

## How it works

### Architecture

```
Browser / CLI
      │
      ▼
┌─────────────────┐      ┌───────┐
│   API Gateway   │◄────►│ Redis │
│   (FastAPI)     │      └───────┘
└────────┬────────┘
         │ enqueue job
         ▼
┌─────────────────┐
│     Worker      │  (ARQ background process)
│  (5-stage pipe) │
└────────┬────────┘
         │ write results
         ▼
┌─────────────────────────────────┐
│  ~/.autowiki/                   │
│    autowiki.db  (SQLite)        │
│    repos/<id>/faiss.index       │
│    repos/<id>/wiki/*.md         │
└─────────────────────────────────┘
```

The API gateway is stateless — it accepts requests, reads from SQLite, and pushes jobs onto a Redis queue. The worker runs the pipeline and writes results back to SQLite and disk. The Next.js frontend talks only to the API; it never touches the worker or storage directly.

### Pipeline (5 stages)

Each indexing job runs five stages in sequence:

**Stage 1 — Repo ingestion** (`worker/pipeline/ingestion.py`)
Shallow-clones the repository with GitPython and records the HEAD commit SHA. Files are filtered by extension and size (max 1 MB); binary files, vendored dependencies (`node_modules`, `.git`, `vendor`, etc.), and generated code are excluded.

**Stage 2 — AST analysis** (`worker/pipeline/ast_analysis.py`)
Every source file is parsed with Tree-Sitter to extract named entities — classes, functions, structs, interfaces. Results are grouped into a *module tree* (top-level directories become modules), which the wiki planner uses to decide page scope.

**Stage 3 — RAG indexing** (`worker/pipeline/rag_indexer.py`)
Source files are split into overlapping chunks with LangChain's `RecursiveCharacterTextSplitter`, embedded in batches by the configured embedding provider, and stored in a FAISS `IndexFlatIP` (inner-product / cosine similarity). The index and chunk metadata are persisted to disk so they survive restarts.

**Stage 4 — Wiki planning** (`worker/pipeline/wiki_planner.py`)
The LLM receives the module tree and produces a JSON page plan: a list of pages with titles, URL slugs, and the modules each page should cover. If the LLM output is invalid, the planner retries up to three times with the error appended to the prompt, then falls back to a flat one-page-per-module structure.

**Stage 5 — Page generation** (`worker/pipeline/page_generator.py`)
For each page in the plan, the page title and module list are embedded and used to retrieve the top-8 most relevant code chunks from the FAISS index. Those chunks, together with file paths, are assembled into a prompt and sent to the LLM, which writes a Markdown wiki page grounded in the actual source. Pages are stored in SQLite and written as `.md` files.

### Data flow (single indexing request)

```
POST /api/repos {"url": "github.com/owner/repo"}
  → validate URL, create Repository + Job rows (status=queued)
  → enqueue run_full_index on Redis
  → return {repo_id, job_id}           [202 Accepted]

Worker picks up job:
  Stage 1  clone/fetch → files[]           progress 5→20
  Stage 2  AST parse  → module_tree[]      progress   →35
  Stage 3  embed+index → FAISSStore        progress   →55
  Stage 4  LLM plan   → PagePlan           progress   →65
  Stage 5  per-page LLM → WikiPage rows    progress   →100

  Job status  → "done"
  Repo status → "ready"

GET /api/repos/{repo_id}/wiki        → list of {slug, title, page_order}
GET /api/repos/{repo_id}/wiki/{slug} → {title, content (Markdown)}

WS /ws/jobs/{job_id}                 → streams {progress, status} every second
```

---

## Roadmap

- **Phase 2** — Incremental refresh, Q&A chat, diagram synthesis, `.autowikiignore`
- **Phase 3** — Deep Research mode, MCP server
- **Phase 4** — GitHub webhooks, user-steered wiki structure
- **Phase 5** — GitLab/Bitbucket, hybrid search

---

## License

This project is licensed under the MIT License.
