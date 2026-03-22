# AutoWiki Phase 1 — Core MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a working AutoWiki instance that clones a GitHub repo, runs the 5-stage generation pipeline (ingestion → AST → RAG → planning → page generation), serves the wiki via REST API, and displays it in a Next.js web UI — all orchestrated via Docker Compose.

**Architecture:** Worker + API Gateway split. The FastAPI API Gateway enqueues jobs to Redis; the ARQ Worker executes the 5-stage pipeline asynchronously. Both share a SQLite database and a FAISS vector store on a shared Docker volume. The Next.js frontend talks only to the API Gateway.

**Tech Stack:** Python 3.12, FastAPI, ARQ (Redis job queue), Tree-Sitter, LangChain text splitter, FAISS, SQLite, Next.js 15, Tailwind CSS, shadcn/ui, Docker Compose, pytest, Playwright.

---

## File Map

```
AutoWiki/
├── docker-compose.yml
├── pyproject.toml                  # Python monorepo (api + worker + cli)
├── autowiki.yml.example
│
├── shared/                         # Imported by both api/ and worker/
│   ├── __init__.py
│   ├── config.py                   # Config loading: env → cwd yaml → ~/.autowiki/yaml → defaults
│   ├── models.py                   # SQLAlchemy models + Pydantic schemas
│   └── database.py                 # SQLite engine, session factory, migrations
│
├── worker/
│   ├── __init__.py
│   ├── main.py                     # ARQ worker entrypoint (function registry)
│   ├── jobs.py                     # full_index job: orchestrates pipeline stages
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── ingestion.py            # Stage 1: clone/fetch, file filter, commit SHA
│   │   ├── ast_analysis.py         # Stage 2: Tree-Sitter parsing, dep graph, module tree
│   │   ├── rag_indexer.py          # Stage 3: chunk, embed, build/update FAISS index
│   │   ├── wiki_planner.py         # Stage 4: LLM → hierarchical JSON page plan
│   │   └── page_generator.py       # Stage 5: per-page RAG retrieval + LLM generation
│   ├── llm/
│   │   ├── __init__.py             # make_llm_provider() factory
│   │   ├── base.py                 # LLMProvider abstract base
│   │   ├── anthropic_provider.py   # Anthropic adapter
│   │   ├── openai_provider.py      # OpenAI + openai-compatible adapter
│   │   └── ollama_provider.py      # Ollama adapter
│   └── embedding/
│       ├── __init__.py             # make_embedding_provider() factory
│       ├── base.py                 # EmbeddingProvider abstract base
│       ├── openai_embed.py         # OpenAI embeddings
│       └── ollama_embed.py         # Ollama embeddings
│
├── api/
│   ├── __init__.py
│   ├── main.py                     # FastAPI app, startup, CORS
│   ├── queue.py                    # ARQ Redis pool, enqueue helpers
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── repos.py                # POST /api/repos, GET /api/repos/{id}
│   │   ├── wiki.py                 # GET /api/repos/{id}/wiki, GET .../wiki/{slug}
│   │   └── jobs.py                 # GET /api/jobs/{id}
│   └── ws/
│       ├── __init__.py
│       └── jobs.py                 # WS /ws/jobs/{job_id} — streams progress 0-100
│
├── cli/
│   ├── __init__.py
│   ├── main.py                     # Typer app entrypoint
│   └── commands/
│       ├── __init__.py
│       ├── index.py                # autowiki index <url> [--force]
│       ├── list_repos.py           # autowiki list
│       ├── serve.py                # autowiki serve [--port]
│       └── config_cmd.py           # autowiki config show / set
│
├── web/
│   ├── package.json
│   ├── next.config.ts
│   ├── tailwind.config.ts
│   ├── components.json             # shadcn/ui config
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx                # Home: IndexForm
│   │   ├── repos/
│   │   │   └── page.tsx            # /repos list
│   │   └── [owner]/
│   │       └── [repo]/
│   │           ├── page.tsx        # Wiki index
│   │           └── [slug]/
│   │               └── page.tsx    # Individual wiki page
│   ├── components/
│   │   ├── IndexForm.tsx
│   │   ├── JobProgressBar.tsx
│   │   ├── WikiSidebar.tsx
│   │   └── WikiPage.tsx
│   └── lib/
│       ├── api.ts                  # REST API client (typed fetch wrappers)
│       └── ws.ts                   # WebSocket hook for job progress
│   └── app/
│       └── layout.tsx              # Root layout — dark mode, font
│
└── tests/
    ├── conftest.py                 # Shared fixtures: test DB, mock LLM, fixture repo
    ├── fixtures/
    │   └── simple-repo/            # Tiny fixture repo (3 Python files, known structure)
    │       ├── main.py
    │       ├── utils.py
    │       └── models.py
    ├── worker/
    │   ├── test_ingestion.py
    │   ├── test_ast_analysis.py
    │   ├── test_rag_indexer.py
    │   ├── test_wiki_planner.py
    │   └── test_page_generator.py
    ├── api/
    │   ├── test_repos.py
    │   ├── test_wiki.py
    │   ├── test_jobs.py
    │   └── test_ws.py
    ├── cli/
    │   └── test_cli.py
    └── e2e/
        └── test_index_flow.py      # Playwright: home → index → wiki page visible
```

---

## Task 1: Project Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `docker-compose.yml`
- Create: `autowiki.yml.example`
- Create: `.gitignore` (extend existing)
- Create: `web/package.json`
- Create: `web/next.config.ts`
- Create: `web/tailwind.config.ts`
- Create: `web/components.json`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "autowiki"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "arq>=0.26",
    "redis>=5.0",
    "sqlalchemy>=2.0",
    "aiosqlite>=0.20",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "pyyaml>=6.0",
    "gitpython>=3.1",
    "tree-sitter>=0.23",
    "tree-sitter-python>=0.23",
    "tree-sitter-javascript>=0.23",
    "tree-sitter-typescript>=0.23",
    "tree-sitter-java>=0.23",
    "tree-sitter-go>=0.23",
    "tree-sitter-c>=0.23",
    "tree-sitter-cpp>=0.23",
    "tree-sitter-c-sharp>=0.23",
    "langchain-text-splitters>=0.3",
    "faiss-cpu>=1.8",
    "anthropic>=0.40",
    "openai>=1.50",
    "typer>=0.12",
    "httpx>=0.27",
    "websockets>=13.0",
]

[project.scripts]
autowiki = "cli.main:app"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "httpx>=0.27",
    "pytest-cov>=5.0",
    "playwright>=1.48",
    "pytest-playwright>=0.5",
]
```

- [ ] **Step 2: Create `docker-compose.yml`**

```yaml
services:
  api:
    build:
      context: .
      dockerfile: api/Dockerfile
    ports:
      - "127.0.0.1:3001:3001"
    environment:
      - REDIS_URL=redis://redis:6379
      - DATABASE_PATH=/data/autowiki.db
      - AUTOWIKI_DATA_DIR=/data
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
      - OPENAI_API_KEY=${OPENAI_API_KEY:-}
    volumes:
      - autowiki_data:/data
    depends_on:
      redis:
        condition: service_healthy

  worker:
    build:
      context: .
      dockerfile: worker/Dockerfile
    environment:
      - REDIS_URL=redis://redis:6379
      - DATABASE_PATH=/data/autowiki.db
      - AUTOWIKI_DATA_DIR=/data
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
      - OPENAI_API_KEY=${OPENAI_API_KEY:-}
    volumes:
      - autowiki_data:/data
    depends_on:
      redis:
        condition: service_healthy

  web:
    build:
      context: ./web
      dockerfile: Dockerfile
    ports:
      - "127.0.0.1:3000:3000"
    environment:
      # Browser-side (baked into client bundle at build time)
      - NEXT_PUBLIC_API_URL=http://localhost:3001
      # Server-side SSR calls within Docker network
      - INTERNAL_API_URL=http://api:3001

  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

volumes:
  autowiki_data:
```

- [ ] **Step 3: Create `autowiki.yml.example`**

```yaml
# Copy to autowiki.yml and fill in your values.
# Environment variables take precedence over this file.

llm:
  provider: anthropic          # anthropic | openai | openai-compatible | ollama
  model: claude-sonnet-4-6
  api_key: ${ANTHROPIC_API_KEY}
  # base_url: http://localhost:11434/v1  # only for openai-compatible / ollama

embedding:
  provider: openai             # openai | ollama
  model: text-embedding-3-small
  api_key: ${OPENAI_API_KEY}

server:
  host: 127.0.0.1              # Change to 0.0.0.0 with caution — enables network exposure
  port: 3001
  # auth_token: ""             # Optional bearer token for network-exposed instances

chat:
  history_window: 10           # Number of prior turns injected into LLM context
```

- [ ] **Step 4: Bootstrap the web app**

```bash
cd web
npx create-next-app@latest . --typescript --tailwind --app --src-dir=no --import-alias="@/*" --yes
npx shadcn@latest init --defaults
npx shadcn@latest add button input card badge progress separator scroll-area
```

- [ ] **Step 5: Create stub Dockerfiles**

`api/Dockerfile`:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]" 2>/dev/null || pip install --no-cache-dir -e .
COPY shared/ ./shared/
COPY api/ ./api/
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "3001"]
```

`worker/Dockerfile`:
```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .
COPY shared/ ./shared/
COPY worker/ ./worker/
CMD ["python", "-m", "worker.main"]
```

`web/Dockerfile`:
```dockerfile
FROM node:22-alpine AS builder
WORKDIR /app
COPY package*.json .
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
CMD ["node", "server.js"]
```

- [ ] **Step 6: Create stub `__init__.py` files for all Python packages**

```bash
mkdir -p shared worker/pipeline worker/llm worker/embedding api/routers api/ws cli/commands tests/worker tests/api tests/e2e tests/fixtures/simple-repo
touch shared/__init__.py worker/__init__.py worker/pipeline/__init__.py worker/llm/__init__.py worker/embedding/__init__.py api/__init__.py api/routers/__init__.py api/ws/__init__.py cli/__init__.py cli/commands/__init__.py
```

- [ ] **Step 7: Create fixture repo**

```bash
cat > tests/fixtures/simple-repo/models.py << 'EOF'
class User:
    def __init__(self, name: str, email: str):
        self.name = name
        self.email = email

class Post:
    def __init__(self, title: str, author: User):
        self.title = title
        self.author = author
EOF

cat > tests/fixtures/simple-repo/utils.py << 'EOF'
from .models import User

def greet(user: User) -> str:
    return f"Hello, {user.name}!"

def validate_email(email: str) -> bool:
    return "@" in email and "." in email
EOF

cat > tests/fixtures/simple-repo/main.py << 'EOF'
from .models import User
from .utils import greet, validate_email

def run():
    user = User("Alice", "alice@example.com")
    if validate_email(user.email):
        print(greet(user))
EOF
```

- [ ] **Step 8: Commit scaffold**

```bash
git add .
git commit -m "chore: project scaffold — docker-compose, pyproject, web bootstrap, fixture repo"
```

---

## Task 2: Shared Config

