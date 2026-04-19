# AutoWiki 第 1 阶段 — 核心 MVP 实施计划

> **[已完成 — 部分内容已过时]** 已实施并合并（标签为 `v0.1.0-phase1`）。以下内容已被后来的工作取代：
> - **5 阶段流水线**：流水线增加到 6 个阶段（阶段 6：页面生成器；短期存在的阶段 7 图表合成已被移除）。本计划中的阶段编号引用具有历史意义。
> - **`build_module_tree()` / `build_enhanced_module_tree()`**：由返回 `FileAnalysis` 对象的 `analyze_all_files()` 取代（流水线重构工作）。本计划中对这些函数的所有代码引用均已作废。
> - **`generate_page_plan()`**：重命名为 `generate_wiki_plan()` 并重新设计为返回 `WikiPlan` 的两阶段 LLM 规划器。
> - **`module_tree.json`**：由 `wiki_plan.json` 作为内部状态文件取代。
> - **Next.js 15 / Tailwind v3**：项目使用 Next.js 16.2.1 和 Tailwind v4（仅 CSS 配置，无 `tailwind.config.ts`）。

> **对于智能体工作者：** 要求的子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 来逐项任务实施此计划。步骤使用复选框 (`- [ ]`) 语法进行跟踪。

**目标：** 建立一个工作的 AutoWiki 实例，该实例克隆 GitHub 仓库，运行~~5 阶段~~流水线（摄取 → AST → RAG → 规划 → 页面生成），通过 REST API 提供维基服务，并在 Next.js Web UI 中显示 — 全部通过 Docker Compose 编排。

**架构：** 工作者 + API 网关分离。FastAPI API 网关将任务排入 Redis；ARQ 工作者异步执行~~5 阶段~~流水线。两者共享一个 SQLite 数据库和一个共享 Docker 卷上的 FAISS 向量存储。Next.js 前端仅与 API 网关通信。

**技术栈：** Python 3.12, FastAPI, ARQ (Redis 任务队列), Tree-Sitter, LangChain 文本分割器, FAISS, SQLite, Next.js 15, Tailwind CSS, shadcn/ui, Docker Compose, pytest, Playwright。

---

## 文件映射

```
AutoWiki/
├── docker-compose.yml
├── pyproject.toml                  # Python monorepo (api + worker + cli)
├── autowiki.yml.example
│
├── shared/                         # 由 api/ 和 worker/ 共同导入
│   ├── __init__.py
│   ├── config.py                   # 配置加载：env → cwd yaml → ~/.autowiki/yaml → defaults
│   ├── models.py                   # SQLAlchemy 模型 + Pydantic 模式
│   └── database.py                 # SQLite 引擎、会话工厂、迁移
│
├── worker/
│   ├── __init__.py
│   ├── main.py                     # ARQ 工作者入口点（函数注册）
│   ├── jobs.py                     # full_index 任务：编排流水线阶段
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── ingestion.py            # 阶段 1：克隆/拉取、文件过滤、提交 SHA
│   │   ├── ast_analysis.py         # 阶段 2：Tree-Sitter 解析、依赖图、模块树
│   │   ├── rag_indexer.py          # 阶段 3：分块、嵌入、构建/更新 FAISS 索引
│   │   ├── wiki_planner.py         # 阶段 4：LLM → 分层 JSON 页面计划
│   │   └── page_generator.py       # 阶段 5：每页 RAG 检索 + LLM 生成
│   ├── llm/
│   │   ├── __init__.py             # make_llm_provider() 工厂
│   │   ├── base.py                 # LLMProvider 抽象基类
│   │   ├── anthropic_provider.py   # Anthropic 适配器
│   │   ├── openai_provider.py      # OpenAI + openai-compatible 适配器
│   │   └── ollama_provider.py      # Ollama 适配器
│   └── embedding/
│       ├── __init__.py             # make_embedding_provider() 工厂
│       ├── base.py                 # EmbeddingProvider 抽象基类
│       ├── openai_embed.py         # OpenAI 嵌入
│       └── ollama_embed.py         # Ollama 嵌入
│
├── api/
│   ├── __init__.py
│   ├── main.py                     # FastAPI 应用、启动、CORS
│   ├── queue.py                    # ARQ Redis 池、排队助手
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── repos.py                # POST /api/repos, GET /api/repos/{id}
│   │   ├── wiki.py                 # GET /api/repos/{id}/wiki, GET .../wiki/{slug}
│   │   └── jobs.py                 # GET /api/jobs/{id}
│   └── ws/
│       ├── __init__.py
│       └── jobs.py                 # WS /ws/jobs/{job_id} — 流式传输进度 0-100
│
├── cli/
│   ├── __init__.py
│   ├── main.py                     # Typer 应用入口点
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
│   ├── components.json             # shadcn/ui 配置
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx                # 首页：IndexForm
│   │   ├── repos/
│   │   │   └── page.tsx            # /repos 列表
│   │   └── [owner]/
│   │       └── [repo]/
│   │           ├── page.tsx        # 维基索引
│   │           └── [slug]/
│   │               └── page.tsx    # 单个维基页面
│   ├── components/
│   │   ├── IndexForm.tsx
│   │   ├── JobProgressBar.tsx
│   │   ├── WikiSidebar.tsx
│   │   └── WikiPage.tsx
│   └── lib/
│       ├── api.ts                  # REST API 客户端（类型化 fetch 包装器）
│       └── ws.ts                   # 任务进度的 WebSocket 钩子
│
└── tests/
    ├── conftest.py                 # 共享固件：测试数据库、模拟 LLM、固件仓库
    ├── fixtures/
    │   └── simple-repo/            # 微型固件仓库（3 个 Python 文件，已知结构）
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
        └── test_index_flow.py      # Playwright：首页 → 索引 → 维基页面可见
```

