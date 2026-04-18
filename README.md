# AutoWiki

Self-hosted, open-source AI-powered wiki generator for GitHub repositories. Point it at a repo and get a browsable, source-linked wiki in minutes — running entirely on your own machine with your own API keys.

## What it does

1. Clones the repository (shallow)
2. Parses source files with Tree-Sitter (Python, JS/TS, Java, Go, Rust, C/C++, C#)
3. Builds a file-level dependency graph from imports
4. Chunks and embeds code into a FAISS vector index
5. Asks an LLM to generate a logical page hierarchy with file assignments (two-phase: outline then file assignment)
6. Generates wiki pages bottom-up through a 4-pass pipeline — outline, draft, fact-check, and targeted revision — using a fast model for cheap passes and the main model for quality passes

The result is served via a REST API and displayed in a Next.js web UI with sidebar navigation and a conversational Q&A chat interface.

---

## Quick start

### Local

**Requirements:** Python 3.12+, Node.js 22+, Redis, and an API key (Anthropic, OpenAI, or Google)

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

**Requirements:** Docker, and an API key (Anthropic, OpenAI, or Google)

```bash
# 1. Build the images (required for first run or when source changes)
docker-compose build

# 2. Start everything (Anthropic LLM + OpenAI embeddings default)
ANTHROPIC_API_KEY=sk-ant-... OPENAI_API_KEY=sk-... docker-compose up

# Combined build and run
ANTHROPIC_API_KEY=sk-ant-... OPENAI_API_KEY=sk-... docker-compose up --build
```

#### Run with different providers

```bash
# OpenAI for everything
AUTOWIKI_LLM_PROVIDER=openai AUTOWIKI_LLM_MODEL=gpt-4o \
  OPENAI_API_KEY=sk-... docker-compose up

# Fully local with Ollama (point OLLAMA_HOST at your running instance)
AUTOWIKI_LLM_PROVIDER=ollama AUTOWIKI_LLM_MODEL=llama3.2 \
AUTOWIKI_EMBEDDING_PROVIDER=ollama AUTOWIKI_EMBEDDING_MODEL=nomic-embed-text \
OLLAMA_HOST=http://host.docker.internal:11434 docker-compose up

# Google Gemini for everything
AUTOWIKI_LLM_PROVIDER=google AUTOWIKI_LLM_MODEL=gemini-1.5-pro \
AUTOWIKI_EMBEDDING_PROVIDER=google AUTOWIKI_EMBEDDING_MODEL=models/text-embedding-004 \
GOOGLE_API_KEY=AIzaSy... docker-compose up
```

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
| `AUTOWIKI_LLM_FAST_MODEL` | *(same as LLM model)* | Faster/cheaper model for outline and fact-check passes (e.g. `claude-haiku-4-5`) |
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

# Re-index without rebuilding the FAISS vector index (faster, skips embedding)
autowiki index github.com/owner/repo --reuse-index

# List all indexed repositories
autowiki list

# Start the full stack (API + worker + web UI)
autowiki serve [--port 3000] [--api-port 3001]

# Run a deep research query against an indexed repo
autowiki research github.com/owner/repo "How does the authentication system work?"

# Inspect a stored wiki plan without running the pipeline
autowiki validate-plan owner-repo

# Show or update config
autowiki config show
autowiki config set <key> <value>  # Dot-separated key, e.g. llm.provider, embedding.model
```

---

## API

```text
POST  /api/repos                                      Submit a repo for indexing → {repo_id, job_id, status}
GET   /api/repos                                      List all repos
GET   /api/repos/{repo_id}                            Repo status and metadata
POST  /api/repos/{repo_id}/refresh                    Trigger incremental refresh → {job_id}
GET   /api/repos/{repo_id}/wiki                       List wiki pages (ordered)
GET   /api/repos/{repo_id}/wiki/{slug}                Get a wiki page (Markdown + metadata)
POST  /api/repos/{repo_id}/chat                       Create a new chat session → {session_id}
GET   /api/repos/{repo_id}/chat/{session_id}          Get chat history
POST  /api/repos/{repo_id}/research                   Start a deep research query → {job_id, report_id, status}
GET   /api/repos/{repo_id}/research/{job_id}          Get research report (plan, findings, Markdown)
GET   /api/jobs/{job_id}                              Job status and progress (0–100)
WS    /ws/jobs/{job_id}                               Stream {progress, status} until done/failed
WS    /ws/repos/{repo_id}/chat/{session_id}           Stream chat responses in real time
WS    /ws/repos/{repo_id}/research/{job_id}           Stream research events (plan/step/finding/report)
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

```text
AutoWiki/
├── api/                    # FastAPI gateway
│   ├── routers/            # REST endpoints (repos, jobs, wiki)
│   └── ws/                 # WebSocket job progress
├── worker/                 # ARQ background worker
│   ├── pipeline/           # 6-stage generation pipeline
│   ├── llm/                # LLM provider adapters
│   └── embedding/          # Embedding provider adapters
├── shared/                 # Config, SQLAlchemy models, database
├── cli/                    # Typer CLI (index, list, serve, config)
├── web/                    # Next.js 16 frontend
└── tests/                  # pytest suite (205 tests, 80% coverage)
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

```text
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
│  (6-stage pipe) │
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

### Pipeline (6 stages)

Each indexing job runs six stages in sequence:

**Stage 1 — Repo ingestion** (`worker/pipeline/ingestion.py`)
Shallow-clones the repository with GitPython and records the HEAD commit SHA. Files are filtered by extension and size (max 1 MB); binary files, vendored dependencies (`node_modules`, `.git`, `vendor`, etc.), and generated code are excluded.

**Stage 2 — AST analysis** (`worker/pipeline/ast_analysis.py`)
Every source file is parsed with Tree-Sitter in a single pass to extract named entities — classes, functions, structs, interfaces. Results are stored in a `FileAnalysis` structure (per-file entity lists with counts and summaries), which feeds all downstream stages.

**Stage 3 — Dependency graph** (`worker/pipeline/dependency_graph.py`)
Import statements are extracted from each file using language-specific regex patterns and resolved to known repo files. The result is a file-level dependency graph with connected-component clusters, used by the wiki planner to understand code relationships.

**Stage 4 — RAG indexing** (`worker/pipeline/rag_indexer.py`)
Source files are split into overlapping chunks with LangChain's `RecursiveCharacterTextSplitter`, embedded in batches by the configured embedding provider, and stored in a FAISS `IndexFlatIP` (inner-product / cosine similarity). Entity-aware chunking keeps whole functions/classes together when possible. Pass `--reuse-index` to skip this stage and reuse an existing index.

**Stage 5 — Wiki planning** (`worker/pipeline/wiki_planner.py`)
A two-phase LLM process: Phase 1 generates the page hierarchy (titles, purposes, parent relationships) informed by architectural anchors (directory tree, package docstrings, README headings); Phase 2 selects 5–8 representative source files per page (max 10) rather than assigning every file to one page. Each phase validates its output and self-retries with feedback; on final failure, `_heuristic_select_files` preserves valid pages and fills the remainder via scoring. The output is saved as `wiki.json` (user-facing) and `wiki_plan.json` (internal, with file mappings and `all_repo_files` for correct refresh coverage). Use `autowiki validate-plan <repo>` to inspect the plan offline.

**Stage 6 — Page generation** (`worker/pipeline/page_generator.py`)
Pages are generated bottom-up (leaf pages first, parent pages last) through a 4-pass pipeline per page:
- **Pass 1 — Outline** (fast model): produces a structured outline with planned sections, diagrams, and key claims to verify.
- **Pass 2 — Draft** (main model): generates full Markdown from the outline using multi-query RAG context.
- **Pass 3 — Fact-check** (fast model): verifies key claims and diagrams against source code.
- **Pass 4 — Revision** (main model, conditional): applies targeted fixes when fact-check fails; deterministic fallback strips any still-flagged issues.

Parent pages receive their children's rendered Markdown and synthesize an overview rather than duplicating content. Prompt caching reduces cost on repeated system prompts, and a configurable fast model (`AUTOWIKI_LLM_FAST_MODEL`) handles the cheap passes.

### Data flow (single indexing request)

```text
POST /api/repos {"url": "github.com/owner/repo"}
  → validate URL, create Repository + Job rows (status=queued)
  → enqueue run_full_index on Redis
  → return {repo_id, job_id}           [202 Accepted]

Worker picks up job:
  Stage 1  clone/fetch → files[]            progress 5→20
  Stage 2  AST parse  → FileAnalysis        progress   →35
  Stage 3  dep graph  → DependencyGraph     progress   →45  (internal; no API surface)
  Stage 4  embed+index → FAISSStore         progress   →55  (skipped with --reuse-index)
  Stage 5  two-phase LLM plan → WikiPlan    progress   →70
  Stage 6  bottom-up batch LLM → WikiPages  progress   →100

  Job status  → "done"
  Repo status → "ready"

GET /api/repos/{repo_id}/wiki        → list of {slug, title, page_order}
GET /api/repos/{repo_id}/wiki/{slug} → {title, content (Markdown)}

WS /ws/jobs/{job_id}                 → streams {progress, status} every second
```

---

## Roadmap

- **Phase 1** ✅ — Core pipeline (index + static wiki + REST API + web UI + CLI)
- **Phase 2** ✅ — Incremental refresh, Q&A chat, dependency diagrams
- **Phase 2.5** ✅ — Wiki quality enhancements: two-phase planner, 4-pass page generation, prompt caching, fast model support, RAG tuning, diagram post-processing
- **Phase 3** ✅ — Deep Research mode: multi-step RAG investigation with LLM planner, per-step AST context, synthesized Markdown report; REST + WebSocket API; `autowiki research` CLI command (PR #20)
- **Phase 4** ✅ — User-steered wiki structure via `.autowiki/wiki.json`: override page hierarchy, assign modules to pages, inject repo/page notes into generation (PR #20)
- **Phase 4.5** ✅ — Planner robustness hardening (PR #22): architectural anchors in Phase-1 outline prompt (Layer C1), multi-page file assignment with `secondary_files` (Layer C2), `autowiki validate-plan` offline diagnostic CLI, feedback-retry loop in `_assign_files`, various bug fixes (Gemini JSON, Mermaid, Docker)
- **Phase 4.6** ✅ — Page-centric file selection (PR #23): Phase 2 replaced from file-centric assignment to page-centric selection (5–8 files per page); scoring-based pre-filter + fallback (`_score_file_for_page`, `_heuristic_select_files`); `WikiPlan.all_repo_files` for correct refresh coverage; orphan enforcement removed
- **Phase 5** — GitLab/Bitbucket, hybrid search, MCP server

---

## License

This project is licensed under the MIT License.
