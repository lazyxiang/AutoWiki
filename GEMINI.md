# AutoWiki: AI-Powered Wiki Generator for Software Repositories

AutoWiki is a self-hosted, open-source tool that automatically generates comprehensive, browsable wikis for software repositories. By combining **Tree-Sitter AST analysis** with **RAG (Retrieval-Augmented Generation)**, it produces architecture overviews, module breakdowns, dependency diagrams, and source-linked documentation.

## 🚀 Project Status
- **Phase 1 is complete** (tagged `v0.1.0-phase1`). Core pipeline (index + static wiki + REST API + web UI + CLI) is implemented and tested.
- **Phase 2 planning** is underway (Incremental refresh, Q&A chat, diagram synthesis).

## 🏗 Architecture

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

### Generation Pipeline (5 Stages — Phase 1)
1.  **Repo Ingestion** (`worker/pipeline/ingestion.py`): Shallow clone, file filtering, commit SHA.
2.  **AST Analysis** (`worker/pipeline/ast_analysis.py`): Tree-Sitter entity extraction (classes, functions), module tree.
3.  **RAG Indexing** (`worker/pipeline/rag_indexer.py`): LangChain chunking, FAISS IndexFlatIP (vector store).
4.  **Wiki Planning** (`worker/pipeline/wiki_planner.py`): LLM produces hierarchical JSON page plan with retry + fallback.
5.  **Page Generation** (`worker/pipeline/page_generator.py`): Agentic LLM generation with RAG-retrieved context.

Supported AST languages: Python, JavaScript/JSX, TypeScript/TSX, Java, Go, Rust, C, C++, C#.

## 🛠 Tech Stack

- **Backend:** Python 3.12, FastAPI (API Gateway), ARQ (Async Job Queue with Redis).
- **AST Analysis:** Tree-Sitter ≥0.23 API.
- **RAG & Search:** LangChain, FAISS (Vector Store), OpenAI/Anthropic/Gemini/Ollama providers.
- **Frontend:** Next.js 16.2.1, React 19, Tailwind CSS 4 (CSS-only), shadcn/ui.
- **Storage:** SQLite (Metadata), FAISS (Vectors), Markdown (Wiki Pages).
- **Deployment:** Docker Compose.

## 🛠 Development Conventions

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

### Key Implementation Notes
- **pydantic-settings v2**: Sub-model env_prefix isolation — no `env_nested_delimiter` on parent `Config`.
- **SQLAlchemy 2.0 async**: with aiosqlite; use `datetime.now(timezone.utc)` not `datetime.utcnow()`.
- **Next.js 16.2.1**: Tailwind v4 (CSS-only, no `tailwind.config.ts`), `@base-ui/react` not `@radix-ui/react`.
- **Gemini providers**: Migrated to `google-genai` (Phase 2). Dynamic dimension detection enabled for embeddings.
- **ARQ worker**: Blocking I/O must use `run_in_executor`.

### Quality Standards
- **Testing:** Target ≥80% line coverage for Python services. pytest with `asyncio_mode = "auto"`.
- **TDD:** Implementation follows the failing-test-first approach.
- **Linting/Formatting:** Usage of `ruff` for Python and `prettier` for frontend.

## 📖 Key Documentation
- **PRD & Design Spec:** `docs/superpowers/specs/2026-03-22-autowiki-design.md`
- **Phase 1 Plan:** `docs/superpowers/plans/2026-03-22-phase1-core-mvp.md`
- **Phase 2 Plan:** `docs/superpowers/plans/2026-03-23-phase2-chat-diagrams-refresh.md`

## 📦 Building and Running

- **Docker:** `docker-compose up --build`
- **Local Development:**
  - Backend: `pip install -e .` and `autowiki serve`
  - Frontend: `cd web && npm install && npm run dev`
- **Testing:** `pytest tests/ --ignore=tests/e2e`

## 🛣 Phased Delivery Roadmap
- **Phase 1** ✅: Core pipeline (index + static wiki + REST API + web UI + CLI).
- **Phase 2**: Incremental refresh + Q&A chat + `.autowikiignore` + diagram synthesis.
- **Phase 3**: Deep Research mode + MCP server.
- **Phase 4**: GitHub webhooks + user steering (`.autowiki/wiki.json`).
- **Phase 5**: GitLab/Bitbucket + hybrid search.
