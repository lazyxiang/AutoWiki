# AutoWiki: AI-Powered Wiki Generator for Software Repositories

AutoWiki is a self-hosted, open-source tool that automatically generates comprehensive, browsable wikis for software repositories. By combining **Tree-Sitter AST analysis** with **RAG (Retrieval-Augmented Generation)**, it produces architecture overviews, module breakdowns, dependency diagrams, and source-linked documentation.

## 🚀 Project Overview

- **Goal:** Transform complex codebases into navigable, human-readable documentation.
- **Key Differentiator:** Combines deep structural analysis (AST) with semantic search (RAG) to maintain architectural context that traditional RAG-only tools lose.
- **Surfaces:** Web UI (Next.js), CLI (`autowiki` command), and MCP Server for AI IDE integration.

## 🛠 Tech Stack

- **Backend:** Python 3.12, FastAPI (API Gateway), ARQ (Async Job Queue with Redis).
- **AST Analysis:** Tree-Sitter (supporting Python, JS/TS, Java, Go, Rust, C/C++, C#).
- **RAG & Search:** LangChain, FAISS (Vector Store), OpenAI/Anthropic/Gemini/Ollama providers.
- **Frontend:** Next.js 16, React 19, Tailwind CSS 4, shadcn/ui.
- **Storage:** SQLite (Metadata), FAISS (Vectors), Markdown (Wiki Pages).
- **Deployment:** Docker Compose.

## 🏗 Architecture

AutoWiki uses a **Worker + API Gateway** split to handle long-running generation tasks asynchronously.

1.  **API Gateway (`api/`):** FastAPI app handling REST/WebSocket requests and enqueuing jobs.
2.  **Worker Service (`worker/`):** Executes the 6-stage generation pipeline:
    - **Ingestion:** Shallow clone and change detection.
    - **AST Analysis:** Language-aware parsing and dependency graph construction.
    - **RAG Indexing:** Semantic chunking and vector embedding.
    - **Wiki Planning:** LLM-driven hierarchical page planning.
    - **Page Generation:** Agentic LLM generation with RAG/AST context.
    - **Diagram Synthesis:** Automatic Mermaid diagram generation.

## 📖 Key Documentation

-   **PRD & Design Spec:** `docs/superpowers/specs/2026-03-22-autowiki-design.md` - Comprehensive technical architecture and product requirements.
-   **Implementation Plan:** `docs/superpowers/plans/2026-03-22-phase1-core-mvp.md` - Step-by-step TDD plan for building the MVP.

## 🛠 Development Conventions

### Project Structure
-   `shared/`: Common models, configuration, and database logic.
-   `api/`: FastAPI routes and queue management.
-   `worker/`: Pipeline stages, LLM/Embedding adapters, and background jobs.
-   `cli/`: Typer-based command-line interface.
-   `web/`: Next.js frontend application.
-   `tests/`: Comprehensive test suite (pytest, Playwright).

### Configuration
AutoWiki uses a hierarchical configuration discovery:
1.  Environment Variables (e.g., `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`).
2.  `autowiki.yml` in the current directory.
3.  `~/.autowiki/autowiki.yml` (Global).
4.  Built-in defaults.

### Quality Standards
-   **Testing:** Target ≥80% line coverage for Python services.
-   **TDD:** Implementation follows the failing-test-first approach documented in the Phase 1 plan.
-   **Linting/Formatting:** Usage of `ruff` or similar for Python and `prettier` for frontend.

## 📦 Building and Running

The project is managed via:
-   **Docker:** `docker-compose up --build`
-   **Local Development:**
    -   Backend: `pip install -e .` and `autowiki serve`
    -   Frontend: `cd web && npm install && npm run dev`
-   **Testing:** `pytest` and `cd web && npm test`
