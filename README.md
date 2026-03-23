# AutoWiki

Self-hosted, open-source AI-powered wiki generator for GitHub repositories. Point it at a repo and get a browsable, source-linked wiki in minutes — running entirely on your own machine with your own API keys.

## What it does

1. Clones the repository (shallow)
2. Parses source files with Tree-Sitter (Python, JS/TS, Java, Go, Rust, C/C++, C#)
3. Chunks and embeds code into a FAISS vector index
4. Asks an LLM to plan a hierarchical wiki structure
5. Generates each wiki page with RAG-retrieved context

The result is served via a REST API and displayed in a Next.js web UI with sidebar navigation.

## Quick start (local)

**Requirements:** Python 3.12+, Node.js 22+, Redis

```bash
# Install Python packages
pip install .

# Install and build the web UI
cd web && npm install && npm run build && cd ..

# Start Redis (if not already running)
redis-server --daemonize yes

# Start everything
autowiki serve
```

Or start services individually:

```bash
uvicorn api.main:app --port 3001 &
python -m worker.main &
cd web && npm start
```

## Quick start (Docker)

```bash
# Copy and fill in your API keys
cp autowiki.yml.example autowiki.yml

# Start everything
ANTHROPIC_API_KEY=sk-... docker-compose up
```

- Web UI: http://localhost:3000
- API: http://localhost:3001

Enter a GitHub URL in the form and click **Generate Wiki**. Progress streams in real time via WebSocket.

## Configuration

AutoWiki looks for config in this order (highest wins):

1. Environment variables
2. `autowiki.yml` in the current directory
3. `~/.autowiki/autowiki.yml`
4. Built-in defaults

### Environment Variables

| Variable | Description | Default | Example |
|---|---|---|---|
| `AUTOWIKI_LLM_PROVIDER` | LLM provider | `anthropic` | `google`, `openai`, `ollama` |
| `AUTOWIKI_LLM_MODEL` | LLM model name | `claude-sonnet-4-6` | `claude-3-5-sonnet-20240620` |
| `AUTOWIKI_LLM_API_KEY` | LLM API key | (empty) | `sk-ant-api03-...` |
| `AUTOWIKI_EMBEDDING_PROVIDER` | Embedding provider | `openai` | `google`, `ollama` |
| `AUTOWIKI_EMBEDDING_MODEL` | Embedding model name | `text-embedding-3-small` | `models/text-embedding-004` |
| `AUTOWIKI_EMBEDDING_API_KEY`| Embedding API key | (empty) | `AIzaSy...` |
| `REDIS_URL` | Redis connection URL | `redis://localhost:6379` | `redis://redis:6379` |
| `DATABASE_PATH` | Path to SQLite database | `~/.autowiki/autowiki.db` | `/data/autowiki.db` |
| `AUTOWIKI_DATA_DIR` | Directory for data storage | `~/.autowiki` | `/data` |

### YAML Configuration

```yaml
# autowiki.yml
llm:
  provider: anthropic          # anthropic | google | openai | openai-compatible | ollama
  model: claude-sonnet-4-6
  api_key: ${ANTHROPIC_API_KEY}

embedding:
  provider: google             # google | openai | ollama
  model: models/text-embedding-004
  api_key: ${GOOGLE_API_KEY}
```

Configure via CLI:

```bash
autowiki config show
autowiki config set llm.provider ollama
autowiki config set llm.model llama3.2
```

## CLI

```bash
# Index a repository
autowiki index github.com/owner/repo

# List indexed repositories
autowiki list

# Start the full stack
autowiki serve [--port 3000] [--api-port 3001]
```

## API

```
POST  /api/repos                         Submit a repo for indexing
GET   /api/repos                         List all repos
GET   /api/repos/{repo_id}               Repo status and metadata
GET   /api/repos/{repo_id}/wiki          List wiki pages
GET   /api/repos/{repo_id}/wiki/{slug}   Get a wiki page (Markdown)
GET   /api/jobs/{job_id}                 Job status and progress (0–100)
WS    /ws/jobs/{job_id}                  Stream job progress
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ --ignore=tests/e2e

# Run tests with coverage
pytest tests/ --ignore=tests/e2e --cov=worker --cov=api --cov=shared --cov-report=term-missing
```

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

## Supported LLM providers

| Provider | LLM | Embeddings |
|---|---|---|
| Anthropic | ✅ | — |
| OpenAI / compatible | ✅ | ✅ |
| Ollama | ✅ | ✅ |
| Google (Gemini) | ✅ | ✅ |

## Roadmap

- **Phase 2** — Incremental refresh, Q&A chat, diagram synthesis, `.autowikiignore`
- **Phase 3** — Deep Research mode, MCP server
- **Phase 4** — GitHub webhooks, user-steered wiki structure
- **Phase 5** — GitLab/Bitbucket, hybrid search

## License

This project is licensed under the MIT License.