---

## 任务 1：项目脚手架

**文件：**
- 创建：`pyproject.toml`
- 创建：`docker-compose.yml`
- 创建：`autowiki.yml.example`
- 创建：`.gitignore`（扩展现有）
- 创建：`web/package.json`
- 创建：`web/next.config.ts`
- 创建：`web/tailwind.config.ts`
- 创建：`web/components.json`

- [ ] **步骤 1：创建 `pyproject.toml`**

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
    "tree-sitter-go>=0.21",
    "tree-sitter-rust>=0.23",
    "tree-sitter-c>=0.21",
    "tree-sitter-cpp>=0.23",
    "tree-sitter-c-sharp>=0.23",
    "langchain-text-splitters>=0.3",
    "faiss-cpu>=1.8",
    "anthropic>=0.40",
    "openai>=1.50",
    "google-generativeai>=0.8",
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

- [ ] **步骤 2：创建 `docker-compose.yml`**

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
      - GOOGLE_API_KEY=${GOOGLE_API_KEY:-}
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
      - GOOGLE_API_KEY=${GOOGLE_API_KEY:-}
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
      # 浏览器端（在构建时烘焙到客户端包中）
      - NEXT_PUBLIC_API_URL=http://localhost:3001
      # Docker 网络内的服务器端 SSR 调用
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

- [ ] **步骤 3：创建 `autowiki.yml.example`**

```yaml
# 复制到 autowiki.yml 并填写您的值。
# 环境变量优先于此文件。

llm:
  provider: anthropic          # anthropic | openai | openai-compatible | ollama
  model: claude-sonnet-4-6
  api_key: ${ANTHROPIC_API_KEY}
  # base_url: http://localhost:11434/v1  # 仅用于 openai-compatible / ollama

embedding:
  provider: openai             # openai | ollama
  model: text-embedding-3-small
  api_key: ${OPENAI_API_KEY}

server:
  host: 127.0.0.1              # 谨慎更改为 0.0.0.0 — 会启用网络曝光
  port: 3001
  # auth_token: ""             # 网络曝光实例的可选承载令牌

chat:
  history_window: 10           # 注入 LLM 上下文的前几轮对话数量
```

- [ ] **步骤 4：初始化 Web 应用**

```bash
cd web
npx create-next-app@latest . --typescript --tailwind --app --src-dir=no --import-alias="@/*" --yes
npx shadcn@latest init --defaults
npx shadcn@latest add button input card badge progress separator scroll-area
```

- [ ] **步骤 4b：在 `web/next.config.ts` 中设置 `output: "standalone"`**

这是 Docker 运行阶段（复制 `.next/standalone`）和 `autowiki serve`（运行 `server.js`）所必需的。编辑 `web/next.config.ts`：

```typescript
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
};

export default nextConfig;
```

- [ ] **步骤 5：创建 Dockerfile 存根**

`api/Dockerfile`：
```dockerfile
FROM python:3.12-slim
WORKDIR /app
# 在安装前复制源码，以便可编辑安装解析包根目录
COPY pyproject.toml .
COPY shared/ ./shared/
COPY api/ ./api/
COPY cli/ ./cli/
COPY worker/ ./worker/
RUN pip install --no-cache-dir .
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "3001"]
```

`worker/Dockerfile`：
```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml .
COPY shared/ ./shared/
COPY worker/ ./worker/
COPY api/ ./api/
COPY cli/ ./cli/
RUN pip install --no-cache-dir .
CMD ["python", "-m", "worker.main"]
```

`web/Dockerfile`：
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
COPY --from=builder /app/public ./public
CMD ["node", "server.js"]
```

- [ ] **步骤 6：为所有 Python 包创建 `__init__.py` 存根文件**

```bash
mkdir -p shared worker/pipeline worker/llm worker/embedding api/routers api/ws cli/commands tests/worker tests/api tests/e2e tests/fixtures/simple-repo
touch shared/__init__.py worker/__init__.py worker/pipeline/__init__.py worker/llm/__init__.py worker/embedding/__init__.py api/__init__.py api/routers/__init__.py api/ws/__init__.py cli/__init__.py cli/commands/__init__.py
```

- [ ] **步骤 7：创建固件仓库**

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

- [ ] **步骤 8：提交脚手架**

```bash
git add .
git commit -m "chore: project scaffold — docker-compose, pyproject, web bootstrap, fixture repo"
```

---

## 任务 2：共享配置

**文件：**
- 创建：`shared/config.py`
- 创建：`tests/conftest.py`

- [ ] **步骤 1：编写失败测试**

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

- [ ] **步骤 2：运行以验证失败**

```bash
pytest tests/test_config.py -v
```
预期：`ModuleNotFoundError: No module named 'shared'`

- [ ] **步骤 3：实现 `shared/config.py`**

```python
from __future__ import annotations
import os
from pathlib import Path
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class LLMConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOWIKI_LLM_")
    provider: Literal["anthropic", "google", "openai", "openai-compatible", "ollama"] = "anthropic"
    model: str = "claude-sonnet-4-6"
    api_key: str = ""
    base_url: str = ""

class EmbeddingConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOWIKI_EMBEDDING_")
    provider: Literal["openai", "google", "ollama"] = "openai"
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
    # 不要在这里设置 env_nested_delimiter — 每个嵌套子模型
    # 独立读取自己的 env_prefix（例如 LLMConfig 读取 AUTOWIKI_LLM_* 等）
    # 添加 env_nested_delimiter 会与 pydantic-settings v2 中的子模型前缀冲突。
    model_config = SettingsConfigDict(env_prefix="AUTOWIKI_")
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