**Files:**
- Create: `shared/config.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import os
import pytest
from shared.config import Config

def test_defaults():
    cfg = Config()
    assert cfg.llm.provider == "anthropic"
    assert cfg.llm.model == "claude-sonnet-4-6"
    assert cfg.server.host == "127.0.0.1"
    assert cfg.chat.history_window == 10

def test_env_override(monkeypatch):
    monkeypatch.setenv("AUTOWIKI_LLM_PROVIDER", "openai")
    monkeypatch.setenv("AUTOWIKI_LLM_MODEL", "gpt-4o")
    cfg = Config()
    assert cfg.llm.provider == "openai"
    assert cfg.llm.model == "gpt-4o"
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_config.py -v
```
Expected: `ModuleNotFoundError: No module named 'shared'`

- [ ] **Step 3: Implement `shared/config.py`**

```python
from __future__ import annotations
import os
from pathlib import Path
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class LLMConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOWIKI_LLM_")
    provider: Literal["anthropic", "openai", "openai-compatible", "ollama"] = "anthropic"
    model: str = "claude-sonnet-4-6"
    api_key: str = ""
    base_url: str = ""

class EmbeddingConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOWIKI_EMBEDDING_")
    provider: Literal["openai", "ollama"] = "openai"
    model: str = "text-embedding-3-small"
    api_key: str = ""

class ServerConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOWIKI_SERVER_")
    host: str = "127.0.0.1"
    port: int = 3001
    auth_token: str = ""

class ChatConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOWIKI_CHAT_")
    history_window: int = 10

class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUTOWIKI_",
        env_nested_delimiter="__",
    )
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    chat: ChatConfig = Field(default_factory=ChatConfig)
    data_dir: Path = Field(
        default_factory=lambda: Path(os.environ.get("AUTOWIKI_DATA_DIR", Path.home() / ".autowiki"))
    )
    database_path: Path = Field(
        default_factory=lambda: Path(os.environ.get("DATABASE_PATH", Path.home() / ".autowiki" / "autowiki.db"))
    )

_config: Config | None = None

def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_config.py -v
```
Expected: 2 PASSED

- [ ] **Step 5: Create `tests/conftest.py` with shared fixtures**

```python
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "simple-repo"

@pytest.fixture
def fixture_repo_path():
    return FIXTURE_REPO

@pytest.fixture
def mock_llm():
    """Returns a mock LLMProvider that returns predictable content."""
    m = AsyncMock()
    m.generate.return_value = "Mocked wiki page content."
    m.generate_structured.return_value = {
        "pages": [
            {"title": "Overview", "slug": "overview", "modules": ["."]},
            {"title": "Models", "slug": "models", "modules": ["models.py"]},
            {"title": "Utils", "slug": "utils", "modules": ["utils.py"]},
        ]
    }
    return m

@pytest.fixture
def mock_embedding():
    """Returns a mock EmbeddingProvider that returns zero vectors."""
    import numpy as np
    m = AsyncMock()
    m.embed.return_value = np.zeros(1536, dtype="float32")
    m.embed_batch.side_effect = lambda texts: [np.zeros(1536, dtype="float32") for _ in texts]
    return m
```

- [ ] **Step 6: Commit**

```bash
git add shared/config.py tests/test_config.py tests/conftest.py
git commit -m "feat: shared config with env/yaml precedence and pydantic-settings"
```

---

## Task 3: SQLite Database

**Files:**
- Create: `shared/database.py`
- Create: `shared/models.py`
- Create: `tests/test_database.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_database.py
import pytest
import asyncio
from pathlib import Path
from shared.database import init_db, get_session
from shared.models import Repository, Job, WikiPage

@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(str(db_path))
    return db_path

async def test_create_repository(db):
    async with get_session(str(db)) as session:
        repo = Repository(
            id="abc123",
            owner="testowner",
            name="testrepo",
            platform="github",
            status="pending",
        )
        session.add(repo)
        await session.commit()

    async with get_session(str(db)) as session:
        result = await session.get(Repository, "abc123")
        assert result.owner == "testowner"
        assert result.status == "pending"

async def test_create_job(db):
    async with get_session(str(db)) as session:
        repo = Repository(id="r1", owner="o", name="n", status="pending")
        job = Job(id="j1", repo_id="r1", type="full_index", status="queued", progress=0)
        session.add(repo)
        session.add(job)
        await session.commit()

    async with get_session(str(db)) as session:
        result = await session.get(Job, "j1")
        assert result.status == "queued"
        assert result.progress == 0
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_database.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement `shared/models.py`**

```python
from __future__ import annotations
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class Repository(Base):
    __tablename__ = "repositories"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    platform: Mapped[str] = mapped_column(String, default="github")
    last_commit: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    wiki_path: Mapped[str | None] = mapped_column(String, nullable=True)
    jobs: Mapped[list[Job]] = relationship("Job", back_populates="repository")
    pages: Mapped[list[WikiPage]] = relationship("WikiPage", back_populates="repository")

class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    repository: Mapped[Repository] = relationship("Repository", back_populates="jobs")

class WikiPage(Base):
    __tablename__ = "wiki_pages"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page_order: Mapped[int] = mapped_column(Integer, default=0)
    parent_slug: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    repository: Mapped[Repository] = relationship("Repository", back_populates="pages")
```

- [ ] **Step 4: Implement `shared/database.py`**

```python
from __future__ import annotations
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from shared.models import Base

_engines: dict[str, any] = {}
_session_factories: dict[str, async_sessionmaker] = {}

async def init_db(database_path: str) -> None:
    url = f"sqlite+aiosqlite:///{database_path}"
    engine = create_async_engine(url, echo=False)
    _engines[database_path] = engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    _session_factories[database_path] = async_sessionmaker(engine, expire_on_commit=False)

@asynccontextmanager
async def get_session(database_path: str):
    factory = _session_factories[database_path]
    async with factory() as session:
        yield session
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_database.py -v
```
Expected: 2 PASSED

- [ ] **Step 6: Commit**

```bash
git add shared/models.py shared/database.py tests/test_database.py
git commit -m "feat: SQLite schema with SQLAlchemy async (repositories, jobs, wiki_pages)"
```

---

## Task 4: LLM Provider Abstraction

**Files:**
- Create: `worker/llm/base.py`
- Create: `worker/llm/anthropic_provider.py`
- Create: `worker/llm/openai_provider.py`
- Create: `worker/llm/ollama_provider.py`
- Create: `tests/worker/test_llm.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/worker/test_llm.py
import pytest
from unittest.mock import AsyncMock, patch
from worker.llm.base import LLMProvider
from worker.llm.anthropic_provider import AnthropicProvider

def test_provider_is_abstract():
    with pytest.raises(TypeError):
        LLMProvider()