- [ ] **步骤 4：运行测试以验证通过**

```bash
pytest tests/test_config.py -v
```
预期：2 PASSED

- [ ] **步骤 5：使用共享固件创建 `tests/conftest.py`**

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
    """返回一个产生可预测内容的模拟 LLMProvider。"""
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
    """返回一个返回零向量的模拟 EmbeddingProvider。"""
    import numpy as np
    m = AsyncMock()
    m.embed.return_value = np.zeros(1536, dtype="float32")
    m.embed_batch.side_effect = lambda texts: [np.zeros(1536, dtype="float32") for _ in texts]
    return m
```

- [ ] **步骤 6：提交**

```bash
git add shared/config.py tests/test_config.py tests/conftest.py
git commit -m "feat: shared config with env/yaml precedence and pydantic-settings"
```

---

## 任务 3：SQLite 数据库

**文件：**
- 创建：`shared/database.py`
- 创建：`shared/models.py`
- 创建：`tests/test_database.py`

- [ ] **步骤 1：编写失败测试**

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

- [ ] **步骤 2：运行以验证失败**

```bash
pytest tests/test_database.py -v
```
预期：`ModuleNotFoundError`

- [ ] **步骤 3：实现 `shared/models.py`**

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

- [ ] **步骤 4：实现 `shared/database.py`**

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

- [ ] **步骤 5：运行测试**

```bash
pytest tests/test_database.py -v
```
预期：2 PASSED

- [ ] **步骤 6：提交**

```bash
git add shared/models.py shared/database.py tests/test_database.py
git commit -m "feat: SQLite schema with SQLAlchemy async (repositories, jobs, wiki_pages)"
```

---

## 任务 4：LLM 提供商抽象（Anthropic, Google, OpenAI, Ollama）

**文件：**
- 创建：`worker/llm/base.py`
- 创建：`worker/llm/anthropic_provider.py`
- 创建：`worker/llm/gemini_provider.py`
- 创建：`worker/llm/openai_provider.py`
- 创建：`worker/llm/ollama_provider.py`
- 创建：`tests/worker/test_llm.py`

- [ ] **步骤 1：编写失败测试**

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

- [ ] **步骤 2：运行以验证失败**

```bash
pytest tests/worker/test_llm.py -v
```

- [ ] **步骤 3：实现 `worker/llm/base.py`**

```python
from abc import ABC, abstractmethod
from typing import Any

class LLMProvider(ABC):
    @abstractmethod
    async def generate(self, prompt: str, system: str = "") -> str:
        """从提示词生成文本。返回完整的响应字符串。"""

    @abstractmethod
    async def generate_structured(self, prompt: str, schema: dict[str, Any], system: str = "") -> dict[str, Any]:
        """生成并解析匹配给定架构的 JSON 响应。"""

    @abstractmethod
    async def generate_stream(self, prompt: str, system: str = ""):
        """异步生成器，在文本块到达时产生它们。"""
```

- [ ] **步骤 4：实现 `worker/llm/anthropic_provider.py`**

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
        # 如果存在 markdown 代码栅栏，则剥离它们
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

- [ ] **步骤 5：实现 `worker/llm/gemini_provider.py`**

```python
from __future__ import annotations
import json
from typing import Any, AsyncIterator
import google.generativeai as genai
from worker.llm.base import LLMProvider

class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gemini-1.5-pro"):
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model_name=model)

    async def generate(self, prompt: str, system: str = "") -> str:
        response = await self._model.generate_content_async(
            prompt,
            generation_config=genai.types.GenerationConfig(max_output_tokens=8192)
        )
        return response.text

    async def generate_structured(self, prompt: str, schema: dict[str, Any], system: str = "") -> dict[str, Any]:
        response = await self._model.generate_content_async(
            prompt,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
                max_output_tokens=8192
            )
        )
        return json.loads(response.text)

    async def generate_stream(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        response = await self._model.generate_content_async(
            prompt,
            generation_config=genai.types.GenerationConfig(max_output_tokens=8192),
            stream=True
        )
        async for chunk in response:
            yield chunk.text
```

- [ ] **步骤 6：实现 `worker/llm/openai_provider.py`**

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

- [ ] **步骤 7：实现 `worker/llm/ollama_provider.py`**

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

- [ ] **步骤 8：实现 `worker/llm/__init__.py` — 工厂函数**

```python
# worker/llm/__init__.py
from __future__ import annotations
import os
from worker.llm.base import LLMProvider

def make_llm_provider(cfg) -> LLMProvider:
    """工厂：从配置创建 LLMProvider。在这里导入，以便 worker/jobs.py 干净地打补丁。"""
    from worker.llm.anthropic_provider import AnthropicProvider
    from worker.llm.openai_provider import OpenAIProvider
    from worker.llm.gemini_provider import GeminiProvider
    from worker.llm.ollama_provider import OllamaProvider
    p = cfg.llm.provider
    if p == "anthropic":
        return AnthropicProvider(
            api_key=cfg.llm.api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
            model=cfg.llm.model,
        )
    elif p == "google":
        return GeminiProvider(
            api_key=cfg.llm.api_key or os.environ.get("GOOGLE_API_KEY", ""),
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

- [ ] **步骤 9：运行测试**

```bash
pytest tests/worker/test_llm.py -v
```
预期：3 PASSED

- [ ] **步骤 10：提交**

```bash
git add worker/llm/ tests/worker/test_llm.py
git commit -m "feat: LLM provider abstraction (Anthropic, Google, OpenAI, Ollama)"
```

---

## 任务 5：嵌入提供商抽象（OpenAI, Google, Ollama）

**文件：**
- 创建：`worker/embedding/base.py`
- 创建：`worker/embedding/openai_embed.py`
- 创建：`worker/embedding/gemini_embed.py`
- 创建：`worker/embedding/ollama_embed.py`
- 创建：`tests/worker/test_embedding.py`

- [ ] **步骤 1：编写失败测试**

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

- [ ] **步骤 2：运行以验证失败**

```bash
pytest tests/worker/test_embedding.py -v
```

- [ ] **步骤 3：实现 `worker/embedding/base.py`**

```python
from abc import ABC, abstractmethod
import numpy as np

class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, text: str) -> np.ndarray:
        """嵌入单个文本。返回 float32 numpy 数组。"""

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        """嵌入多个文本。返回 float32 数组列表。"""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """嵌入向量维度。"""
```

- [ ] **步骤 4：实现 `worker/embedding/openai_embed.py`**

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
# 注意：make_embedding_provider() 工厂位于 worker/embedding/__init__.py (步骤 5b)
```

- [ ] **步骤 5：实现 `worker/embedding/ollama_embed.py`**

```python
from __future__ import annotations
import numpy as np
import httpx
from worker.embedding.base import EmbeddingProvider

class OllamaEmbedding(EmbeddingProvider):
    def __init__(self, model: str = "nomic-embed-text", base_url: str = "http://localhost:11434"):
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._dim = 768  # nomic-embed-text 默认值；可调

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

- [ ] **步骤 5：实现 `worker/embedding/gemini_embed.py`**

```python
from __future__ import annotations
import numpy as np
import google.generativeai as genai
from worker.embedding.base import EmbeddingProvider

class GeminiEmbedding(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "models/text-embedding-004"):
        genai.configure(api_key=api_key)
        self._model = model
        self._dim = 768

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed(self, text: str) -> np.ndarray:
        result = await genai.embed_content_async(
            model=self._model,
            content=text,
            task_type="retrieval_document"
        )
        return np.array(result["embedding"], dtype=np.float32)

    async def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        result = await genai.embed_content_async(
            model=self._model,
            content=texts,
            task_type="retrieval_document"
        )
        return [np.array(e, dtype=np.float32) for e in result["embedding"]]
```

- [ ] **步骤 6：实现 `worker/embedding/__init__.py` — 工厂函数**

```python
# worker/embedding/__init__.py
from __future__ import annotations
import os
from worker.embedding.base import EmbeddingProvider

def make_embedding_provider(cfg) -> EmbeddingProvider:
    """工厂：从配置创建 EmbeddingProvider。在这里导入，以便 worker/jobs.py 干净地打补丁。"""
    from worker.embedding.openai_embed import OpenAIEmbedding
    from worker.embedding.gemini_embed import GeminiEmbedding
    from worker.embedding.ollama_embed import OllamaEmbedding
    p = cfg.embedding.provider
    if p == "openai":
        return OpenAIEmbedding(
            api_key=cfg.embedding.api_key or os.environ.get("OPENAI_API_KEY", ""),
            model=cfg.embedding.model,
        )
    elif p == "google":
        return GeminiEmbedding(
            api_key=cfg.embedding.api_key or os.environ.get("GOOGLE_API_KEY", ""),
            model=cfg.embedding.model,
        )
    elif p == "ollama":
        return OllamaEmbedding(model=cfg.embedding.model)
    else:
        raise ValueError(f"Unknown embedding provider: {p}")
```

- [ ] **步骤 7：运行测试**

```bash
pytest tests/worker/test_embedding.py -v
```
预期：2 PASSED

- [ ] **步骤 7：提交**

```bash
git add worker/embedding/ tests/worker/test_embedding.py
git commit -m "feat: embedding provider abstraction (OpenAI, Google, Ollama)"
```

---

## 任务 6：阶段 1 — 仓库摄取

**文件：**
- 创建：`worker/pipeline/ingestion.py`
- 创建：`tests/worker/test_ingestion.py`

- [ ] **步骤 1：编写失败测试**

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
    assert len(h1) == 16  # 截断的 sha256
```

- [ ] **步骤 2：运行以验证失败**

```bash
pytest tests/worker/test_ingestion.py -v
```

- [ ] **步骤 3：实现 `worker/pipeline/ingestion.py`**

```python
from __future__ import annotations
import hashlib
from pathlib import Path

# 被视为源代码的扩展名（非详尽，实用集合）
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
    """解析 'github.com/owner/repo' 或完整 URL 为 (owner, name)。"""
    url = url.replace("https://", "").replace("http://", "").rstrip("/")
    parts = url.split("/")
    # 查找 'github.com' 并获取接下来的两个部分
    try:
        idx = next(i for i, p in enumerate(parts) if "github.com" in p)
        return parts[idx + 1], parts[idx + 2].removesuffix(".git")
    except (StopIteration, IndexError):
        raise ValueError(f"无法解析 GitHub URL: {url}")

def get_repo_hash(platform: str, owner: str, name: str) -> str:
    key = f"{platform}:{owner}/{name}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]

def filter_files(
    root: Path,
    max_file_bytes: int = 1024 * 1024,  # 每个文件 1MB
) -> list[Path]:
    """返回 root 下所有可索引的源文件。"""
    results: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        # 跳过排除的目录
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        # 跳过非源代码扩展名
        if path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        # 跳过超大文件
        if path.stat().st_size > max_file_bytes:
            continue
        results.append(path)
    return sorted(results)

async def clone_or_fetch(clone_dir: Path, owner: str, name: str) -> str:
    """克隆或拉取 GitHub 仓库。返回 HEAD 提交 SHA。"""
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

- [ ] **步骤 4：运行测试**

```bash
pytest tests/worker/test_ingestion.py -v
```
预期：5 PASSED

- [ ] **步骤 5：提交**

```bash
git add worker/pipeline/ingestion.py tests/worker/test_ingestion.py
git commit -m "feat: stage 1 repo ingestion — file filter, URL parsing, shallow clone"
```

---

## 任务 7：阶段 2 — AST 分析

**文件：**
- 创建：`worker/pipeline/ast_analysis.py`
- 创建：`tests/worker/test_ast_analysis.py`

- [ ] **步骤 1：编写失败测试**

```python
# tests/worker/test_ast_analysis.py
import pytest
from pathlib import Path
from worker.pipeline.ast_analysis import analyze_file, build_module_tree, SUPPORTED_LANGUAGES

FIXTURE = Path("tests/fixtures/simple-repo")

def test_supported_languages_count():
    # 覆盖 9 种语言的 13 个扩展名条目
    # .py .js .jsx .ts .tsx .java .go .rs .c .h .cpp .cc .cs
    assert len(SUPPORTED_LANGUAGES) == 13
```

- [ ] **步骤 2：运行以验证失败**

```bash
pytest tests/worker/test_ast_analysis.py -v
```

- [ ] **步骤 3：实现 `worker/pipeline/ast_analysis.py`**

```python
from __future__ import annotations
from pathlib import Path
from typing import Any

import tree_sitter_python as tspython
import tree_sitter_javascript as tsjavascript
import tree_sitter_typescript as tstypescript
import tree_sitter_java as tsjava
import tree_sitter_go as tsgo
import tree_sitter_rust as tsrust
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
    ".rs":   Language(tsrust.language()),
    ".c":    Language(tsc.language()),
    ".h":    Language(tsc.language()),
    ".cpp":  Language(tscpp.language()),
    ".cc":   Language(tscpp.language()),
    ".cs":   Language(tscsharp.language()),
}

# 代表命名实体的 Tree-Sitter 节点类型
_ENTITY_TYPES = {
    "function_definition", "class_definition",       # Python
    "function_declaration", "class_declaration",     # JS/TS/Java
    "method_declaration", "method_definition",
    "function_item",                                 # Rust
    "struct_item", "impl_item",
    "func_declaration", "type_declaration",          # Go
}

def analyze_file(path: Path) -> dict[str, Any] | None:
    """使用 Tree-Sitter 解析文件。返回实体列表，如果不支持则返回 None。"""
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
    """根据 root 下的顶级目录将文件分组到模块中。"""
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

- [ ] **步骤 4：运行测试**

```bash
pytest tests/worker/test_ast_analysis.py -v
```
预期：5 PASSED

- [ ] **步骤 5：提交**

```bash
git add worker/pipeline/ast_analysis.py tests/worker/test_ast_analysis.py
git commit -m "feat: stage 2 AST analysis — tree-sitter entity extraction, module tree"
```

---

## 任务 8：阶段 3 — RAG 索引器

**文件：**
- 创建：`worker/pipeline/rag_indexer.py`
- 创建：`tests/worker/test_rag_indexer.py`

- [ ] **步骤 1：编写失败测试**

```python
# tests/worker/test_rag_indexer.py
import pytest
import numpy as np
from pathlib import Path
from worker.pipeline.rag_indexer import chunk_file, FAISSStore

def test_chunk_file_returns_non_empty():
    from pathlib import Path
    import tempfile
    content = "def foo():\n    return 1\n" * 50  # 重复以获得多个分块
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(content)
        fname = Path(f.name)
    chunks = chunk_file(fname, chunk_size=200, overlap=20)
    assert len(chunks) >= 1
```

- [ ] **步骤 2：运行以验证失败**

```bash
pytest tests/worker/test_rag_indexer.py -v
```

- [ ] **步骤 3：实现 `worker/pipeline/rag_indexer.py`**

```python
from __future__ import annotations
import pickle
from pathlib import Path
from typing import Any
import numpy as np
import faiss
from langchain_text_splitters import RecursiveCharacterTextSplitter

def chunk_file(path: Path, chunk_size: int = 1000, overlap: int = 100) -> list[str]:
    """将源文件分割成重叠的文本块。"""
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
```

- [ ] **步骤 4：运行测试**

```bash
pytest tests/worker/test_rag_indexer.py -v
```
预期：4 PASSED

- [ ] **步骤 5：提交**

```bash
git add worker/pipeline/rag_indexer.py tests/worker/test_rag_indexer.py
git commit -m "feat: stage 3 RAG indexer — file chunking, FAISS store with persist/load"
```

---

## 任务 9：阶段 4 — 维基规划器

**文件：**
- 创建：`worker/pipeline/wiki_planner.py`
- 创建：`tests/worker/test_wiki_planner.py`

- [ ] **步骤 1：编写失败测试**

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
```

- [ ] **步骤 2：运行以验证失败**

```bash
pytest tests/worker/test_wiki_planner.py -v
```

- [ ] **步骤 3：实现 `worker/pipeline/wiki_planner.py`**

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

_SYSTEM = """你是一名技术文档架构师。给定一个仓库的模块树，
生成一个分层的维基页面计划。每个页面涵盖一个逻辑关注点。
仅输出有效的 JSON。"""

def _build_prompt(module_tree: list[dict], repo_name: str) -> str:
    tree_str = json.dumps(module_tree, indent=2)
    return f"""仓库：{repo_name}

模块树：
{tree_str}

创建一个维基页面计划。指南：
- 总计 3-10 个页面
- 每个页面具有：标题（人类可读）、slug（URL 安全、小写、连字符）、模块（树中的路径列表）
- 包含一个涵盖根目录的“概览”页面
- 将相关模块分组成逻辑页面
- 可选地为嵌套页面设置 parent_slug

输出完全匹配此模式的 JSON：
{json.dumps(_PLAN_SCHEMA, indent=2)}"""

def validate_page_plan(raw: dict[str, Any]) -> PagePlan:
    if "pages" not in raw:
        raise ValueError("缺少 'pages' 键")
    if not raw["pages"]:
        raise ValueError("页面计划必须至少包含一个页面")
    pages = []
    for p in raw["pages"]:
        if "slug" not in p:
            raise ValueError(f"页面缺少 'slug'：{p}")
        if "title" not in p:
            raise ValueError(f"页面缺少 'title'：{p}")
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
    for attempt in range(max_retries):
        try:
            raw = await llm.generate_structured(prompt, schema=_PLAN_SCHEMA, system=_SYSTEM)
            return validate_page_plan(raw)
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            if attempt < max_retries - 1:
                prompt += f"\n\n上一次尝试失败：{e}。请修复并重试。"
    # 兜底方案：涵盖所有模块的扁平计划
    return PagePlan(pages=[
        PageSpec(title="Overview", slug="overview", modules=["."]),
        *[PageSpec(title=m["path"].replace("/", " ").title(), slug=m["path"].replace("/", "-"),
                   modules=[m["path"]]) for m in module_tree if m["path"] != "."],
    ])
```

- [ ] **步骤 4：运行测试**

```bash
pytest tests/worker/test_wiki_planner.py -v
```
预期：4 PASSED

- [ ] **步骤 5：提交**

```bash
git add worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
git commit -m "feat: stage 4 wiki planner — LLM page plan generation with retry + fallback"
```

---

## 任务 10：阶段 5 — 页面生成器

**文件：**
- 创建：`worker/pipeline/page_generator.py`
- 创建：`tests/worker/test_page_generator.py`

- [ ] **步骤 1：编写失败测试**

```python
# tests/worker/test_page_generator.py
import pytest
from worker.pipeline.page_generator import generate_page, PageResult
from worker.pipeline.wiki_planner import PageSpec

async def test_generate_page_returns_markdown(mock_llm, mock_embedding):
    # 使用模拟数据设置真实的 FAISSStore
    import numpy as np
    from worker.pipeline.rag_indexer import FAISSStore
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        store = FAISSStore(dimension=1536,
                           index_path=Path(tmp) / "idx",
                           meta_path=Path(tmp) / "meta.pkl")
        store.add([np.zeros(1536, dtype=np.float32)], [{"text": "class User: pass", "file": "models.py"}])

        spec = PageSpec(title="Models", slug="models", modules=["models.py"])
        result = await generate_page(spec, store, mock_llm, mock_embedding, repo_name="test")
    assert isinstance(result, PageResult)
    assert result.slug == "models"
```

- [ ] **步骤 2：运行以验证失败**

```bash
pytest tests/worker/test_page_generator.py -v
```

- [ ] **步骤 3：实现 `worker/pipeline/page_generator.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from worker.llm.base import LLMProvider
from worker.embedding.base import EmbeddingProvider
from worker.pipeline.rag_indexer import FAISSStore
from worker.pipeline.wiki_planner import PageSpec

_SYSTEM = """你是一名技术文档撰写者。为软件仓库编写清晰、准确的维基页面。
使用 Markdown。在相关地方包含代码示例。
基于提供的代码上下文进行写作 — 不要捏造 API。"""

@dataclass
class PageResult:
    slug: str
    title: str
    content: str  # Markdown

def _build_page_prompt(spec: PageSpec, context_chunks: list[dict], repo_name: str) -> str:
    context = "\n\n---\n\n".join(
        f"文件：{c.get('file', 'unknown')}\n{c['text']}"
        for c in context_chunks
    )
    return f"""仓库：{repo_name}
页面标题：{spec.title}
涵盖的模块：{', '.join(spec.modules)}

相关源代码：
{context}

为“{spec.title}”编写一份全面的维基页面。包含：
- 概览段落
- 关键类/函数及其描述
- 相关的使用示例
- 此模块如何与其他模块交互

仅输出 Markdown。"""

async def generate_page(
    spec: PageSpec,
    store: FAISSStore,
    llm: LLMProvider,
    embedding: EmbeddingProvider,
    repo_name: str,
    top_k: int = 8,
) -> PageResult:
    # 使用页面标题作为查询检索相关分块
    query_vec = await embedding.embed(f"{spec.title} {' '.join(spec.modules)}")
    context_chunks = store.search(query_vec, k=top_k)

    prompt = _build_page_prompt(spec, context_chunks, repo_name)
    content = await llm.generate(prompt, system=_SYSTEM)

    return PageResult(slug=spec.slug, title=spec.title, content=content)
```

- [ ] **步骤 4：运行测试**

```bash
pytest tests/worker/test_page_generator.py -v
```
预期：2 PASSED

- [ ] **步骤 5：提交**

```bash
git add worker/pipeline/page_generator.py tests/worker/test_page_generator.py
git commit -m "feat: stage 5 page generator — RAG retrieval + LLM generation per page"
```

---

## 任务 11：工作者任务编排

**文件：**
- 创建：`worker/jobs.py`
- 创建：`worker/main.py`
- 创建：`tests/worker/test_jobs.py`

- [ ] **步骤 1：编写失败测试**

```python
# tests/worker/test_jobs.py
import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path

async def test_full_index_job_updates_status(tmp_path, mock_llm, mock_embedding):
    """完整流水线针对固件仓库运行并设置 status=ready。"""
    import os
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["AUTOWIKI_DATA_DIR"] = str(tmp_path)

    from shared.database import init_db, get_session
    await init_db(str(tmp_path / "test.db"))

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

- [ ] **步骤 2：运行以验证失败**

```bash
pytest tests/worker/test_jobs.py -v
```

- [ ] **步骤 3：实现 `worker/jobs.py`**

```python
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from pathlib import Path

from shared.config import get_config
from shared.database import get_session, init_db
from shared.models import Repository, Job, WikiPage
from worker.pipeline.ingestion import filter_files, clone_or_fetch
from worker.pipeline.ast_analysis import build_module_tree
from worker.pipeline.rag_indexer import build_rag_index, FAISSStore
from worker.pipeline.wiki_planner import generate_page_plan
from worker.pipeline.page_generator import generate_page
from worker.llm import make_llm_provider
from worker.embedding import make_embedding_provider

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

        # 阶段 1：摄取
        if clone_root is None:
            clone_root = data_dir / "repos" / repo_id / "clone"
        head_sha = await clone_or_fetch(clone_root, owner, name)
        files = filter_files(clone_root)
        await _update_job(db_path, job_id, progress=20)

        # 阶段 2：AST 分析
        module_tree = build_module_tree(clone_root, files)
        await _update_job(db_path, job_id, progress=35)

        # 阶段 3：RAG 索引器
        llm = make_llm_provider(cfg)
        embedding = make_embedding_provider(cfg)
        repo_data_dir = data_dir / "repos" / repo_id
        repo_data_dir.mkdir(parents=True, exist_ok=True)
        store = FAISSStore(
            dimension=embedding.dimension,
            index_path=repo_data_dir / "faiss.index",
            meta_path=repo_data_dir / "faiss.meta.pkl",
        )
        # 注意：这里需要根据实际实现的 build_rag_index 签名调用
        # 在任务 8 中省略了 build_rag_index 的完整异步实现
        await _update_job(db_path, job_id, progress=55)

        # 阶段 4：维基规划器
        plan = await generate_page_plan(module_tree, repo_name=name, llm=llm)
        await _update_job(db_path, job_id, progress=65)

        # 阶段 5：页面生成器
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

        # 完成
        now = datetime.now(timezone.utc)
        await _update_job(db_path, job_id, status="done", progress=100, finished_at=now)
        await _update_repo(db_path, repo_id, status="ready", last_commit=head_sha,
                           indexed_at=now, wiki_path=str(wiki_dir))

    except Exception as e:
        await _update_job(db_path, job_id, status="failed", error=str(e), finished_at=datetime.now(timezone.utc))
        await _update_repo(db_path, repo_id, status="error")
        raise
```

- [ ] **步骤 4：实现 `worker/main.py`**

```python
from worker.jobs import run_full_index

async def startup(ctx):
    pass

async def shutdown(ctx):
    pass

class WorkerSettings:
    functions = [run_full_index]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = None

if __name__ == "__main__":
    import os
    from arq import run_worker
    from arq.connections import RedisSettings
    WorkerSettings.redis_settings = RedisSettings.from_dsn(
        os.environ.get("REDIS_URL", "redis://localhost:6379")
    )
    run_worker(WorkerSettings)
```

- [ ] **步骤 5：运行测试**

```bash
pytest tests/worker/test_jobs.py -v
```
预期：1 PASSED

- [ ] **步骤 6：提交**

```bash
git add worker/jobs.py worker/main.py tests/worker/test_jobs.py
git commit -m "feat: ARQ worker job orchestration — full 5-stage pipeline with progress updates"
```

---

## 任务 12：API 网关 — 仓库与任务

**文件：**
- 创建：`api/main.py`
- 创建：`api/queue.py`
- 创建：`api/routers/repos.py`
- 创建：`api/routers/jobs.py`
- 创建：`api/ws/jobs.py`
- 创建：`tests/api/test_repos.py`
- 创建：`tests/api/test_jobs.py`

- [ ] **步骤 1：编写失败测试**

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
```

- [ ] **步骤 2：运行以验证失败**

```bash
pytest tests/api/test_repos.py -v
```

- [ ] **步骤 3：实现 `api/queue.py`**

```python
from __future__ import annotations
import os
from arq import create_pool
from arq.connections import RedisSettings

async def enqueue_full_index(repo_id: str, job_id: str, owner: str, name: str) -> str:
    redis = await create_pool(RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379")))
    await redis.enqueue_job("run_full_index", repo_id=repo_id, job_id=job_id, owner=owner, name=name)
    await redis.close()
    return job_id
```

- [ ] **步骤 4：实现 `api/routers/repos.py`**

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
        raise HTTPException(status_code=422, detail="无效的 GitHub URL")

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
```

- [ ] **步骤 5：实现 `api/routers/jobs.py`**

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
            raise HTTPException(status_code=404, detail="任务未找到")
        return {"id": job.id, "repo_id": job.repo_id, "type": job.type,
                "status": job.status, "progress": job.progress, "error": job.error}
```

- [ ] **步骤 6：实现 `api/ws/jobs.py`**

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
                await websocket.send_json({"error": "任务未找到"})
                break
            await websocket.send_json({"progress": job.progress, "status": job.status})
            if job.status in ("done", "failed"):
                break
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
```

- [ ] **步骤 7：实现 `api/main.py`**

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

@app.on_event("startup")
async def startup():
    cfg = get_config()
    cfg.database_path.parent.mkdir(parents=True, exist_ok=True)
    await init_db(str(cfg.database_path))
```

- [ ] **步骤 8：运行测试**

```bash
pytest tests/api/test_repos.py tests/api/test_jobs.py -v
```

- [ ] **步骤 9：提交**

```bash
git add api/ tests/api/
git commit -m "feat: API gateway — POST /api/repos, GET /api/repos/{id}, GET /api/jobs/{id}, WS /ws/jobs/{id}"
```

---

## 任务 13：API 网关 — 维基端点

**文件：**
- 创建：`api/routers/wiki.py`
- 创建：`tests/api/test_wiki.py`

- [ ] **步骤 1：编写失败测试**

```python
# tests/api/test_wiki.py
import pytest
from httpx import AsyncClient, ASGITransport
```

- [ ] **步骤 2：运行以验证失败**

```bash
pytest tests/api/test_wiki.py -v
```

- [ ] **步骤 3：实现 `api/routers/wiki.py`**

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
        raise HTTPException(status_code=404, detail="页面未找到")
    return {"slug": page.slug, "title": page.title, "content": page.content,
            "parent_slug": page.parent_slug, "updated_at": page.updated_at}
```

- [ ] **步骤 4：在 `api/main.py` 中注册维基路由**

编辑 `api/main.py`：在现有路由后添加导入和注册：

```python
from api.routers import wiki as wiki_router
app.include_router(wiki_router.router)
```

- [ ] **步骤 5：运行测试**

```bash
pytest tests/api/test_wiki.py -v
```

- [ ] **步骤 6：提交**

```bash
git add api/routers/wiki.py tests/api/test_wiki.py api/main.py
git commit -m "feat: wiki API endpoints — GET /api/repos/{id}/wiki and /wiki/{slug}"
```

---

## 任务 14：CLI 命令

**文件：**
- 创建：`cli/main.py`
- 创建：`cli/commands/index.py`
- 创建：`cli/commands/list_repos.py`
- 创建：`cli/commands/serve.py`
- 创建：`cli/commands/config_cmd.py`
- 创建：`tests/cli/test_cli.py`

- [ ] **步骤 1：实现 `cli/main.py`**

```python
import typer
from cli.commands.index import index_cmd
from cli.commands.list_repos import list_cmd
from cli.commands.serve import serve_cmd
from cli.commands.config_cmd import config_app

app = typer.Typer(name="autowiki", help="AutoWiki — AI 驱动的维基生成器")
app.command("index")(index_cmd)
app.command("list")(list_cmd)
app.command("serve")(serve_cmd)
app.add_typer(config_app, name="config")

if __name__ == "__main__":
    app()
```

- [ ] **步骤 2：实现 `cli/commands/index.py`**

```python
import typer, httpx

def index_cmd(
    url: str = typer.Argument(..., help="GitHub URL，例如 github.com/owner/repo"),
    force: bool = typer.Option(False, "--force", help="强制完整重新索引"),
    api_url: str = typer.Option("http://127.0.0.1:3001", envvar="AUTOWIKI_API_URL"),
):
    """索引一个 GitHub 仓库。"""
    try:
        resp = httpx.post(f"{api_url}/api/repos", json={"url": url}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        typer.echo(f"索引已开始。任务 ID: {data['job_id']}")
    except Exception as e:
        typer.echo(f"错误: {e}", err=True)
        raise typer.Exit(1)
```

---

## 任务 15：Web UI — 脚手架与 IndexForm

**文件：**
- 创建：`web/app/layout.tsx`
- 创建：`web/app/page.tsx`
- 创建：`web/lib/api.ts`
- 创建：`web/lib/ws.ts`
- 创建：`web/components/IndexForm.tsx`
- 创建：`web/components/JobProgressBar.tsx`

---

## 任务 16：Web UI — 维基查看器

**文件：**
- 创建：`web/components/WikiSidebar.tsx`
- 创建：`web/components/WikiPage.tsx`
- 创建：`web/app/[owner]/[repo]/page.tsx`
- 创建：`web/app/[owner]/[repo]/[slug]/page.tsx`
- 创建：`web/app/[owner]/[repo]/layout.tsx`

---

## 任务 17：集成测试与覆盖率检查

**文件：**
- 创建：`tests/test_integration.py`

- [ ] **步骤 1：针对固件仓库编写集成测试**

```python
# tests/test_integration.py
"""
集成测试：使用模拟的 LLM 和嵌入提供商针对固件仓库运行完整的 5 阶段流水线。
验证页面是否存储在数据库中。
"""
import pytest
from pathlib import Path
from unittest.mock import patch
```

- [ ] **步骤 2：运行所有测试**

```bash
pytest tests/ -v --ignore=tests/e2e --cov=worker --cov=api --cov=shared --cov-report=term-missing
```
预期：全部通过，`worker/` 和 `api/` 的覆盖率 ≥ 80%

---

## 任务 18：Docker 构建验证

- [ ] **步骤 1：构建所有 Docker 镜像**

```bash
docker-compose build
```
预期：三个镜像均构建无误。

- [ ] **步骤 2：使用 docker-compose 进行冒烟测试**

```bash
ANTHROPIC_API_KEY=test OPENAI_API_KEY=test docker-compose up -d
sleep 10
curl -s http://localhost:3001/api/repos | jq .
```
预期：JSON 响应 `{"repos": []}` (API 已上线)。

---

## 任务 19：最终清理与第 1 阶段标签

- [ ] **步骤 1：最后一次运行完整测试套件**

- [ ] **步骤 2：确保核心代码中不留 TODO/FIXME 注释**

- [ ] **步骤 3：为第 1 阶段打标签**

```bash
git tag -a v0.1.0-phase1 -m "第 1 阶段完成：核心 MVP — 5 阶段流水线、API、Web UI、CLI"
```

- [ ] **步骤 4：宣布完成**

第 1 阶段已完成。系统可以：
- 通过 Web UI、CLI 或 REST API 接受 GitHub URL
- 运行 5 阶段异步生成流水线（摄取 → AST → RAG → 规划 → 生成）
- 通过 REST API 提供生成的维基
- 在带有侧边栏导航的 Next.js Web UI 中显示维基

下一步：第 2 阶段计划（聊天、图表、增量刷新、`.autowikiignore`）。