async def test_anthropic_generate_calls_api():
    provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-6")
    with patch.object(provider._client.messages, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = AsyncMock(content=[AsyncMock(text="Hello")])
        result = await provider.generate("Say hello")
    assert result == "Hello"

async def test_anthropic_generate_structured_returns_dict():
    provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-6")
    raw = '{"pages": [{"title": "Overview", "slug": "overview", "modules": ["."]}]}'
    with patch.object(provider._client.messages, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = AsyncMock(content=[AsyncMock(text=raw)])
        result = await provider.generate_structured("Make a plan", schema={"type": "object"})
    assert result["pages"][0]["slug"] == "overview"
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/worker/test_llm.py -v
```

- [ ] **Step 3: Implement `worker/llm/base.py`**

```python
from abc import ABC, abstractmethod
from typing import Any

class LLMProvider(ABC):
    @abstractmethod
    async def generate(self, prompt: str, system: str = "") -> str:
        """Generate text from a prompt. Returns the full response string."""

    @abstractmethod
    async def generate_structured(self, prompt: str, schema: dict[str, Any], system: str = "") -> dict[str, Any]:
        """Generate and parse a JSON response matching the given schema."""

    @abstractmethod
    async def generate_stream(self, prompt: str, system: str = ""):
        """Async generator that yields text chunks as they arrive."""
```

- [ ] **Step 4: Implement `worker/llm/anthropic_provider.py`**

```python
from __future__ import annotations
import json
from typing import Any, AsyncIterator
import anthropic
from worker.llm.base import LLMProvider

class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def generate(self, prompt: str, system: str = "") -> str:
        kwargs: dict = {"model": self._model, "max_tokens": 8192,
                        "messages": [{"role": "user", "content": prompt}]}
        if system:
            kwargs["system"] = system
        response = await self._client.messages.create(**kwargs)
        return response.content[0].text

    async def generate_structured(self, prompt: str, schema: dict[str, Any], system: str = "") -> dict[str, Any]:
        json_prompt = f"{prompt}\n\nRespond ONLY with valid JSON matching this schema:\n{json.dumps(schema)}"
        raw = await self.generate(json_prompt, system=system)
        # Strip markdown code fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(raw)

    async def generate_stream(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        kwargs: dict = {"model": self._model, "max_tokens": 8192,
                        "messages": [{"role": "user", "content": prompt}]}
        if system:
            kwargs["system"] = system
        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text
```

- [ ] **Step 5: Implement `worker/llm/openai_provider.py`**

```python
from __future__ import annotations
import json
from typing import Any, AsyncIterator
from openai import AsyncOpenAI
from worker.llm.base import LLMProvider

class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gpt-4o", base_url: str | None = None):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
        self._model = model

    async def generate(self, prompt: str, system: str = "") -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = await self._client.chat.completions.create(
            model=self._model, messages=messages, max_tokens=8192
        )
        return response.choices[0].message.content

    async def generate_structured(self, prompt: str, schema: dict[str, Any], system: str = "") -> dict[str, Any]:
        json_prompt = f"{prompt}\n\nRespond ONLY with valid JSON."
        raw = await self.generate(json_prompt, system=system)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(raw)

    async def generate_stream(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        stream = await self._client.chat.completions.create(
            model=self._model, messages=messages, max_tokens=8192, stream=True
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
```

- [ ] **Step 6: Implement `worker/llm/ollama_provider.py`**

```python
from __future__ import annotations
import json
from typing import Any, AsyncIterator
import httpx
from worker.llm.base import LLMProvider

class OllamaProvider(LLMProvider):
    def __init__(self, model: str = "llama3", base_url: str = "http://localhost:11434"):
        self._model = model
        self._base_url = base_url.rstrip("/")

    async def generate(self, prompt: str, system: str = "") -> str:
        payload = {"model": self._model, "prompt": prompt, "stream": False}
        if system:
            payload["system"] = system
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{self._base_url}/api/generate", json=payload)
            resp.raise_for_status()
        return resp.json()["response"]

    async def generate_structured(self, prompt: str, schema: dict[str, Any], system: str = "") -> dict[str, Any]:
        json_prompt = f"{prompt}\n\nRespond ONLY with valid JSON."
        raw = await self.generate(json_prompt, system=system)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(raw)

    async def generate_stream(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        payload = {"model": self._model, "prompt": prompt, "stream": True}
        if system:
            payload["system"] = system
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", f"{self._base_url}/api/generate", json=payload) as resp:
                async for line in resp.aiter_lines():
                    if line:
                        data = json.loads(line)
                        yield data.get("response", "")
```

- [ ] **Step 6b: Implement `worker/llm/__init__.py` — factory function**

```python
# worker/llm/__init__.py
from __future__ import annotations
import os
from worker.llm.base import LLMProvider

def make_llm_provider(cfg) -> LLMProvider:
    """Factory: create LLMProvider from config. Import here so worker/jobs.py patches cleanly."""
    from worker.llm.anthropic_provider import AnthropicProvider
    from worker.llm.openai_provider import OpenAIProvider
    from worker.llm.ollama_provider import OllamaProvider
    p = cfg.llm.provider
    if p == "anthropic":
        return AnthropicProvider(
            api_key=cfg.llm.api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
            model=cfg.llm.model,
        )
    elif p in ("openai", "openai-compatible"):
        return OpenAIProvider(
            api_key=cfg.llm.api_key or os.environ.get("OPENAI_API_KEY", ""),
            model=cfg.llm.model,
            base_url=cfg.llm.base_url or None,
        )
    elif p == "ollama":
        return OllamaProvider(
            model=cfg.llm.model,
            base_url=cfg.llm.base_url or "http://localhost:11434",
        )
    else:
        raise ValueError(f"Unknown LLM provider: {p}")
```

- [ ] **Step 7: Run tests**

```bash
pytest tests/worker/test_llm.py -v
```
Expected: 3 PASSED

- [ ] **Step 8: Commit**

```bash
git add worker/llm/ tests/worker/test_llm.py
git commit -m "feat: LLM provider abstraction (Anthropic, OpenAI, Ollama)"
```

---

## Task 5: Embedding Provider Abstraction

**Files:**
- Create: `worker/embedding/base.py`
- Create: `worker/embedding/openai_embed.py`
- Create: `worker/embedding/ollama_embed.py`
- Create: `tests/worker/test_embedding.py`

- [ ] **Step 1: Write failing test**

```python
# tests/worker/test_embedding.py
import pytest
import numpy as np
from unittest.mock import AsyncMock, patch
from worker.embedding.openai_embed import OpenAIEmbedding

async def test_embed_returns_float32_array():
    provider = OpenAIEmbedding(api_key="test-key")
    fake_vector = [0.1] * 1536
    with patch.object(provider._client.embeddings, "create", new_callable=AsyncMock) as mock:
        mock.return_value = AsyncMock(data=[AsyncMock(embedding=fake_vector)])
        result = await provider.embed("hello world")
    assert isinstance(result, np.ndarray)
    assert result.dtype == np.float32
    assert result.shape == (1536,)

async def test_embed_batch_returns_list():
    provider = OpenAIEmbedding(api_key="test-key")
    fake_vector = [0.0] * 1536
    with patch.object(provider._client.embeddings, "create", new_callable=AsyncMock) as mock:
        mock.return_value = AsyncMock(data=[AsyncMock(embedding=fake_vector), AsyncMock(embedding=fake_vector)])
        result = await provider.embed_batch(["a", "b"])
    assert len(result) == 2
    assert all(isinstance(v, np.ndarray) for v in result)
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/worker/test_embedding.py -v
```

- [ ] **Step 3: Implement `worker/embedding/base.py`**

```python
from abc import ABC, abstractmethod
import numpy as np

class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, text: str) -> np.ndarray:
        """Embed a single text. Returns float32 numpy array."""

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        """Embed multiple texts. Returns list of float32 arrays."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Embedding vector dimension."""
```

- [ ] **Step 4: Implement `worker/embedding/openai_embed.py`**

```python
from __future__ import annotations
import numpy as np
from openai import AsyncOpenAI
from worker.embedding.base import EmbeddingProvider

class OpenAIEmbedding(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._dim = 1536 if "small" in model else 3072

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed(self, text: str) -> np.ndarray:
        response = await self._client.embeddings.create(input=[text], model=self._model)
        return np.array(response.data[0].embedding, dtype=np.float32)

    async def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        if not texts:
            return []
        response = await self._client.embeddings.create(input=texts, model=self._model)
        return [np.array(d.embedding, dtype=np.float32) for d in response.data]

def make_embedding_provider(cfg) -> EmbeddingProvider:
    from worker.embedding.ollama_embed import OllamaEmbedding
    p = cfg.embedding.provider
    if p == "openai":
        return OpenAIEmbedding(
            api_key=cfg.embedding.api_key or __import__("os").environ.get("OPENAI_API_KEY", ""),
            model=cfg.embedding.model,
        )
    elif p == "ollama":
        return OllamaEmbedding(model=cfg.embedding.model)
    else:
        raise ValueError(f"Unknown embedding provider: {p}")
```

- [ ] **Step 5: Implement `worker/embedding/ollama_embed.py`**

```python
from __future__ import annotations
import numpy as np
import httpx
from worker.embedding.base import EmbeddingProvider

class OllamaEmbedding(EmbeddingProvider):
    def __init__(self, model: str = "nomic-embed-text", base_url: str = "http://localhost:11434"):
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._dim = 768  # nomic-embed-text default; adjustable

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed(self, text: str) -> np.ndarray:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{self._base_url}/api/embeddings",
                                     json={"model": self._model, "prompt": text})
            resp.raise_for_status()
        return np.array(resp.json()["embedding"], dtype=np.float32)

    async def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        return [await self.embed(t) for t in texts]
```

- [ ] **Step 5b: Implement `worker/embedding/__init__.py` — factory function**

```python
# worker/embedding/__init__.py
from __future__ import annotations
import os
from worker.embedding.base import EmbeddingProvider

def make_embedding_provider(cfg) -> EmbeddingProvider:
    """Factory: create EmbeddingProvider from config. Import here so worker/jobs.py patches cleanly."""
    from worker.embedding.openai_embed import OpenAIEmbedding
    from worker.embedding.ollama_embed import OllamaEmbedding
    p = cfg.embedding.provider
    if p == "openai":
        return OpenAIEmbedding(
            api_key=cfg.embedding.api_key or os.environ.get("OPENAI_API_KEY", ""),
            model=cfg.embedding.model,
        )
    elif p == "ollama":
        return OllamaEmbedding(model=cfg.embedding.model)
    else:
        raise ValueError(f"Unknown embedding provider: {p}")
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/worker/test_embedding.py -v
```
Expected: 2 PASSED

- [ ] **Step 7: Commit**

```bash
git add worker/embedding/ tests/worker/test_embedding.py
git commit -m "feat: embedding provider abstraction (OpenAI, Ollama)"
```

---

## Task 6: Stage 1 — Repo Ingestion

**Files:**
- Create: `worker/pipeline/ingestion.py`
- Create: `tests/worker/test_ingestion.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/worker/test_ingestion.py
import pytest
from pathlib import Path
from worker.pipeline.ingestion import filter_files, get_repo_hash, parse_github_url

def test_parse_github_url():
    owner, name = parse_github_url("https://github.com/psf/requests")
    assert owner == "psf"
    assert name == "requests"

def test_parse_github_url_without_scheme():
    owner, name = parse_github_url("github.com/psf/requests")
    assert owner == "psf"
    assert name == "requests"

def test_get_repo_hash_is_deterministic():
    h1 = get_repo_hash("github", "psf", "requests")
    h2 = get_repo_hash("github", "psf", "requests")
    assert h1 == h2
    assert len(h1) == 16  # truncated sha256

def test_filter_files_excludes_binaries(tmp_path):
    (tmp_path / "main.py").write_text("print('hello')")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lib.js").write_text("// lib")
    files = filter_files(tmp_path)
    paths = [f.name for f in files]
    assert "main.py" in paths
    assert "image.png" not in paths
    assert "lib.js" not in paths

def test_filter_files_respects_size_limit(tmp_path):
    small = tmp_path / "small.py"
    large = tmp_path / "large.py"
    small.write_text("x = 1")
    large.write_bytes(b"x" * (2 * 1024 * 1024))  # 2MB > 1MB limit
    files = filter_files(tmp_path, max_file_bytes=1024 * 1024)
    assert small in files
    assert large not in files
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/worker/test_ingestion.py -v
```

- [ ] **Step 3: Implement `worker/pipeline/ingestion.py`**

```python
from __future__ import annotations
import hashlib
from pathlib import Path

# Extensions considered source code (non-exhaustive, practical set)
SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go",
    ".c", ".h", ".cpp", ".cc", ".hpp", ".cs", ".rs",
    ".rb", ".php", ".swift", ".kt", ".scala", ".r",
    ".sh", ".bash", ".yaml", ".yml", ".toml", ".json",
    ".md", ".rst", ".txt", ".sql", ".graphql", ".proto",
}

EXCLUDED_DIRS = {
    "node_modules", ".git", "__pycache__", ".pytest_cache",
    "venv", ".venv", "env", "dist", "build", "target",
    ".next", ".nuxt", "vendor", "third_party", ".gradle",
    "coverage", ".coverage", "htmlcov",
}

def parse_github_url(url: str) -> tuple[str, str]:
    """Parse 'github.com/owner/repo' or full URL into (owner, name)."""
    url = url.replace("https://", "").replace("http://", "").rstrip("/")
    parts = url.split("/")
    # Find 'github.com' and take the next two parts
    try:
        idx = next(i for i, p in enumerate(parts) if "github.com" in p)
        return parts[idx + 1], parts[idx + 2].removesuffix(".git")
    except (StopIteration, IndexError):
        raise ValueError(f"Cannot parse GitHub URL: {url}")

def get_repo_hash(platform: str, owner: str, name: str) -> str:
    key = f"{platform}:{owner}/{name}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]

def filter_files(
    root: Path,
    max_file_bytes: int = 1024 * 1024,  # 1MB per file
) -> list[Path]:
    """Return all indexable source files under root."""
    results: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        # Skip excluded directories
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        # Skip non-source extensions
        if path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        # Skip oversized files
        if path.stat().st_size > max_file_bytes:
            continue
        results.append(path)
    return sorted(results)

async def clone_or_fetch(clone_dir: Path, owner: str, name: str) -> str:
    """Clone or fetch a GitHub repo. Returns HEAD commit SHA."""
    import git
    url = f"https://github.com/{owner}/{name}.git"
    if (clone_dir / ".git").exists():
        repo = git.Repo(clone_dir)
        repo.remotes.origin.fetch()
        repo.head.reset("FETCH_HEAD", index=True, working_tree=True)
    else:
        clone_dir.mkdir(parents=True, exist_ok=True)
        repo = git.Repo.clone_from(url, clone_dir, depth=1)
    return repo.head.commit.hexsha
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/worker/test_ingestion.py -v
```
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/ingestion.py tests/worker/test_ingestion.py
git commit -m "feat: stage 1 repo ingestion — file filter, URL parsing, shallow clone"
```

---

## Task 7: Stage 2 — AST Analysis

**Files:**
- Create: `worker/pipeline/ast_analysis.py`
- Create: `tests/worker/test_ast_analysis.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/worker/test_ast_analysis.py
import pytest
from pathlib import Path
from worker.pipeline.ast_analysis import analyze_file, build_module_tree, SUPPORTED_LANGUAGES

FIXTURE = Path("tests/fixtures/simple-repo")

def test_supported_languages_count():
    # 11 extension entries covering 8 languages (some languages have multiple extensions)
    assert len(SUPPORTED_LANGUAGES) == 11

def test_analyze_python_file():
    result = analyze_file(FIXTURE / "models.py")
    assert result is not None
    names = [e["name"] for e in result["entities"]]
    assert "User" in names
    assert "Post" in names

def test_analyze_python_file_entities_have_type():
    result = analyze_file(FIXTURE / "models.py")
    for entity in result["entities"]:
        assert "type" in entity   # "class" or "function"
        assert "name" in entity

def test_build_module_tree_groups_by_dir(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth").mkdir()
    files = [tmp_path / "src" / "main.py", tmp_path / "src" / "auth" / "handler.py"]
    for f in files:
        f.write_text("x = 1")
    tree = build_module_tree(tmp_path, files)
    modules = [m["path"] for m in tree]
    assert "src" in modules or any("src" in m for m in modules)

def test_unsupported_language_returns_none():
    from pathlib import Path
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".rb", mode="w", delete=False) as f:
        f.write("puts 'hello'")
        fname = f.name
    result = analyze_file(Path(fname))
    assert result is None  # Ruby not supported in Phase 1 AST
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/worker/test_ast_analysis.py -v
```

- [ ] **Step 3: Implement `worker/pipeline/ast_analysis.py`**

```python
from __future__ import annotations
from pathlib import Path
from typing import Any

import tree_sitter_python as tspython
import tree_sitter_javascript as tsjavascript
import tree_sitter_typescript as tstypescript
import tree_sitter_java as tsjava
import tree_sitter_go as tsgo
import tree_sitter_c as tsc
import tree_sitter_cpp as tscpp
import tree_sitter_c_sharp as tscsharp
from tree_sitter import Language, Parser

SUPPORTED_LANGUAGES: dict[str, Language] = {
    ".py":   Language(tspython.language()),
    ".js":   Language(tsjavascript.language()),
    ".jsx":  Language(tsjavascript.language()),
    ".ts":   Language(tstypescript.language_typescript()),
    ".tsx":  Language(tstypescript.language_tsx()),
    ".java": Language(tsjava.language()),
    ".go":   Language(tsgo.language()),
    ".c":    Language(tsc.language()),
    ".h":    Language(tsc.language()),
    ".cpp":  Language(tscpp.language()),
    ".cc":   Language(tscpp.language()),
    ".cs":   Language(tscsharp.language()),
}

# Tree-Sitter node types that represent named entities
_ENTITY_TYPES = {
    "function_definition", "class_definition",       # Python
    "function_declaration", "class_declaration",     # JS/TS/Java
    "method_declaration", "method_definition",
    "function_item",                                 # Rust (future)
    "struct_item", "impl_item",
    "func_declaration", "type_declaration",          # Go
}

def analyze_file(path: Path) -> dict[str, Any] | None:
    """Parse a file with Tree-Sitter. Returns entity list or None if unsupported."""
    lang = SUPPORTED_LANGUAGES.get(path.suffix.lower())
    if lang is None:
        return None
    try:
        source = path.read_bytes()
    except (OSError, PermissionError):
        return None

    parser = Parser(lang)
    tree = parser.parse(source)
    entities = _extract_entities(tree.root_node, source)
    return {"path": str(path), "entities": entities}

def _extract_entities(node, source: bytes) -> list[dict[str, Any]]:
    results = []
    if node.type in _ENTITY_TYPES:
        name_node = node.child_by_field_name("name")
        name = name_node.text.decode("utf-8", errors="replace") if name_node else "<anonymous>"
        kind = "class" if "class" in node.type else "function"
        results.append({"type": kind, "name": name, "start_line": node.start_point[0] + 1,
                         "end_line": node.end_point[0] + 1})
    for child in node.children:
        results.extend(_extract_entities(child, source))
    return results

def build_module_tree(root: Path, files: list[Path]) -> list[dict[str, Any]]:
    """Group files into modules by top-level directory under root."""
    modules: dict[str, list[Path]] = {}
    for f in files:
        try:
            rel = f.relative_to(root)
        except ValueError:
            continue
        parts = rel.parts
        module_path = parts[0] if len(parts) > 1 else "."
        modules.setdefault(module_path, []).append(f)

    return [{"path": mod, "files": [str(f) for f in fs]} for mod, fs in sorted(modules.items())]
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/worker/test_ast_analysis.py -v
```
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/ast_analysis.py tests/worker/test_ast_analysis.py
git commit -m "feat: stage 2 AST analysis — tree-sitter entity extraction, module tree"
```

---

## Task 8: Stage 3 — RAG Indexer

**Files:**
- Create: `worker/pipeline/rag_indexer.py`
- Create: `tests/worker/test_rag_indexer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/worker/test_rag_indexer.py
import pytest
import numpy as np
from pathlib import Path
from unittest.mock import AsyncMock
from worker.pipeline.rag_indexer import chunk_file, FAISSStore

def test_chunk_file_returns_non_empty():
    from pathlib import Path
    import tempfile
    content = "def foo():\n    return 1\n" * 50  # repeat to get multiple chunks
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(content)
        fname = Path(f.name)
    chunks = chunk_file(fname, chunk_size=200, overlap=20)
    assert len(chunks) >= 1
    assert all(isinstance(c, str) for c in chunks)

def test_chunk_file_small_file_is_one_chunk(tmp_path):
    small = tmp_path / "small.py"
    small.write_text("x = 1\ny = 2\n")
    chunks = chunk_file(small, chunk_size=1000, overlap=100)
    assert len(chunks) == 1

async def test_faiss_store_add_and_search(tmp_path):
    store = FAISSStore(dimension=4, index_path=tmp_path / "test.index",
                       meta_path=tmp_path / "test.meta.pkl")
    vecs = [np.array([1, 0, 0, 0], dtype=np.float32),
            np.array([0, 1, 0, 0], dtype=np.float32)]
    metas = [{"text": "alpha", "file": "a.py"}, {"text": "beta", "file": "b.py"}]
    store.add(vecs, metas)
    results = store.search(np.array([1, 0, 0, 0], dtype=np.float32), k=1)
    assert results[0]["text"] == "alpha"

async def test_faiss_store_persist_and_load(tmp_path):
    store = FAISSStore(dimension=4, index_path=tmp_path / "test.index",
                       meta_path=tmp_path / "test.meta.pkl")
    vecs = [np.array([1, 0, 0, 0], dtype=np.float32)]
    store.add(vecs, [{"text": "hello", "file": "x.py"}])
    store.save()

    store2 = FAISSStore(dimension=4, index_path=tmp_path / "test.index",
                        meta_path=tmp_path / "test.meta.pkl")
    store2.load()
    results = store2.search(np.array([1, 0, 0, 0], dtype=np.float32), k=1)
    assert results[0]["text"] == "hello"
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/worker/test_rag_indexer.py -v
```

- [ ] **Step 3: Implement `worker/pipeline/rag_indexer.py`**

```python
from __future__ import annotations
import pickle
from pathlib import Path
from typing import Any
import numpy as np
import faiss
from langchain_text_splitters import RecursiveCharacterTextSplitter

def chunk_file(path: Path, chunk_size: int = 1000, overlap: int = 100) -> list[str]:
    """Split a source file into overlapping text chunks."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if not text.strip():
        return []
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=overlap)
    chunks = splitter.split_text(text)
    return chunks if chunks else [text]

class FAISSStore:
    def __init__(self, dimension: int, index_path: Path, meta_path: Path):
        self._dim = dimension
        self._index_path = Path(index_path)
        self._meta_path = Path(meta_path)
        self._index: faiss.IndexFlatIP | None = None
        self._metas: list[dict[str, Any]] = []

    def _ensure_index(self):
        if self._index is None:
            self._index = faiss.IndexFlatIP(self._dim)

    def add(self, vectors: list[np.ndarray], metas: list[dict[str, Any]]) -> None:
        self._ensure_index()
        matrix = np.stack(vectors).astype(np.float32)
        faiss.normalize_L2(matrix)
        self._index.add(matrix)
        self._metas.extend(metas)

    def search(self, query: np.ndarray, k: int = 5) -> list[dict[str, Any]]:
        self._ensure_index()
        if self._index.ntotal == 0:
            return []
        q = query.astype(np.float32).reshape(1, -1)
        faiss.normalize_L2(q)
        k = min(k, self._index.ntotal)
        _, indices = self._index.search(q, k)
        return [self._metas[i] for i in indices[0] if i >= 0]

    def save(self) -> None:
        self._ensure_index()
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(self._index_path))
        self._meta_path.write_bytes(pickle.dumps(self._metas))

    def load(self) -> None:
        self._index = faiss.read_index(str(self._index_path))
        self._metas = pickle.loads(self._meta_path.read_bytes())

async def build_rag_index(
    files: list[Path],
    root: Path,
    store: FAISSStore,
    embedding_provider,
) -> None:
    """Chunk all files, embed, and add to FAISS store."""
    for file_path in files:
        chunks = chunk_file(file_path)
        if not chunks:
            continue
        try:
            rel = str(file_path.relative_to(root))
        except ValueError:
            rel = str(file_path)
        vectors = await embedding_provider.embed_batch(chunks)
        metas = [{"text": chunk, "file": rel, "chunk_idx": i}
                 for i, chunk in enumerate(chunks)]
        store.add(vectors, metas)
    store.save()
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/worker/test_rag_indexer.py -v
```
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/rag_indexer.py tests/worker/test_rag_indexer.py
git commit -m "feat: stage 3 RAG indexer — file chunking, FAISS store with persist/load"
```

---

## Task 9: Stage 4 — Wiki Planner

**Files:**
- Create: `worker/pipeline/wiki_planner.py`
- Create: `tests/worker/test_wiki_planner.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/worker/test_wiki_planner.py
import pytest
from unittest.mock import AsyncMock
from worker.pipeline.wiki_planner import generate_page_plan, validate_page_plan, PagePlan

async def test_generate_page_plan_returns_pages(mock_llm):
    module_tree = [
        {"path": ".", "files": ["main.py"]},
        {"path": "models", "files": ["models.py"]},
    ]
    plan = await generate_page_plan(module_tree, repo_name="testrepo", llm=mock_llm)
    assert len(plan.pages) >= 1
    assert all(hasattr(p, "title") for p in plan.pages)
    assert all(hasattr(p, "slug") for p in plan.pages)

def test_validate_page_plan_accepts_valid():
    raw = {"pages": [{"title": "Overview", "slug": "overview", "modules": ["."]}]}
    plan = validate_page_plan(raw)
    assert plan is not None
    assert plan.pages[0].slug == "overview"

def test_validate_page_plan_rejects_missing_slug():
    raw = {"pages": [{"title": "Overview", "modules": ["."]}]}
    with pytest.raises(ValueError):
        validate_page_plan(raw)

def test_validate_page_plan_rejects_empty_pages():
    with pytest.raises(ValueError):
        validate_page_plan({"pages": []})
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/worker/test_wiki_planner.py -v
```

- [ ] **Step 3: Implement `worker/pipeline/wiki_planner.py`**

```python
from __future__ import annotations
import json
import re
from dataclasses import dataclass
from typing import Any
from worker.llm.base import LLMProvider

@dataclass
class PageSpec:
    title: str
    slug: str
    modules: list[str]
    parent_slug: str | None = None

@dataclass
class PagePlan:
    pages: list[PageSpec]

_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "slug": {"type": "string"},
                    "modules": {"type": "array", "items": {"type": "string"}},
                    "parent_slug": {"type": ["string", "null"]},
                },
                "required": ["title", "slug", "modules"],
            },
        }
    },
    "required": ["pages"],
}

_SYSTEM = """You are a technical documentation architect. Given a repository's module tree,
produce a hierarchical wiki page plan. Each page covers one logical concern.
Output ONLY valid JSON."""

def _build_prompt(module_tree: list[dict], repo_name: str) -> str:
    tree_str = json.dumps(module_tree, indent=2)
    return f"""Repository: {repo_name}

Module tree:
{tree_str}

Create a wiki page plan. Guidelines:
- 3–10 pages total
- Each page has: title (human-readable), slug (url-safe, lowercase, hyphens), modules (list of paths from the tree)
- Include an "Overview" page covering the root
- Group related modules into logical pages
- Optionally set parent_slug for nested pages

Output JSON exactly matching this schema:
{json.dumps(_PLAN_SCHEMA, indent=2)}"""

def validate_page_plan(raw: dict[str, Any]) -> PagePlan:
    if "pages" not in raw:
        raise ValueError("Missing 'pages' key")
    if not raw["pages"]:
        raise ValueError("Page plan must have at least one page")
    pages = []
    for p in raw["pages"]:
        if "slug" not in p:
            raise ValueError(f"Page missing 'slug': {p}")
        if "title" not in p:
            raise ValueError(f"Page missing 'title': {p}")
        pages.append(PageSpec(
            title=p["title"],
            slug=re.sub(r"[^a-z0-9-]", "-", p["slug"].lower()),
            modules=p.get("modules", ["."]),
            parent_slug=p.get("parent_slug"),
        ))
    return PagePlan(pages=pages)

async def generate_page_plan(
    module_tree: list[dict],
    repo_name: str,
    llm: LLMProvider,
    max_retries: int = 3,
) -> PagePlan:
    prompt = _build_prompt(module_tree, repo_name)
    last_error = None
    for attempt in range(max_retries):
        try:
            raw = await llm.generate_structured(prompt, schema=_PLAN_SCHEMA, system=_SYSTEM)
            return validate_page_plan(raw)
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            last_error = e
            if attempt < max_retries - 1:
                prompt += f"\n\nPrevious attempt failed: {e}. Please fix and retry."
    # Fallback: flat plan covering all modules
    return PagePlan(pages=[
        PageSpec(title="Overview", slug="overview", modules=["."]),
        *[PageSpec(title=m["path"].replace("/", " ").title(), slug=m["path"].replace("/", "-"),
                   modules=[m["path"]]) for m in module_tree if m["path"] != "."],
    ])
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/worker/test_wiki_planner.py -v
```
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
git commit -m "feat: stage 4 wiki planner — LLM page plan generation with retry + fallback"
```

---

## Task 10: Stage 5 — Page Generator

**Files:**
- Create: `worker/pipeline/page_generator.py`
- Create: `tests/worker/test_page_generator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/worker/test_page_generator.py
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from worker.pipeline.page_generator import generate_page, PageResult
from worker.pipeline.wiki_planner import PageSpec

async def test_generate_page_returns_markdown(mock_llm, mock_embedding):
    # Set up a real FAISSStore with mock data
    import numpy as np
    from worker.pipeline.rag_indexer import FAISSStore
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmp:
        store = FAISSStore(dimension=1536,
                           index_path=Path(tmp) / "idx",
                           meta_path=Path(tmp) / "meta.pkl")
        store.add([np.zeros(1536, dtype=np.float32)], [{"text": "class User: pass", "file": "models.py"}])

        spec = PageSpec(title="Models", slug="models", modules=["models.py"])
        result = await generate_page(spec, store, mock_llm, mock_embedding, repo_name="test")
    assert isinstance(result, PageResult)
    assert result.slug == "models"
    assert len(result.content) > 0

async def test_generate_page_content_is_non_empty(mock_llm, mock_embedding):
    import numpy as np
    from worker.pipeline.rag_indexer import FAISSStore
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = FAISSStore(dimension=1536, index_path=Path(tmp) / "idx",
                           meta_path=Path(tmp) / "meta.pkl")
        store.add([np.zeros(1536, dtype=np.float32)], [{"text": "x = 1", "file": "main.py"}])
        spec = PageSpec(title="Overview", slug="overview", modules=["."])
        result = await generate_page(spec, store, mock_llm, mock_embedding, repo_name="test")
    assert result.content.strip() != ""
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/worker/test_page_generator.py -v
```

- [ ] **Step 3: Implement `worker/pipeline/page_generator.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from worker.llm.base import LLMProvider
from worker.embedding.base import EmbeddingProvider
from worker.pipeline.rag_indexer import FAISSStore
from worker.pipeline.wiki_planner import PageSpec

_SYSTEM = """You are a technical documentation writer. Write clear, accurate wiki pages
for software repositories. Use Markdown. Include code examples where relevant.
Ground your writing in the provided code context — do not invent APIs."""

@dataclass
class PageResult:
    slug: str
    title: str
    content: str  # Markdown

def _build_page_prompt(spec: PageSpec, context_chunks: list[dict], repo_name: str) -> str:
    context = "\n\n---\n\n".join(
        f"File: {c.get('file', 'unknown')}\n{c['text']}"
        for c in context_chunks
    )
    return f"""Repository: {repo_name}
Page title: {spec.title}
Modules covered: {', '.join(spec.modules)}

Relevant source code:
{context}

Write a comprehensive wiki page for "{spec.title}". Include:
- Overview paragraph
- Key classes/functions with descriptions
- Usage examples where relevant
- How this module interacts with others

Output Markdown only."""

async def generate_page(
    spec: PageSpec,
    store: FAISSStore,
    llm: LLMProvider,
    embedding: EmbeddingProvider,
    repo_name: str,
    top_k: int = 8,
) -> PageResult:
    # Retrieve relevant chunks using the page title as the query
    query_vec = await embedding.embed(f"{spec.title} {' '.join(spec.modules)}")
    context_chunks = store.search(query_vec, k=top_k)

    prompt = _build_page_prompt(spec, context_chunks, repo_name)
    content = await llm.generate(prompt, system=_SYSTEM)

    return PageResult(slug=spec.slug, title=spec.title, content=content)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/worker/test_page_generator.py -v
```
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/page_generator.py tests/worker/test_page_generator.py
git commit -m "feat: stage 5 page generator — RAG retrieval + LLM generation per page"
```

---

## Task 11: Worker Job Orchestration

**Files:**
- Create: `worker/jobs.py`
- Create: `worker/main.py`
- Create: `tests/worker/test_jobs.py`

- [ ] **Step 1: Write failing test**

```python
# tests/worker/test_jobs.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

async def test_full_index_job_updates_status(tmp_path, mock_llm, mock_embedding):
    """Full pipeline runs against fixture repo and sets status=ready."""
    import os
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["AUTOWIKI_DATA_DIR"] = str(tmp_path)

    from shared.database import init_db
    await init_db(str(tmp_path / "test.db"))

    from shared.database import get_session
    from shared.models import Repository, Job
    import uuid

    async with get_session(str(tmp_path / "test.db")) as s:
        repo = Repository(id="r1", owner="testowner", name="simple-repo",
                          platform="github", status="pending")
        job = Job(id="j1", repo_id="r1", type="full_index", status="queued", progress=0)
        s.add(repo); s.add(job); await s.commit()

    with patch("worker.jobs.clone_or_fetch", return_value="abc123def456"), \
         patch("worker.jobs.make_llm_provider", return_value=mock_llm), \
         patch("worker.jobs.make_embedding_provider", return_value=mock_embedding):
        from worker.jobs import run_full_index
        await run_full_index(
            ctx={},
            repo_id="r1",
            job_id="j1",
            owner="testowner",
            name="simple-repo",
            clone_root=Path("tests/fixtures/simple-repo"),
        )

    async with get_session(str(tmp_path / "test.db")) as s:
        job = await s.get(Job, "j1")
        repo = await s.get(Repository, "r1")
        assert job.status == "done"
        assert repo.status == "ready"
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/worker/test_jobs.py -v
```

- [ ] **Step 3: Implement `worker/jobs.py`**

```python
from __future__ import annotations
import uuid
from datetime import datetime
from pathlib import Path

from shared.config import get_config
from shared.database import get_session, init_db
from shared.models import Repository, Job, WikiPage
from worker.pipeline.ingestion import filter_files, clone_or_fetch
from worker.pipeline.ast_analysis import build_module_tree
from worker.pipeline.rag_indexer import build_rag_index, FAISSStore
from worker.pipeline.wiki_planner import generate_page_plan
from worker.pipeline.page_generator import generate_page
from worker.llm import make_llm_provider        # factory lives in worker/llm/__init__.py
from worker.embedding import make_embedding_provider  # factory lives in worker/embedding/__init__.py

async def _update_job(db_path: str, job_id: str, **kwargs):
    async with get_session(db_path) as s:
        job = await s.get(Job, job_id)
        for k, v in kwargs.items():
            setattr(job, k, v)
        await s.commit()

async def _update_repo(db_path: str, repo_id: str, **kwargs):
    async with get_session(db_path) as s:
        repo = await s.get(Repository, repo_id)
        for k, v in kwargs.items():
            setattr(repo, k, v)
        await s.commit()

async def run_full_index(
    ctx: dict,
    repo_id: str,
    job_id: str,
    owner: str,
    name: str,
    clone_root: Path | None = None,
):
    cfg = get_config()
    db_path = str(cfg.database_path)
    data_dir = cfg.data_dir
    await init_db(db_path)

    try:
        await _update_job(db_path, job_id, status="running", progress=5)
        await _update_repo(db_path, repo_id, status="indexing")

        # Stage 1: Ingestion
        if clone_root is None:
            clone_root = data_dir / "repos" / repo_id / "clone"
        head_sha = await clone_or_fetch(clone_root, owner, name)
        files = filter_files(clone_root)
        await _update_job(db_path, job_id, progress=20)

        # Stage 2: AST Analysis
        module_tree = build_module_tree(clone_root, files)
        await _update_job(db_path, job_id, progress=35)

        # Stage 3: RAG Indexer
        llm = make_llm_provider(cfg)
        embedding = make_embedding_provider(cfg)
        repo_data_dir = data_dir / "repos" / repo_id
        repo_data_dir.mkdir(parents=True, exist_ok=True)
        store = FAISSStore(
            dimension=embedding.dimension,
            index_path=repo_data_dir / "faiss.index",
            meta_path=repo_data_dir / "faiss.meta.pkl",
        )
        await build_rag_index(files, clone_root, store, embedding)
        await _update_job(db_path, job_id, progress=55)

        # Stage 4: Wiki Planner
        plan = await generate_page_plan(module_tree, repo_name=name, llm=llm)
        await _update_job(db_path, job_id, progress=65)

        # Stage 5: Page Generator
        wiki_dir = repo_data_dir / "wiki"
        wiki_dir.mkdir(exist_ok=True)
        total = len(plan.pages)
        for i, page_spec in enumerate(plan.pages):
            result = await generate_page(page_spec, store, llm, embedding, repo_name=name)
            (wiki_dir / f"{result.slug}.md").write_text(result.content)
            async with get_session(db_path) as s:
                page = WikiPage(
                    id=str(uuid.uuid4()),
                    repo_id=repo_id,
                    slug=result.slug,
                    title=result.title,
                    content=result.content,
                    page_order=i,
                    parent_slug=page_spec.parent_slug,
                )
                s.add(page)
                await s.commit()
            progress = 65 + int(35 * (i + 1) / total)
            await _update_job(db_path, job_id, progress=progress)

        # Done
        await _update_job(db_path, job_id, status="done", progress=100, finished_at=datetime.utcnow())
        await _update_repo(db_path, repo_id, status="ready", last_commit=head_sha,
                           indexed_at=datetime.utcnow(), wiki_path=str(wiki_dir))

    except Exception as e:
        await _update_job(db_path, job_id, status="failed", error=str(e), finished_at=datetime.utcnow())
        await _update_repo(db_path, repo_id, status="error")
        raise
```

- [ ] **Step 4: Implement `worker/main.py`**

```python
from worker.jobs import run_full_index

async def startup(ctx):
    pass  # connection pool setup if needed

async def shutdown(ctx):
    pass

class WorkerSettings:
    functions = [run_full_index]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = None  # set from REDIS_URL env at runtime

if __name__ == "__main__":
    import os
    from arq import run_worker
    from arq.connections import RedisSettings
    WorkerSettings.redis_settings = RedisSettings.from_dsn(
        os.environ.get("REDIS_URL", "redis://localhost:6379")
    )
    run_worker(WorkerSettings)
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/worker/test_jobs.py -v
```
Expected: 1 PASSED

- [ ] **Step 6: Commit**

```bash
git add worker/jobs.py worker/main.py tests/worker/test_jobs.py
git commit -m "feat: ARQ worker job orchestration — full 5-stage pipeline with progress updates"
```

---

## Task 12: API Gateway — Repos & Jobs

**Files:**
- Create: `api/main.py`
- Create: `api/queue.py`
- Create: `api/routers/repos.py`
- Create: `api/routers/jobs.py`
- Create: `api/ws/jobs.py`
- Create: `tests/api/test_repos.py`
- Create: `tests/api/test_jobs.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/api/test_repos.py
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock

@pytest.fixture
async def client(tmp_path):
    import os
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["AUTOWIKI_DATA_DIR"] = str(tmp_path)
    from shared.database import init_db
    await init_db(str(tmp_path / "test.db"))
    from api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

async def test_post_repos_returns_202(client):
    with patch("api.routers.repos.enqueue_full_index", new_callable=AsyncMock) as mock_eq:
        mock_eq.return_value = "job-uuid-1"
        resp = await client.post("/api/repos", json={"url": "https://github.com/psf/requests"})
    assert resp.status_code == 202
    body = resp.json()
    assert "repo_id" in body
    assert "job_id" in body
    assert body["status"] == "queued"

async def test_post_repos_bad_url(client):
    resp = await client.post("/api/repos", json={"url": "not-a-github-url"})
    assert resp.status_code == 422

async def test_get_repo_not_found(client):
    resp = await client.get("/api/repos/doesnotexist")
    assert resp.status_code == 404

async def test_list_repos_empty(client):
    resp = await client.get("/api/repos")
    assert resp.status_code == 200
    assert resp.json() == {"repos": []}

async def test_list_repos_after_index(client):
    with patch("api.routers.repos.enqueue_full_index", new_callable=AsyncMock) as mock_eq:
        mock_eq.return_value = "job-uuid-2"
        await client.post("/api/repos", json={"url": "https://github.com/psf/requests"})
    resp = await client.get("/api/repos")
    assert resp.status_code == 200
    repos = resp.json()["repos"]
    assert len(repos) == 1
    assert repos[0]["owner"] == "psf"
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/api/test_repos.py -v
```

- [ ] **Step 3: Implement `api/queue.py`**

```python
from __future__ import annotations
import os
import uuid
from arq import create_pool
from arq.connections import RedisSettings

async def enqueue_full_index(repo_id: str, job_id: str, owner: str, name: str) -> str:
    redis = await create_pool(RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379")))
    await redis.enqueue_job("run_full_index", repo_id=repo_id, job_id=job_id, owner=owner, name=name)
    await redis.close()
    return job_id
```

- [ ] **Step 4: Implement `api/routers/repos.py`**

```python
from __future__ import annotations
import uuid, hashlib
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from shared.database import get_session
from shared.models import Repository, Job
from shared.config import get_config
from worker.pipeline.ingestion import parse_github_url
from api.queue import enqueue_full_index

router = APIRouter(prefix="/api/repos")

class IndexRequest(BaseModel):
    url: str

@router.post("", status_code=202)
async def submit_repo(req: IndexRequest):
    try:
        owner, name = parse_github_url(req.url)
    except ValueError:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="Invalid GitHub URL")

    cfg = get_config()
    db_path = str(cfg.database_path)
    repo_id = hashlib.sha256(f"github:{owner}/{name}".encode()).hexdigest()[:16]
    job_id = str(uuid.uuid4())

    async with get_session(db_path) as s:
        existing = await s.get(Repository, repo_id)
        if existing is None:
            repo = Repository(id=repo_id, owner=owner, name=name, status="pending")
            s.add(repo)
        job = Job(id=job_id, repo_id=repo_id, type="full_index", status="queued", progress=0)
        s.add(job)
        await s.commit()

    await enqueue_full_index(repo_id, job_id, owner, name)
    return {"repo_id": repo_id, "job_id": job_id, "status": "queued"}

@router.get("/{repo_id}")
async def get_repo(repo_id: str):
    cfg = get_config()
    async with get_session(str(cfg.database_path)) as s:
        repo = await s.get(Repository, repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="Repository not found")
        return {"id": repo.id, "owner": repo.owner, "name": repo.name,
                "status": repo.status, "indexed_at": repo.indexed_at}
```

- [ ] **Step 5: Implement `api/routers/jobs.py`**

```python
from fastapi import APIRouter, HTTPException
from shared.database import get_session
from shared.models import Job
from shared.config import get_config

router = APIRouter(prefix="/api/jobs")

@router.get("/{job_id}")
async def get_job(job_id: str):
    cfg = get_config()
    async with get_session(str(cfg.database_path)) as s:
        job = await s.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"id": job.id, "repo_id": job.repo_id, "type": job.type,
                "status": job.status, "progress": job.progress, "error": job.error}
```

- [ ] **Step 6: Implement `api/ws/jobs.py`**

```python
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from shared.database import get_session
from shared.models import Job
from shared.config import get_config
import asyncio

router = APIRouter()

@router.websocket("/ws/jobs/{job_id}")
async def ws_job_progress(websocket: WebSocket, job_id: str):
    await websocket.accept()
    cfg = get_config()
    try:
        while True:
            async with get_session(str(cfg.database_path)) as s:
                job = await s.get(Job, job_id)
            if job is None:
                await websocket.send_json({"error": "Job not found"})
                break
            await websocket.send_json({"progress": job.progress, "status": job.status})
            if job.status in ("done", "failed"):
                break
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
```

- [ ] **Step 7: Implement `api/main.py`**

Note: the wiki router is NOT registered here yet — it is added in Task 13 once `api/routers/wiki.py` exists.

```python
from __future__ import annotations
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from shared.config import get_config
from shared.database import init_db
from api.routers import repos, jobs as jobs_router
from api.ws import jobs as ws_jobs

app = FastAPI(title="AutoWiki API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(repos.router)
app.include_router(jobs_router.router)
app.include_router(ws_jobs.router)
# wiki router added in Task 13

@app.on_event("startup")
async def startup():
    cfg = get_config()
    cfg.database_path.parent.mkdir(parents=True, exist_ok=True)
    await init_db(str(cfg.database_path))
```

- [ ] **Step 8: Run tests**

```bash
pytest tests/api/test_repos.py tests/api/test_jobs.py -v
```
Expected: all PASSED

- [ ] **Step 9: Commit**

```bash
git add api/ tests/api/
git commit -m "feat: API gateway — POST /api/repos, GET /api/repos/{id}, GET /api/jobs/{id}, WS /ws/jobs/{id}"
```

---

## Task 13: API Gateway — Wiki Endpoints

**Files:**
- Create: `api/routers/wiki.py`
- Create: `tests/api/test_wiki.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/api/test_wiki.py
import pytest
from httpx import AsyncClient, ASGITransport
from shared.models import Repository, WikiPage
from shared.database import get_session
import uuid

@pytest.fixture
async def client_with_wiki(tmp_path):
    import os
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["AUTOWIKI_DATA_DIR"] = str(tmp_path)
    from shared.database import init_db
    await init_db(str(tmp_path / "test.db"))

    from shared.config import get_config
    cfg = get_config()
    async with get_session(str(cfg.database_path)) as s:
        repo = Repository(id="r1", owner="owner", name="repo", status="ready")
        p1 = WikiPage(id=str(uuid.uuid4()), repo_id="r1", slug="overview",
                      title="Overview", content="# Overview\nHello.", page_order=0)
        p2 = WikiPage(id=str(uuid.uuid4()), repo_id="r1", slug="models",
                      title="Models", content="# Models\nClass User.", page_order=1)
        s.add_all([repo, p1, p2])
        await s.commit()

    from api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

async def test_list_wiki_pages(client_with_wiki):
    resp = await client_with_wiki.get("/api/repos/r1/wiki")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["pages"]) == 2
    slugs = [p["slug"] for p in data["pages"]]
    assert "overview" in slugs

async def test_get_wiki_page(client_with_wiki):
    resp = await client_with_wiki.get("/api/repos/r1/wiki/overview")
    assert resp.status_code == 200
    assert resp.json()["content"].startswith("# Overview")

async def test_get_wiki_page_not_found(client_with_wiki):
    resp = await client_with_wiki.get("/api/repos/r1/wiki/nonexistent")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/api/test_wiki.py -v
```

- [ ] **Step 3: Implement `api/routers/wiki.py`**

```python
from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from shared.database import get_session
from shared.models import WikiPage
from shared.config import get_config

router = APIRouter(prefix="/api/repos/{repo_id}/wiki")

@router.get("")
async def list_wiki_pages(repo_id: str):
    cfg = get_config()
    async with get_session(str(cfg.database_path)) as s:
        result = await s.execute(
            select(WikiPage).where(WikiPage.repo_id == repo_id).order_by(WikiPage.page_order)
        )
        pages = result.scalars().all()
    return {"pages": [{"slug": p.slug, "title": p.title, "parent_slug": p.parent_slug,
                        "page_order": p.page_order} for p in pages]}

@router.get("/{slug}")
async def get_wiki_page(repo_id: str, slug: str):
    cfg = get_config()
    async with get_session(str(cfg.database_path)) as s:
        result = await s.execute(
            select(WikiPage).where(WikiPage.repo_id == repo_id, WikiPage.slug == slug)
        )
        page = result.scalar_one_or_none()
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return {"slug": page.slug, "title": page.title, "content": page.content,
            "parent_slug": page.parent_slug, "updated_at": page.updated_at}
```

- [ ] **Step 4: Register wiki router in `api/main.py`**

Edit `api/main.py`: add the import and registration after the existing routers:

```python
# Add these two lines to api/main.py (after the ws_jobs include_router line):
from api.routers import wiki as wiki_router
app.include_router(wiki_router.router)
```

Also remove the `# wiki router added in Task 13` comment.

- [ ] **Step 5: Run tests**

```bash
pytest tests/api/test_wiki.py -v
```
Expected: 3 PASSED

- [ ] **Step 6: Commit**

```bash
git add api/routers/wiki.py tests/api/test_wiki.py api/main.py
git commit -m "feat: wiki API endpoints — GET /api/repos/{id}/wiki and /wiki/{slug}"
```

---

## Task 14: CLI Commands

**Files:**
- Create: `cli/main.py`
- Create: `cli/commands/index.py`
- Create: `cli/commands/list_repos.py`
- Create: `cli/commands/serve.py`
- Create: `cli/commands/config_cmd.py`
- Create: `tests/cli/test_cli.py`

- [ ] **Step 1: Write failing CLI tests**

```python
# tests/cli/test_cli.py
import pytest
from typer.testing import CliRunner
from unittest.mock import patch, MagicMock
import httpx

@pytest.fixture
def runner():
    from cli.main import app
    return CliRunner(), app

def test_index_success(runner):
    cli, app = runner
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"repo_id": "abc", "job_id": "j1", "status": "queued"}
    mock_resp.raise_for_status = MagicMock()
    with patch("httpx.post", return_value=mock_resp):
        result = cli.invoke(app, ["index", "github.com/psf/requests"])
    assert result.exit_code == 0
    assert "j1" in result.output

def test_index_connection_error(runner):
    cli, app = runner
    with patch("httpx.post", side_effect=httpx.ConnectError("no server")):
        result = cli.invoke(app, ["index", "github.com/psf/requests"])
    assert result.exit_code == 1
    assert "cannot connect" in result.output.lower()

def test_list_empty(runner):
    cli, app = runner
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"repos": []}
    mock_resp.raise_for_status = MagicMock()
    with patch("httpx.get", return_value=mock_resp):
        result = cli.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "No repositories" in result.output

def test_config_show(runner):
    cli, app = runner
    result = cli.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "anthropic" in result.output  # default provider
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/cli/test_cli.py -v
```
Expected: `ModuleNotFoundError` (CLI not implemented yet)

- [ ] **Step 3: Implement `cli/main.py`**

```python
import typer
from cli.commands.index import index_cmd
from cli.commands.list_repos import list_cmd
from cli.commands.serve import serve_cmd
from cli.commands.config_cmd import config_app

app = typer.Typer(name="autowiki", help="AutoWiki — AI-powered wiki generator")
app.command("index")(index_cmd)
app.command("list")(list_cmd)
app.command("serve")(serve_cmd)
app.add_typer(config_app, name="config")

if __name__ == "__main__":
    app()
```

- [ ] **Step 2: Implement `cli/commands/index.py`**

```python
import typer, httpx

def index_cmd(
    url: str = typer.Argument(..., help="GitHub URL, e.g. github.com/owner/repo"),
    force: bool = typer.Option(False, "--force", help="Force full re-index"),
    api_url: str = typer.Option("http://127.0.0.1:3001", envvar="AUTOWIKI_API_URL"),
):
    """Index a GitHub repository."""
    try:
        resp = httpx.post(f"{api_url}/api/repos", json={"url": url}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        typer.echo(f"Indexing started. Job ID: {data['job_id']}")
        typer.echo(f"Track progress: {api_url}/api/jobs/{data['job_id']}")
    except httpx.ConnectError:
        typer.echo("Error: cannot connect to AutoWiki API. Is the server running?", err=True)
        raise typer.Exit(1)
    except httpx.HTTPStatusError as e:
        typer.echo(f"Error: {e.response.text}", err=True)
        raise typer.Exit(1)
```

- [ ] **Step 3: Implement `cli/commands/list_repos.py`**

```python
import typer, httpx

def list_cmd(
    api_url: str = typer.Option("http://127.0.0.1:3001", envvar="AUTOWIKI_API_URL"),
):
    """List all indexed repositories."""
    try:
        resp = httpx.get(f"{api_url}/api/repos", timeout=10)
        resp.raise_for_status()
        repos = resp.json().get("repos", [])
        if not repos:
            typer.echo("No repositories indexed yet.")
            return
        for r in repos:
            typer.echo(f"{r['owner']}/{r['name']}  [{r['status']}]")
    except httpx.ConnectError:
        typer.echo("Error: cannot connect to AutoWiki API.", err=True)
        raise typer.Exit(1)
```

- [ ] **Step 4: Add GET /api/repos list endpoint to `api/routers/repos.py`**

```python
# Add to api/routers/repos.py:
from sqlalchemy import select

@router.get("")
async def list_repos():
    cfg = get_config()
    async with get_session(str(cfg.database_path)) as s:
        result = await s.execute(select(Repository))
        repos = result.scalars().all()
    return {"repos": [{"id": r.id, "owner": r.owner, "name": r.name,
                        "status": r.status, "indexed_at": r.indexed_at} for r in repos]}
```

- [ ] **Step 5: Implement `cli/commands/serve.py`**

```python
import typer, subprocess, sys, os

def serve_cmd(
    port: int = typer.Option(3000, "--port", "-p", help="Web UI port"),
    api_port: int = typer.Option(3001, "--api-port", help="API port"),
):
    """Start the full AutoWiki stack (API + worker + web UI)."""
    typer.echo(f"Starting AutoWiki...")
    typer.echo(f"  API:    http://127.0.0.1:{api_port}")
    typer.echo(f"  Web UI: http://127.0.0.1:{port}")
    typer.echo("Press Ctrl+C to stop.\n")
    env = {**os.environ, "AUTOWIKI_SERVER_PORT": str(api_port)}
    procs = [
        subprocess.Popen([sys.executable, "-m", "uvicorn", "api.main:app",
                          "--host", "127.0.0.1", "--port", str(api_port)], env=env),
        subprocess.Popen([sys.executable, "-m", "worker.main"], env=env),
    ]
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        for p in procs:
            p.terminate()
```

- [ ] **Step 6: Implement `cli/commands/config_cmd.py`**

```python
import typer, json, yaml
from pathlib import Path
from shared.config import get_config

config_app = typer.Typer(help="Manage AutoWiki configuration")

@config_app.command("show")
def show():
    """Show current configuration."""
    cfg = get_config()
    typer.echo(json.dumps(cfg.model_dump(), indent=2, default=str))

@config_app.command("set")
def set_value(
    key: str = typer.Argument(..., help="Dot-separated key, e.g. llm.provider"),
    value: str = typer.Argument(..., help="Value to set"),
):
    """Set a configuration value in ~/.autowiki/autowiki.yml."""
    config_path = Path.home() / ".autowiki" / "autowiki.yml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    keys = key.split(".")
    d = existing
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value
    config_path.write_text(yaml.dump(existing, default_flow_style=False))
    typer.echo(f"Set {key} = {value} in {config_path}")
```

- [ ] **Step 7: Run CLI tests**

```bash
pytest tests/cli/test_cli.py -v
```
Expected: 4 PASSED

- [ ] **Step 8: Commit**

```bash
git add cli/ api/routers/repos.py tests/cli/
git commit -m "feat: CLI commands — index, list, serve, config show/set (with TDD)"
```

---

## Task 15: Web UI — Scaffold & IndexForm

**Files:**
- Create: `web/app/layout.tsx`
- Create: `web/app/page.tsx`
- Create: `web/lib/api.ts`
- Create: `web/lib/ws.ts`
- Create: `web/components/IndexForm.tsx`
- Create: `web/components/JobProgressBar.tsx`

- [ ] **Step 1: Implement `web/lib/api.ts`**

```typescript
// INTERNAL_API_URL is used for server-side SSR calls (Docker: http://api:3001)
// NEXT_PUBLIC_API_URL is baked into the client bundle (browser: http://localhost:3001)
const API_URL =
  typeof window === "undefined"
    ? (process.env.INTERNAL_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:3001")
    : (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:3001");

export async function submitRepo(url: string) {
  const res = await fetch(`${API_URL}/api/repos`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ repo_id: string; job_id: string; status: string }>;
}

export async function getJob(jobId: string) {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ status: string; progress: number; error?: string }>;
}

export async function getRepoWiki(repoId: string) {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/wiki`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ pages: { slug: string; title: string; parent_slug: string | null }[] }>;
}

export async function getWikiPage(repoId: string, slug: string) {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/wiki/${slug}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ slug: string; title: string; content: string }>;
}
```

- [ ] **Step 2: Implement `web/lib/ws.ts`**

```typescript
"use client";
import { useEffect, useRef, useState } from "react";

const WS_URL = process.env.NEXT_PUBLIC_API_URL?.replace("http", "ws") ?? "ws://localhost:3001";

export function useJobProgress(jobId: string | null) {
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState<string>("queued");
  const ws = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!jobId) return;
    ws.current = new WebSocket(`${WS_URL}/ws/jobs/${jobId}`);
    ws.current.onmessage = (e) => {
      const data = JSON.parse(e.data);
      setProgress(data.progress ?? 0);
      setStatus(data.status ?? "running");
    };
    return () => ws.current?.close();
  }, [jobId]);

  return { progress, status };
}
```

- [ ] **Step 3: Implement `web/components/IndexForm.tsx`**

```typescript
"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { submitRepo } from "@/lib/api";

export function IndexForm() {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const router = useRouter();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const { repo_id, job_id } = await submitRepo(url);
      // Extract owner/repo from the input URL for post-completion redirect
      const match = url.replace(/^https?:\/\//, "").match(/github\.com\/([^/]+)\/([^/]+)/);
      const owner = match?.[1] ?? "";
      const repo = match?.[2]?.replace(/\.git$/, "") ?? "";
      router.push(`/jobs/${job_id}?repo_id=${repo_id}&owner=${owner}&repo=${repo}`);
    } catch (err: any) {
      setError(err.message ?? "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4 w-full max-w-xl">
      <Input
        type="text"
        placeholder="github.com/owner/repo"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        disabled={loading}
        className="font-mono"
      />
      <Button type="submit" disabled={loading || !url.trim()}>
        {loading ? "Submitting…" : "Generate Wiki"}
      </Button>
      {error && <p className="text-destructive text-sm">{error}</p>}
    </form>
  );
}
```

- [ ] **Step 4: Implement `web/components/JobProgressBar.tsx`**

```typescript
"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Progress } from "@/components/ui/progress";
import { useJobProgress } from "@/lib/ws";

interface Props {
  jobId: string;
  repoId: string;
  owner: string;
  repo: string;
}

export function JobProgressBar({ jobId, repoId, owner, repo }: Props) {
  const { progress, status } = useJobProgress(jobId);
  const router = useRouter();

  useEffect(() => {
    if (status === "done") {
      router.push(`/${owner}/${repo}`);
    }
  }, [status, owner, repo, router]);

  return (
    <div className="flex flex-col gap-4 w-full max-w-xl">
      <p className="text-sm text-muted-foreground capitalize">{status}…</p>
      <Progress value={progress} className="h-2" />
      <p className="text-xs text-muted-foreground">{progress}%</p>
      {status === "failed" && (
        <p className="text-destructive text-sm">Generation failed. Check server logs.</p>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Implement `web/app/page.tsx`**

```typescript
import { IndexForm } from "@/components/IndexForm";

export default function HomePage() {
  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-8 p-8 bg-background">
      <div className="text-center">
        <h1 className="text-4xl font-bold tracking-tight">AutoWiki</h1>
        <p className="text-muted-foreground mt-2">AI-powered wiki generator for GitHub repositories</p>
      </div>
      <IndexForm />
    </main>
  );
}
```

- [ ] **Step 6: Create job progress page `web/app/jobs/[job_id]/page.tsx`**

```typescript
"use client";
import { use } from "react";
import { useSearchParams } from "next/navigation";
import { JobProgressBar } from "@/components/JobProgressBar";

export default function JobPage({ params }: { params: Promise<{ job_id: string }> }) {
  const { job_id } = use(params);
  const searchParams = useSearchParams();
  const repoId = searchParams.get("repo_id") ?? "";
  // owner and repo passed as query params by IndexForm for post-completion redirect
  const owner = searchParams.get("owner") ?? "";
  const repo = searchParams.get("repo") ?? "";

  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-8 p-8">
      <h2 className="text-xl font-semibold">Generating Wiki…</h2>
      <JobProgressBar jobId={job_id} repoId={repoId} owner={owner} repo={repo} />
    </main>
  );
}
```

- [ ] **Step 7: Implement `web/app/layout.tsx` — root layout with dark mode**

```typescript
import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "AutoWiki",
  description: "AI-powered wiki generator for GitHub repositories",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className={inter.className}>{children}</body>
    </html>
  );
}
```

- [ ] **Step 8: Commit**

```bash
cd web && git add . && cd .. && git commit -m "feat: web UI scaffold, IndexForm, JobProgressBar with WebSocket progress, root layout"
```

---

## Task 16: Web UI — Wiki Viewer

**Files:**
- Create: `web/components/WikiSidebar.tsx`
- Create: `web/components/WikiPage.tsx`
- Create: `web/app/[owner]/[repo]/page.tsx`
- Create: `web/app/[owner]/[repo]/[slug]/page.tsx`
- Create: `web/app/[owner]/[repo]/layout.tsx`

- [ ] **Step 1: Implement `web/components/WikiSidebar.tsx`**

```typescript
"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

interface Page { slug: string; title: string; parent_slug: string | null }
interface Props { pages: Page[]; owner: string; repo: string }

export function WikiSidebar({ pages, owner, repo }: Props) {
  const pathname = usePathname();
  const topLevel = pages.filter(p => !p.parent_slug);

  return (
    <nav className="w-64 shrink-0 border-r h-full overflow-y-auto p-4">
      <p className="text-xs font-semibold text-muted-foreground uppercase mb-3">{owner}/{repo}</p>
      <ul className="space-y-1">
        {topLevel.map(page => (
          <li key={page.slug}>
            <Link
              href={`/${owner}/${repo}/${page.slug}`}
              className={cn(
                "block text-sm px-2 py-1 rounded hover:bg-accent",
                pathname.endsWith(`/${page.slug}`) && "bg-accent font-medium"
              )}
            >
              {page.title}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
```

- [ ] **Step 2: Install markdown renderer**

```bash
cd web && npm install react-markdown remark-gfm rehype-highlight highlight.js
```

- [ ] **Step 3: Implement `web/components/WikiPage.tsx`**

```typescript
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "highlight.js/styles/github-dark.css";

interface Props { title: string; content: string }

export function WikiPageContent({ title, content }: Props) {
  return (
    <article className="prose prose-invert max-w-none p-8">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </article>
  );
}
```

- [ ] **Step 4: Implement wiki layout and pages**

`web/app/[owner]/[repo]/layout.tsx`:
```typescript
import { getRepoWiki } from "@/lib/api";
import { WikiSidebar } from "@/components/WikiSidebar";
import crypto from "crypto";

export default async function WikiLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ owner: string; repo: string }>;
}) {
  const { owner, repo } = await params;
  const repoId = crypto.createHash("sha256").update(`github:${owner}/${repo}`).digest("hex").slice(0, 16);
  const { pages } = await getRepoWiki(repoId).catch(() => ({ pages: [] }));

  return (
    <div className="flex h-screen overflow-hidden">
      <WikiSidebar pages={pages} owner={owner} repo={repo} />
      <main className="flex-1 overflow-y-auto">{children}</main>
    </div>
  );
}
```

`web/app/[owner]/[repo]/page.tsx`:
```typescript
import { redirect } from "next/navigation";
import { getRepoWiki } from "@/lib/api";
import crypto from "crypto";

export default async function WikiIndex({ params }: { params: Promise<{ owner: string; repo: string }> }) {
  const { owner, repo } = await params;
  const repoId = crypto.createHash("sha256").update(`github:${owner}/${repo}`).digest("hex").slice(0, 16);
  const { pages } = await getRepoWiki(repoId).catch(() => ({ pages: [] }));
  if (pages.length > 0) redirect(`/${owner}/${repo}/${pages[0].slug}`);
  return <p className="p-8 text-muted-foreground">No wiki pages found.</p>;
}
```

`web/app/[owner]/[repo]/[slug]/page.tsx`:
```typescript
import { getWikiPage } from "@/lib/api";
import { WikiPageContent } from "@/components/WikiPage";
import crypto from "crypto";

export default async function WikiPageRoute({
  params,
}: {
  params: Promise<{ owner: string; repo: string; slug: string }>;
}) {
  const { owner, repo, slug } = await params;
  const repoId = crypto.createHash("sha256").update(`github:${owner}/${repo}`).digest("hex").slice(0, 16);
  try {
    const page = await getWikiPage(repoId, slug);
    return <WikiPageContent title={page.title} content={page.content} />;
  } catch {
    return <p className="p-8 text-destructive">Page not found.</p>;
  }
}
```

- [ ] **Step 5: Commit**

```bash
cd web && git add . && cd .. && git commit -m "feat: wiki viewer — WikiSidebar, WikiPage, dynamic route pages"
```

---

## Task 17: Integration Test & Coverage Check

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration test against fixture repo**

```python
# tests/test_integration.py
"""
Integration test: runs the full 5-stage pipeline against the fixture repo
using mocked LLM and embedding providers. Verifies pages are stored in DB.
"""
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock
import numpy as np

FIXTURE_REPO = Path("tests/fixtures/simple-repo")

async def test_full_pipeline_produces_pages(tmp_path, mock_llm, mock_embedding):
    import os
    os.environ["DATABASE_PATH"] = str(tmp_path / "autowiki.db")
    os.environ["AUTOWIKI_DATA_DIR"] = str(tmp_path)

    from shared.database import init_db, get_session
    await init_db(str(tmp_path / "autowiki.db"))
    from shared.models import Repository, Job, WikiPage
    from sqlalchemy import select
    import uuid

    async with get_session(str(tmp_path / "autowiki.db")) as s:
        repo = Repository(id="int-r1", owner="t", name="simple-repo", status="pending")
        job = Job(id="int-j1", repo_id="int-r1", type="full_index", status="queued", progress=0)
        s.add(repo); s.add(job); await s.commit()

    with patch("worker.jobs.clone_or_fetch", return_value="deadbeef"), \
         patch("worker.jobs.make_llm_provider", return_value=mock_llm), \
         patch("worker.jobs.make_embedding_provider", return_value=mock_embedding):
        from worker.jobs import run_full_index
        await run_full_index(
            ctx={}, repo_id="int-r1", job_id="int-j1",
            owner="t", name="simple-repo", clone_root=FIXTURE_REPO,
        )

    async with get_session(str(tmp_path / "autowiki.db")) as s:
        result = await s.execute(select(WikiPage).where(WikiPage.repo_id == "int-r1"))
        pages = result.scalars().all()
        job = await s.get(Job, "int-j1")
        repo = await s.get(Repository, "int-r1")

    assert job.status == "done"
    assert repo.status == "ready"
    assert len(pages) >= 1
    assert all(p.content for p in pages)
```

- [ ] **Step 2: Run all tests**

```bash
pytest tests/ -v --ignore=tests/e2e --cov=worker --cov=api --cov=shared --cov-report=term-missing
```
Expected: all PASSED, coverage ≥ 80% on `worker/` and `api/`

- [ ] **Step 3: Fix any failures before proceeding**

If any tests fail, investigate and fix before continuing. Do not move to the next step with failing tests.

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: integration test — full 5-stage pipeline against fixture repo"
```

---

## Task 18: Docker Build Verification

- [ ] **Step 1: Build all Docker images**

```bash
docker-compose build
```
Expected: all three images build without error.

- [ ] **Step 2: Smoke test with docker-compose**

```bash
ANTHROPIC_API_KEY=test OPENAI_API_KEY=test docker-compose up -d
sleep 10
curl -s http://localhost:3001/api/repos | jq .
```
Expected: JSON response `{"repos": []}` (API is live).

- [ ] **Step 3: Check web UI**

Open `http://localhost:3000` in a browser. Verify the home page loads with the URL input form.

- [ ] **Step 4: Shut down**

```bash
docker-compose down
```

- [ ] **Step 5: Commit**

```bash
git commit -m "chore: verified docker-compose build and smoke test"
```

---

## Task 19: Final Cleanup & Phase 1 Tag

- [ ] **Step 1: Run full test suite one last time**

```bash
pytest tests/ -v --ignore=tests/e2e --cov=worker --cov=api --cov=shared --cov-report=term-missing
```

- [ ] **Step 2: Ensure no TODO/FIXME comments remain in core code**

```bash
grep -r "TODO\|FIXME\|HACK\|XXX" worker/ api/ shared/ cli/ --include="*.py"
```

- [ ] **Step 3: Tag Phase 1**

```bash
git tag -a v0.1.0-phase1 -m "Phase 1 complete: Core MVP — 5-stage pipeline, API, Web UI, CLI"
```

- [ ] **Step 4: Announce completion**

Phase 1 is complete. The system can:
- Accept a GitHub URL via the web UI, CLI, or REST API
- Run a 5-stage async generation pipeline (ingestion → AST → RAG → planning → generation)
- Serve the generated wiki via REST API
- Display the wiki in a Next.js web UI with sidebar navigation

Next: Phase 2 plan (chat, diagrams, incremental refresh, `.autowikiignore`).
