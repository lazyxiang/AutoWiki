# 第 2 阶段 — 聊天、图表和刷新实施计划

> **[已完成 — 部分内容已过时]** 已实施并合并（PR #4）。多个领域现在已被取代：
>
> - **`diagram_synthesis.py` / `test_diagram_synthesis.py`**：在本计划中作为第 6 阶段创建，随后在维基规划器改进工作（PR #17）中被移除。这两个文件都不再存在。任何引用这些文件的任务步骤、测试代码或文件结构条目均已作废。
> - **`architecture.mmd`**：曾与图表合成输出一起存储；不再生成。
> - **`module_tree.json`**：在流水线重构工作中由 `wiki_plan.json` 取代。本计划中对 `ast/module_tree.json` 的所有引用均已作废。
> - **`get_affected_modules(changed_files, module_tree)`**：在流水线重构工作中由 `get_affected_pages(changed_files, WikiPlan)` 取代。
> - **阶段编号**：本计划提到“阶段 6（图表合成）”。移除图表合成后，流水线共有 6 个阶段，其中阶段 6 是页面生成。
> - **Next.js 版本**：计划说是 Next.js 15；项目实际使用 Next.js 16.2.1。

> **对于智能体工作者：** 要求的子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 来逐项任务实施此计划。步骤使用复选框 (`- [ ]`) 语法进行跟踪。

**目标：** 为 AutoWiki 添加多轮聊天、Mermaid 图表合成、增量刷新、`.autowikiignore` 过滤，以及相应的 CLI/UI 界面。

**架构：** 六个独立可测试的子系统共享现有的 FastAPI + ARQ + SQLite 技术栈；`ChatSession`/`ChatMessage` 数据库模型是唯一的新的共享数据层。流水线的~~阶段 6（图表合成）~~和刷新任务插入到现有的 `worker/jobs.py` 模式中，而不触动阶段 1-5 的逻辑。聊天 WebSocket 遵循与现有任务进度 WebSocket 相同的模式。

**技术栈：** Python 3.12, FastAPI (WebSocket), ARQ, SQLAlchemy 2.0 async, FAISS, `pathspec` (新), Next.js 16.2.1 / TypeScript, `reactflow` (前端新依赖), `mermaid` (前端新依赖)。

---

## 范围检查

第 2 阶段包含三个在很大程度上独立的子系统，可以作为独立的计划：
- **流水线增强** — `.autowikiignore`、图表合成、增量刷新
- **聊天** — 数据库模型、工作者处理程序、API 端点、CLI、ChatPanel UI
- **依赖图** — 图表 API 端点 + 前端可视化

它们仅共享新的数据库模型（任务 1）和 `GET /api/repos/{repo_id}/graph` 端点（任务 8）。所有其他任务都是独立的。如果您想并行化，请先开始任务 1（阻塞依赖），然后按任何顺序运行任务 2-9。

---

## 文件结构

### 新文件
| 文件 | 职责 |
|---|---|
| `worker/pipeline/diagram_synthesis.py` | 阶段 6：LLM 为每个维基页面生成并验证 Mermaid 图表 |
| `worker/chat.py` | 聊天会话管理 + 基于 RAG 的流式响应生成器 |
| `api/routers/chat.py` | `POST /api/repos/{id}/chat`, `GET …/chat/{sid}`, `WS /ws/repos/{id}/chat/{sid}` |
| `cli/commands/refresh.py` | `autowiki refresh` 命令 |
| `cli/commands/chat_cmd.py` | `autowiki chat` 命令 |
| `web/components/ChatPanel.tsx` | 带有源代码引用的流式多轮聊天 UI |
| `web/components/DependencyGraph.tsx` | 通过 `reactflow` 实现的力导向模块图 |
| `web/app/[owner]/[repo]/chat/page.tsx` | 聊天路由 |
| `web/app/[owner]/[repo]/graph/page.tsx` | 图表路由 |
| `tests/worker/test_diagram_synthesis.py` | 阶段 6 单元测试 |
| `tests/worker/test_chat.py` | 聊天工作者单元测试 |
| `tests/api/test_chat.py` | 聊天 API + WebSocket 测试 |

### 修改的文件
| 文件 | 变化内容 |
|---|---|
| `shared/models.py` | 添加 `ChatSession`, `ChatMessage` ORM 模型 |
| `worker/pipeline/ingestion.py` | 添加 `.autowikiignore` 解析 + `get_changed_files()` + `get_affected_modules()` |
| `worker/jobs.py` | 集成阶段 6；持久化模块树；添加 `run_refresh_index` ARQ 任务 |
| `worker/main.py` | 在 `WorkerSettings.functions` 中注册 `run_refresh_index` |
| `api/routers/repos.py` | 添加 `POST /{repo_id}/refresh` + `GET /{repo_id}/graph` |
| `api/queue.py` | 添加 `enqueue_refresh_index()` |
| `api/main.py` | 注册聊天路由 |
| `cli/main.py` | 注册 `refresh` 和 `chat` 命令 |
| `pyproject.toml` | 添加 `pathspec>=0.12` 依赖 |
| `tests/worker/test_ingestion.py` | 添加 `.autowikiignore` + `get_changed_files` 测试 |
| `tests/worker/test_jobs.py` | 添加阶段 6 集成 + 刷新任务测试 |
| `tests/api/test_repos.py` | 添加刷新 + 图表端点测试 |
| `tests/cli/test_cli.py` | 添加刷新 + 聊天 CLI 测试 |
| `web/package.json` | 添加 `reactflow`, `mermaid` |
| `web/lib/api.ts` | 添加 `createChatSession`, `getChatHistory`, `getGraph`, `submitRefresh` |
| `web/lib/ws.ts` | 添加 `useChatStream` WebSocket 钩子 |

---

## 任务 1：聊天数据库模型

**文件：**
- 修改：`shared/models.py`
- 修改：`tests/test_database.py`

- [ ] **步骤 1：编写失败测试**

```python
# tests/test_database.py — 添加到现有文件
async def test_chat_models_created(tmp_path):
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    from sqlalchemy import inspect, text
    from shared.database import _engines
    engine = _engines[db_path]
    async with engine.connect() as conn:
        tables = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_table_names()
        )
    assert "chat_sessions" in tables
    assert "chat_messages" in tables
    await dispose_db(db_path)
```

- [ ] **步骤 2：运行测试以验证其失败**

```bash
pytest tests/test_database.py::test_chat_models_created -v
```
预期：FAIL — 表中没有 `chat_sessions`

- [ ] **步骤 3：将模型添加到 `shared/models.py`**

在 `WikiPage` 之后追加：

```python
class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    messages: Mapped[list["ChatMessage"]] = relationship("ChatMessage", back_populates="session")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)   # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    session: Mapped["ChatSession"] = relationship("ChatSession", back_populates="messages")
```

- [ ] **步骤 4：运行测试以验证其通过**

```bash
pytest tests/test_database.py::test_chat_models_created -v
```
预期：PASS

- [ ] **步骤 5：提交**

```bash
git add shared/models.py tests/test_database.py
git commit -m "feat: add ChatSession and ChatMessage DB models"
```

---

## 任务 2：`.autowikiignore` 支持

**文件：**
- 修改：`worker/pipeline/ingestion.py`
- 修改：`pyproject.toml`
- 修改：`tests/worker/test_ingestion.py`

- [ ] **步骤 1：编写失败测试**

```python
# tests/worker/test_ingestion.py — 添加到现有测试下方

def test_filter_files_respects_autowikiignore(tmp_path):
    (tmp_path / "main.py").write_text("print('hello')")
    (tmp_path / "test_main.py").write_text("# test")
    (tmp_path / ".autowikiignore").write_text("test_*.py\n")
    files = filter_files(tmp_path, ignore_file=tmp_path / ".autowikiignore")
    names = [f.name for f in files]
    assert "main.py" in names
    assert "test_main.py" not in names

def test_filter_files_ignores_missing_autowikiignore(tmp_path):
    (tmp_path / "main.py").write_text("x = 1")
    # 没有 .autowikiignore — 不应报错，应返回 main.py
    files = filter_files(tmp_path, ignore_file=tmp_path / ".autowikiignore")
    assert any(f.name == "main.py" for f in files)

def test_get_changed_files_returns_diff(tmp_path):
    from worker.pipeline.ingestion import get_changed_files
    import git
    # 初始化一个包含两个提交的裸仓库
    repo = git.Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "test").release()
    repo.config_writer().set_value("user", "email", "t@t.com").release()
    (tmp_path / "a.py").write_text("x = 1")
    repo.index.add(["a.py"])
    c1 = repo.index.commit("first")
    (tmp_path / "b.py").write_text("y = 2")
    repo.index.add(["b.py"])
    c2 = repo.index.commit("second")
    changed = get_changed_files(tmp_path, c1.hexsha, c2.hexsha)
    assert "b.py" in changed

def test_get_affected_modules():
    from worker.pipeline.ingestion import get_affected_modules
    module_tree = [{"path": "api", "files": []}, {"path": "worker", "files": []}]
    affected = get_affected_modules(["api/main.py", "README.md"], module_tree)
    assert "api" in affected
    assert "worker" not in affected
```

- [ ] **步骤 2：运行测试以验证它们失败**

```bash
pytest tests/worker/test_ingestion.py -v -k "autowikiignore or changed_files or affected_modules"
```
预期：FAIL — `filter_files` 没有 `ignore_file` 参数，`get_changed_files` 未定义

- [ ] **步骤 3：将 `pathspec` 添加到 `pyproject.toml`**

在 `dependencies` 列表中添加：
```
"pathspec>=0.12",
```

运行 `pip install -e .` 来安装它。

- [ ] **步骤 4：更新 `worker/pipeline/ingestion.py`**

在顶部添加导入：
```python
import pathspec
```

更新 `filter_files` 的签名和主体：

```python
def filter_files(
    root: Path,
    max_file_bytes: int = 1024 * 1024,
    ignore_file: Path | None = None,
) -> list[Path]:
    """返回 root 下所有可索引的源文件。

    如果 ignore_file 存在且是有效的 .gitignore 样式文件，
    则应用其中的模式来排除额外的路径。
    """
    spec: pathspec.PathSpec | None = None
    if ignore_file is not None and ignore_file.is_file():
        patterns = ignore_file.read_text().splitlines()
        spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)

    results: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        # 跳过排除的目录
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        # 跳过非源代码扩展名
        if path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        # 跳过超大文件
        if path.stat().st_size > max_file_bytes:
            continue
        # 应用 .autowikiignore 模式
        if spec is not None and spec.match_file(str(rel)):
            continue
        results.append(path)
    return sorted(results)
```

在文件末尾添加 `get_changed_files` 和 `get_affected_modules`：

```python
def get_changed_files(clone_dir: Path, old_sha: str, new_sha: str) -> list[str]:
    """返回两个 git SHA 之间变更的文件路径列表。"""
    import git
    repo = git.Repo(clone_dir)
    diff_output = repo.git.diff("--name-only", old_sha, new_sha)
    if not diff_output:
        return []
    return [line for line in diff_output.split("\n") if line.strip()]


def get_affected_modules(changed_files: list[str], module_tree: list[dict]) -> set[str]:
    """返回被 changed_files 触及的模块路径集（来自 module_tree）。"""
    module_paths = {m["path"] for m in module_tree}
    affected: set[str] = set()
    for f in changed_files:
        parts = Path(f).parts
        module = parts[0] if len(parts) > 1 else "."
        if module in module_paths:
            affected.add(module)
    return affected
```

- [ ] **步骤 5：更新 `worker/jobs.py` 以将 `ignore_file` 传递给 `filter_files`**

在 `run_full_index` 中，更改：
```python
files = filter_files(clone_root)
```
为：
```python
autowikiignore = clone_root / ".autowikiignore"
files = filter_files(clone_root, ignore_file=autowikiignore)
```

- [ ] **步骤 6：运行测试以验证它们通过**

```bash
pytest tests/worker/test_ingestion.py -v
```
预期：全部 PASS

- [ ] **步骤 7：提交**

```bash
git add worker/pipeline/ingestion.py worker/jobs.py pyproject.toml tests/worker/test_ingestion.py
git commit -m "feat: add .autowikiignore support and git change-detection helpers"
```

---

## 任务 3：阶段 6 — 图表合成

**文件：**
- 创建：`worker/pipeline/diagram_synthesis.py`
- 创建：`tests/worker/test_diagram_synthesis.py`

- [ ] **步骤 1：编写失败测试**

```python
# tests/worker/test_diagram_synthesis.py
import pytest
from unittest.mock import AsyncMock
from worker.pipeline.diagram_synthesis import synthesize_diagrams, validate_mermaid

def test_validate_mermaid_accepts_valid():
    assert validate_mermaid("graph TD\n  A --> B") is True
    assert validate_mermaid("flowchart LR\n  A --> B") is True
    assert validate_mermaid("classDiagram\n  Animal <|-- Dog") is True

def test_validate_mermaid_rejects_invalid():
    assert validate_mermaid("not a diagram") is False
    assert validate_mermaid("") is False

async def test_synthesize_diagrams_returns_mermaid(mock_llm):
    mock_llm.generate.return_value = "graph TD\n  A[API] --> B[Worker]"
    module_tree = [{"path": "api", "files": ["api/main.py"]},
                   {"path": "worker", "files": ["worker/jobs.py"]}]
    result = await synthesize_diagrams(module_tree, repo_name="myrepo", llm=mock_llm)
    assert result is not None
    assert "graph" in result.lower() or "flowchart" in result.lower()

async def test_synthesize_diagrams_retries_on_invalid(mock_llm):
    # 第一次调用返回无效，第二次调用返回有效
    mock_llm.generate.side_effect = [
        "not valid mermaid",
        "graph TD\n  A --> B",
    ]
    module_tree = [{"path": "src", "files": ["src/main.py"]}]
    result = await synthesize_diagrams(module_tree, repo_name="repo", llm=mock_llm)
    assert result is not None
    assert mock_llm.generate.call_count == 2

async def test_synthesize_diagrams_returns_none_after_max_retries(mock_llm):
    mock_llm.generate.return_value = "not valid"
    module_tree = [{"path": "src", "files": []}]
    result = await synthesize_diagrams(module_tree, repo_name="repo", llm=mock_llm, max_retries=2)
    assert result is None
```

- [ ] **步骤 2：运行测试以验证它们失败**

```bash
pytest tests/worker/test_diagram_synthesis.py -v
```
预期：FAIL — 模块未找到

- [ ] **步骤 3：创建 `worker/pipeline/diagram_synthesis.py`**

```python
from __future__ import annotations
from typing import Any
from worker.llm.base import LLMProvider

_VALID_DIAGRAM_TYPES = (
    "graph ", "flowchart ", "sequencediagram", "classdiagram",
    "erdiagram", "statediagram", "pie ", "gantt",
)

_SYSTEM = """你是一个软件架构图生成器。
仅输出有效的 Mermaid 图表语法。不要包含反引号、
代码栅栏或任何解释 — 只要原始的 Mermaid 代码。"""

_DIAGRAM_PROMPT_TEMPLATE = """仓库：{repo_name}

模块结构：
{module_list}

生成一个 Mermaid 架构图，显示这些模块之间的关系。
使用 `graph TD` 或 `flowchart TD` 格式。将主要模块显示为节点，
并在一个模块依赖或调用另一个模块的地方绘制连线。
保持简洁 — 最多 15 个节点。"""


def validate_mermaid(diagram: str) -> bool:
    """如果图表以已知的 Mermaid 图表类型关键字开头，则返回 True。"""
    if not diagram or not diagram.strip():
        return False
    first_line = diagram.strip().split("\n")[0].strip().lower()
    return any(first_line.startswith(t) for t in _VALID_DIAGRAM_TYPES)


async def synthesize_diagrams(
    module_tree: list[dict[str, Any]],
    repo_name: str,
    llm: LLMProvider,
    max_retries: int = 3,
) -> str | None:
    """要求 LLM 为仓库生成一个 Mermaid 架构图。

    如果输出未能通过 Mermaid 验证，最多重试 `max_retries` 次。
    返回验证通过的图表字符串，如果所有重试都用完则返回 None。
    """
    module_list = "\n".join(
        f"- {m['path']} ({len(m.get('files', []))} 个文件)" for m in module_tree
    )
    prompt = _DIAGRAM_PROMPT_TEMPLATE.format(
        repo_name=repo_name, module_list=module_list
    )
    last_output = ""
    for attempt in range(max_retries):
        if attempt > 0:
            prompt = (
                f"{prompt}\n\n上一次尝试产生了无效的 Mermaid：\n"
                f"{last_output}\n\n请仅输出有效的 Mermaid 语法。"
            )
        last_output = await llm.generate(prompt, system=_SYSTEM)
        if validate_mermaid(last_output.strip()):
            return last_output.strip()
    return None
```

- [ ] **步骤 4：运行测试以验证它们通过**

```bash
pytest tests/worker/test_diagram_synthesis.py -v
```
预期：全部 PASS

- [ ] **步骤 5：提交**

```bash
git add worker/pipeline/diagram_synthesis.py tests/worker/test_diagram_synthesis.py
git commit -m "feat: add Stage 6 diagram synthesis with Mermaid validation"
```

---

## 任务 4：集成阶段 6 + 持久化模块树

**文件：**
- 修改：`worker/jobs.py`
- 修改：`tests/worker/test_jobs.py`

`run_full_index` 的变更：
1. 阶段 2 之后，将 `module_tree` 持久化到 `repos/{repo_id}/ast/module_tree.json`。
2. 阶段 5 页面生成后，调用 `synthesize_diagrams`，如果生成了图表，则将其预置到每个页面的内容中。

实际上，图表合成是针对每个仓库的（一个架构图），而不是针对每个页面的。将其预置到第一个页面（概览），并将其另存为 `repos/{repo_id}/ast/architecture.mmd`。图表端点可以单独提供它。

- [ ] **步骤 1：编写失败测试**

```python
# tests/worker/test_jobs.py — 添加到现有文件
from unittest.mock import patch, AsyncMock
import json

async def test_run_full_index_persists_module_tree(tmp_path, mock_llm, mock_embedding):
    from worker.jobs import run_full_index
    from shared.database import init_db, dispose_db
    from tests.conftest import FIXTURE_REPO

    db_path = str(tmp_path / "test.db")
    await init_db(db_path)

    with patch("worker.jobs.get_config") as mock_cfg, \
         patch("worker.jobs.clone_or_fetch", new_callable=AsyncMock, return_value="abc123"), \
         patch("worker.jobs.make_llm_provider", return_value=mock_llm), \
         patch("worker.jobs.make_embedding_provider", return_value=mock_embedding), \
         patch("worker.jobs.synthesize_diagrams", new_callable=AsyncMock, return_value="graph TD\n  A-->B"):
        cfg = mock_cfg.return_value
        cfg.database_path = tmp_path / "test.db"
        cfg.data_dir = tmp_path
        from shared.models import Repository, Job
        from shared.database import get_session
        import uuid
        repo_id = "test_repo_1"
        job_id = str(uuid.uuid4())
        async with get_session(db_path) as s:
            s.add(Repository(id=repo_id, owner="o", name="r", status="pending"))
            s.add(Job(id=job_id, repo_id=repo_id, type="full_index", status="queued", progress=0))
            await s.commit()
        await run_full_index({}, repo_id=repo_id, job_id=job_id, owner="o", name="r",
                             clone_root=FIXTURE_REPO)

    module_tree_path = tmp_path / "repos" / repo_id / "ast" / "module_tree.json"
    assert module_tree_path.exists()
    tree = json.loads(module_tree_path.read_text())
    assert isinstance(tree, list)
    await dispose_db(db_path)
```

- [ ] **步骤 2：运行测试以验证其失败**

```bash
pytest tests/worker/test_jobs.py::test_run_full_index_persists_module_tree -v
```
预期：FAIL

- [ ] **步骤 3：更新 `worker/jobs.py`**

添加导入：
```python
import json
from worker.pipeline.diagram_synthesis import synthesize_diagrams
```

在 `run_full_index` 中，阶段 2 之后：
```python
        # 阶段 2：AST 分析
        module_tree = build_module_tree(clone_root, files)
        # 为图表 API 和刷新任务持久化模块树
        ast_dir = repo_data_dir / "ast"
        ast_dir.mkdir(parents=True, exist_ok=True)
        (ast_dir / "module_tree.json").write_text(json.dumps(module_tree))
        await _update_job(db_path, job_id, progress=35)
```

阶段 5 之后（替换循环末尾的“Done”块），添加阶段 6：
```python
        # 阶段 6：图表合成
        diagram = await synthesize_diagrams(module_tree, repo_name=name, llm=llm)
        if diagram and plan.pages:
            # 将架构图预置到第一个（概览）页面
            first_slug = plan.pages[0].slug
            async with get_session(db_path) as s:
                from sqlalchemy import select as sa_select
                result = await s.execute(
                    sa_select(WikiPage).where(WikiPage.repo_id == repo_id, WikiPage.slug == first_slug)
                )
                page = result.scalar_one_or_none()
                if page is not None:
                    diagram_block = f"## 架构\n\n```mermaid\n{diagram}\n```\n\n"
                    page.content = diagram_block + page.content
                    await s.commit()
            # 持久化图表文件
            (ast_dir / "architecture.mmd").write_text(diagram)
        await _update_job(db_path, job_id, progress=100)  # 移除旧的 "Done" progress=100 行
```

> **注意：** 移除或替换阶段 5 循环中旧的 `progress=100` 行 — 它现在移到了阶段 6 之后。

同时更新进度区间：阶段 5 循环应进行到 95（而不是 100），为阶段 6 留出空间：
```python
            progress = 65 + int(30 * (i + 1) / total)  # 65→95 (以前是 35)
            await _update_job(db_path, job_id, progress=progress)
```

- [ ] **步骤 4：运行测试以验证它们通过**

```bash
pytest tests/worker/test_jobs.py -v
```
预期：全部 PASS

- [ ] **步骤 5：提交**

```bash
git add worker/jobs.py tests/worker/test_jobs.py
git commit -m "feat: persist module tree and integrate Stage 6 diagram synthesis into pipeline"
```

---

## 任务 5：增量刷新任务

**文件：**
- 修改：`worker/jobs.py`（添加 `run_refresh_index`）
- 修改：`worker/main.py`（注册新任务）
- 修改：`api/queue.py`（添加 `enqueue_refresh_index`）
- 创建：`tests/worker/test_refresh.py`

刷新任务：
1. 克隆/拉取最新的以获取新的 HEAD SHA。
2. 针对存储的 `last_commit` 进行 diff 以找到变更的文件。
3. 将变更的文件映射到受影响的模块。
4. 如果没有受影响的模块：立即更新 SHA 并标记为完成。
5. 重新构建整个 FAISS 索引（对于第 2 阶段，这比部分更新更简单）。
6. 仅为受影响的模块重新规划页面。
7. 删除受影响模块的旧维基页面。插入新页面（阶段 4, 5, 6）。
8. 更新存储的提交 SHA。

- [ ] **步骤 1：编写失败测试**

```python
# tests/worker/test_refresh.py
import pytest
import uuid
import json
from unittest.mock import patch, AsyncMock
from pathlib import Path

async def test_run_refresh_index_no_changes(tmp_path, mock_llm, mock_embedding):
    """如果 HEAD SHA == 存储的 last_commit，任务立即以 status done 完成。"""
    from worker.jobs import run_refresh_index
    from shared.database import init_db, dispose_db, get_session
    from shared.models import Repository, Job

    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    repo_id = "refresh_repo"
    job_id = str(uuid.uuid4())
    async with get_session(db_path) as s:
        s.add(Repository(id=repo_id, owner="o", name="r", status="ready", last_commit="abc123"))
        s.add(Job(id=job_id, repo_id=repo_id, type="refresh", status="queued", progress=0))
        await s.commit()

    with patch("worker.jobs.get_config") as mock_cfg, \
         patch("worker.jobs.clone_or_fetch", new_callable=AsyncMock, return_value="abc123"):
        cfg = mock_cfg.return_value
        cfg.database_path = tmp_path / "test.db"
        cfg.data_dir = tmp_path
        await run_refresh_index({}, repo_id=repo_id, job_id=job_id, owner="o", name="r",
                                clone_root=tmp_path / "clone")

    async with get_session(db_path) as s:
        job = await s.get(Job, job_id)
        assert job.status == "done"
        assert job.progress == 100
    await dispose_db(db_path)


async def test_run_refresh_index_with_changes(tmp_path, mock_llm, mock_embedding):
    """变更的文件触发对受影响模块的重新索引。"""
    from worker.jobs import run_refresh_index
    from shared.database import init_db, dispose_db, get_session
    from shared.models import Repository, Job, WikiPage
    from tests.conftest import FIXTURE_REPO

    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    repo_id = "refresh_repo_2"
    job_id = str(uuid.uuid4())
    old_sha = "old123"
    new_sha = "new456"

    async with get_session(db_path) as s:
        s.add(Repository(id=repo_id, owner="o", name="r", status="ready", last_commit=old_sha))
        s.add(Job(id=job_id, repo_id=repo_id, type="refresh", status="queued", progress=0))
        # 受影响模块的预先存在的维基页面
        s.add(WikiPage(id="p1", repo_id=repo_id, slug="overview", title="Overview",
                       content="old content", page_order=0))
        await s.commit()

    # 写入一个 module_tree.json 以便刷新任务可以读取它
    ast_dir = tmp_path / "repos" / repo_id / "ast"
    ast_dir.mkdir(parents=True)
    (ast_dir / "module_tree.json").write_text(json.dumps([{"path": ".", "files": ["main.py"]}]))

    with patch("worker.jobs.get_config") as mock_cfg, \
         patch("worker.jobs.clone_or_fetch", new_callable=AsyncMock, return_value=new_sha), \
         patch("worker.jobs.get_changed_files", return_value=["main.py"]), \
         patch("worker.jobs.make_llm_provider", return_value=mock_llm), \
         patch("worker.jobs.make_embedding_provider", return_value=mock_embedding), \
         patch("worker.jobs.synthesize_diagrams", new_callable=AsyncMock, return_value=None):
        cfg = mock_cfg.return_value
        cfg.database_path = tmp_path / "test.db"
        cfg.data_dir = tmp_path
        await run_refresh_index({}, repo_id=repo_id, job_id=job_id, owner="o", name="r",
                                clone_root=FIXTURE_REPO)

    async with get_session(db_path) as s:
        from sqlalchemy import select
        job = await s.get(Job, job_id)
        assert job.status == "done"
        repo = await s.get(Repository, repo_id)
        assert repo.last_commit == new_sha
    await dispose_db(db_path)
```

- [ ] **步骤 2：运行测试以验证它们失败**

```bash
pytest tests/worker/test_refresh.py -v
```
预期：FAIL — `run_refresh_index` 未定义

- [ ] **步骤 3：将 `run_refresh_index` 添加到 `worker/jobs.py`**

在顶部添加导入：
```python
from worker.pipeline.ingestion import filter_files, clone_or_fetch, get_changed_files, get_affected_modules
```
（替换现有的 ingestion 导入行）

添加新的任务函数：

```python
async def run_refresh_index(
    ctx: dict,
    repo_id: str,
    job_id: str,
    owner: str,
    name: str,
    clone_root: Path | None = None,
):
    """增量刷新：仅为具有变更文件的模块重新运行流水线。"""
    cfg = get_config()
    db_path = str(cfg.database_path)
    data_dir = cfg.data_dir
    await init_db(db_path)

    try:
        await _update_job(db_path, job_id, status="running", progress=5)

        # 阶段 1：克隆/拉取以获取新的 HEAD
        if clone_root is None:
            clone_root = data_dir / "repos" / repo_id / "clone"
        new_sha = await clone_or_fetch(clone_root, owner, name)

        # 检查是否有任何变化
        async with get_session(db_path) as s:
            repo = await s.get(Repository, repo_id)
            old_sha = repo.last_commit or ""

        if old_sha == new_sha:
            now = datetime.now(timezone.utc)
            await _update_job(db_path, job_id, status="done", progress=100, finished_at=now)
            return

        # 查找变更的文件和受影响的模块
        changed_files = get_changed_files(clone_root, old_sha, new_sha) if old_sha else []
        repo_data_dir = data_dir / "repos" / repo_id
        ast_dir = repo_data_dir / "ast"
        module_tree_path = ast_dir / "module_tree.json"
        if module_tree_path.exists():
            module_tree = json.loads(module_tree_path.read_text())
        else:
            # 没有先前的索引 — 回退到完整索引
            await run_full_index(ctx, repo_id=repo_id, job_id=job_id, owner=owner, name=name,
                                 clone_root=clone_root)
            return

        affected_modules = get_affected_modules(changed_files, module_tree)
        if not affected_modules:
            # 文件发生了变化，但在跟踪的模块之外 — 仅更新 SHA
            now = datetime.now(timezone.utc)
            await _update_job(db_path, job_id, status="done", progress=100, finished_at=now)
            await _update_repo(db_path, repo_id, last_commit=new_sha)
            return

        await _update_job(db_path, job_id, progress=20)

        # 阶段 2：为所有文件重新分析 AST（模块树可能已更改）
        autowikiignore = clone_root / ".autowikiignore"
        files = filter_files(clone_root, ignore_file=autowikiignore)
        module_tree = build_module_tree(clone_root, files)
        ast_dir.mkdir(parents=True, exist_ok=True)
        (ast_dir / "module_tree.json").write_text(json.dumps(module_tree))
        await _update_job(db_path, job_id, progress=35)

        # 阶段 3：从头开始重建 FAISS 索引
        llm = make_llm_provider(cfg)
        embedding = make_embedding_provider(cfg)
        repo_data_dir.mkdir(parents=True, exist_ok=True)
        store = FAISSStore(
            dimension=embedding.dimension,
            index_path=repo_data_dir / "faiss.index",
            meta_path=repo_data_dir / "faiss.meta.pkl",
        )
        await build_rag_index(files, clone_root, store, embedding)
        await _update_job(db_path, job_id, progress=55)

        # 阶段 4–6：仅重新规划和重新生成受影响的模块页面
        affected_module_tree = [m for m in module_tree if m["path"] in affected_modules]
        plan = await generate_page_plan(affected_module_tree, repo_name=name, llm=llm)
        await _update_job(db_path, job_id, progress=65)

        # 删除受影响模块的旧页面（slug 与计划中的 slug 重叠）
        new_slugs = {p.slug for p in plan.pages}
        async with get_session(db_path) as s:
            from sqlalchemy import select as sa_select, delete as sa_delete
            await s.execute(
                sa_delete(WikiPage).where(
                    WikiPage.repo_id == repo_id,
                    WikiPage.slug.in_(new_slugs),
                )
            )
            await s.commit()

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
            progress = 65 + int(30 * (i + 1) / total)
            await _update_job(db_path, job_id, progress=progress)

        # 阶段 6：图表合成（为更改的模块重建）
        diagram = await synthesize_diagrams(module_tree, repo_name=name, llm=llm)
        if diagram:
            (ast_dir / "architecture.mmd").write_text(diagram)

        now = datetime.now(timezone.utc)
        await _update_job(db_path, job_id, status="done", progress=100, finished_at=now)
        await _update_repo(db_path, repo_id, status="ready", last_commit=new_sha, indexed_at=now)

    except Exception as e:
        now = datetime.now(timezone.utc)
        await _update_job(db_path, job_id, status="failed", error=str(e), finished_at=now)
        await _update_repo(db_path, repo_id, status="error")
        raise
```

- [ ] **步骤 4：在 `worker/main.py` 中注册**

```python
from worker.jobs import run_full_index, run_refresh_index

class WorkerSettings:
    functions = [run_full_index, run_refresh_index]
    # ... 其余不变
```

- [ ] **步骤 5：将 `enqueue_refresh_index` 添加到 `api/queue.py`**

```python
async def enqueue_refresh_index(repo_id: str, job_id: str, owner: str, name: str) -> str:
    redis = await create_pool(RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379")))
    await redis.enqueue_job("run_refresh_index", repo_id=repo_id, job_id=job_id, owner=owner, name=name)
    await redis.close()
    return job_id
```

- [ ] **步骤 6：运行测试以验证它们通过**

```bash
pytest tests/worker/test_refresh.py -v
```
预期：全部 PASS

- [ ] **步骤 7：提交**

```bash
git add worker/jobs.py worker/main.py api/queue.py tests/worker/test_refresh.py
git commit -m "feat: add incremental refresh job (commit-SHA-based module re-indexing)"
```

---

## 任务 6：聊天工作者处理程序

**文件：**
- 创建：`worker/chat.py`
- 创建：`tests/worker/test_chat.py`

- [ ] **步骤 1：编写失败测试**

```python
# tests/worker/test_chat.py
import pytest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

async def test_create_chat_session(tmp_path):
    from worker.chat import create_chat_session
    from shared.database import init_db, dispose_db, get_session
    from shared.models import ChatSession

    db_path = str(tmp_path / "test.db")
    # 确保仓库存在以满足外键约束
    from shared.models import Repository
    await init_db(db_path)
    async with get_session(db_path) as s:
        s.add(Repository(id="r1", owner="o", name="n", status="ready"))
        await s.commit()

    session_id = await create_chat_session("r1", db_path)
    assert session_id

    async with get_session(db_path) as s:
        sess = await s.get(ChatSession, session_id)
        assert sess is not None
        assert sess.repo_id == "r1"
    await dispose_db(db_path)
```

- [ ] **步骤 2：运行测试以验证它们失败**

```bash
pytest tests/worker/test_chat.py -v
```
预期：FAIL — 未找到 `worker.chat`

- [ ] **步骤 3：创建 `worker/chat.py`**

```python
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

from sqlalchemy import select
from shared.database import get_session
from shared.models import ChatSession, ChatMessage
from worker.llm.base import LLMProvider
from worker.embedding.base import EmbeddingProvider
from worker.pipeline.rag_indexer import FAISSStore

_SYSTEM = """你是一个软件仓库的技术文档助手。
请使用提供的源代码上下文准确回答问题。
在回答中引用具体的文件名和函数名。
始终引用你提取信息所依据的源文件。"""


async def create_chat_session(repo_id: str, db_path: str) -> str:
    session_id = str(uuid.uuid4())
    async with get_session(db_path) as s:
        s.add(ChatSession(id=session_id, repo_id=repo_id,
                          created_at=datetime.now(timezone.utc)))
        await s.commit()
    return session_id


async def get_chat_history(session_id: str, db_path: str, limit: int = 20) -> list[dict]:
    """返回会话的最多 `limit` 条消息，按时间正序排列。"""
    async with get_session(db_path) as s:
        result = await s.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        messages = result.scalars().all()
    return [{"role": m.role, "content": m.content} for m in reversed(messages)]


async def save_message(session_id: str, role: str, content: str, db_path: str) -> None:
    async with get_session(db_path) as s:
        s.add(ChatMessage(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role=role,
            content=content,
            created_at=datetime.now(timezone.utc),
        ))
        await s.commit()


async def generate_chat_response(
    user_message: str,
    history: list[dict],
    store: FAISSStore,
    llm: LLMProvider,
    embedding: EmbeddingProvider,
    top_k: int = 5,
) -> AsyncIterator[str]:
    """流式传输 LLM 响应，基于 RAG 检索的代码块和对话历史。"""
    query_vec = await embedding.embed(user_message)
    chunks = store.search(query_vec, k=top_k)

    context = "\n\n---\n\n".join(
        f"文件：{c.get('file', 'unknown')}\n{c.get('text', '')}"
        for c in chunks
    )
    history_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in history
    )

    prompt = (
        f"对话历史：\n{history_text}\n\n"
        f"相关源代码：\n{context}\n\n"
        f"用户: {user_message}\n\n"
        "请根据源代码上下文回答。在相关地方引用文件名。"
    )

    async for chunk in llm.generate_stream(prompt, system=_SYSTEM):
        yield chunk
```

- [ ] **步骤 4：运行测试以验证它们通过**

```bash
pytest tests/worker/test_chat.py -v
```
预期：全部 PASS

- [ ] **步骤 5：提交**

```bash
git add worker/chat.py tests/worker/test_chat.py
git commit -m "feat: add RAG-grounded chat worker handler with session persistence"
```

---

## 任务 7：聊天 API 端点

**文件：**
- 创建：`api/routers/chat.py`
- 修改：`api/main.py`
- 创建：`tests/api/test_chat.py`

- [ ] **步骤 1：编写失败测试**

```python
# tests/api/test_chat.py
import pytest
import uuid
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
```

- [ ] **步骤 2：运行以验证失败**

```bash
pytest tests/api/test_chat.py -v
```
预期：FAIL — 路由未注册

- [ ] **步骤 3：实现 `api/routers/chat.py`**

```python
from __future__ import annotations
import uuid
from fastapi import APIRouter, HTTPException, WebSocket
from sqlalchemy import select
from shared.database import get_session
from shared.models import ChatSession, ChatMessage, Repository
from shared.config import get_config
from worker.chat import (
    create_chat_session as _create_session,
    get_chat_history,
    save_message,
    generate_chat_response,
)
from worker.pipeline.rag_indexer import FAISSStore
from worker.llm import make_llm_provider
from worker.embedding import make_embedding_provider

router = APIRouter()


@router.post("/api/repos/{repo_id}/chat", status_code=201)
async def create_chat_session(repo_id: str):
    cfg = get_config()
    db_path = str(cfg.database_path)
    # 验证仓库是否存在
    async with get_session(db_path) as s:
        repo = await s.get(Repository, repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="仓库未找到")
    session_id = await _create_session(repo_id, db_path)
    return {"session_id": session_id}


@router.get("/api/repos/{repo_id}/chat/{session_id}")
async def get_session_history(repo_id: str, session_id: str):
    cfg = get_config()
    db_path = str(cfg.database_path)
    history = await get_chat_history(session_id, db_path, limit=cfg.chat.history_window * 2)
    return {"session_id": session_id, "messages": history}


@router.websocket("/ws/repos/{repo_id}/chat/{session_id}")
async def ws_chat(websocket: WebSocket, repo_id: str, session_id: str):
    cfg = get_config()
    db_path = str(cfg.database_path)
    data_dir = cfg.data_dir

    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            user_message = data.get("content", "").strip()
            if not user_message:
                continue

            await save_message(session_id, "user", user_message, db_path)

            # 历史记录不包括我们刚刚保存的消息（传递 window*2 以获取配对）
            history = await get_chat_history(
                session_id, db_path, limit=cfg.chat.history_window * 2
            )
            history = history[:-1]  # 移除刚刚插入的用户消息

            repo_data_dir = data_dir / "repos" / repo_id
            embedding = make_embedding_provider(cfg)
            store = FAISSStore(
                dimension=embedding.dimension,
                index_path=repo_data_dir / "faiss.index",
                meta_path=repo_data_dir / "faiss.meta.pkl",
            )
            store.load()

            llm = make_llm_provider(cfg)
            response_chunks: list[str] = []
            async for chunk in generate_chat_response(
                user_message, history, store, llm, embedding
            ):
                response_chunks.append(chunk)
                await websocket.send_json({"type": "chunk", "content": chunk})

            full_response = "".join(response_chunks)
            await save_message(session_id, "assistant", full_response, db_path)
            await websocket.send_json({"type": "done"})

    except Exception as e:
        await websocket.send_json({"type": "error", "content": str(e)})
    finally:
        await websocket.close()
```

- [ ] **步骤 4：在 `api/main.py` 中注册**

```python
from api.routers.chat import router as chat_router
# ... 在创建应用 / lifespan 中：
app.include_router(chat_router)
```

- [ ] **步骤 5：运行测试以验证它们通过**

```bash
pytest tests/api/test_chat.py -v
```
预期：全部 PASS

- [ ] **步骤 6：提交**

```bash
git add api/routers/chat.py api/main.py tests/api/test_chat.py
git commit -m "feat: add chat REST and WebSocket endpoints"
```

---

## 任务 8：刷新 + 图表 API 端点

**文件：**
- 修改：`api/routers/repos.py`
- 修改：`tests/api/test_repos.py`

两个新端点：

**`POST /api/repos/{repo_id}/refresh`** — 创建刷新任务，将 `run_refresh_index` 加入队列。返回 `{job_id, status}`。

**`GET /api/repos/{repo_id}/graph`** — 读取 `repos/{repo_id}/ast/module_tree.json` 并返回 `{nodes: [...], edges: []}`。节点包含 `{id, label, file_count}`。第 2 阶段的边为空（尚未进行跨模块导入分析）。

- [ ] **步骤 1：实现 `api/routers/repos.py` 中的端点**

```python
@router.post("/{repo_id}/refresh", status_code=202)
async def refresh_repo(repo_id: str):
    cfg = get_config()
    db_path = str(cfg.database_path)
    async with get_session(db_path) as s:
        repo = await s.get(Repository, repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="仓库未找到")
        if repo.status not in ("ready", "error"):
            raise HTTPException(status_code=409, detail="仓库不处于可刷新状态")
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, repo_id=repo_id, type="refresh", status="queued", progress=0)
        s.add(job)
        repo.status = "indexing"
        await s.commit()
    await enqueue_refresh_index(repo_id, job_id, repo.owner, repo.name)
    return {"repo_id": repo_id, "job_id": job_id, "status": "queued"}


@router.get("/{repo_id}/graph")
async def get_repo_graph(repo_id: str):
    cfg = get_config()
    module_tree_path = cfg.data_dir / "repos" / repo_id / "ast" / "module_tree.json"
    if not module_tree_path.exists():
        raise HTTPException(status_code=404, detail="图表不可用 — 请先运行索引")
    module_tree = _json.loads(module_tree_path.read_text())
    nodes = [
        {"id": m["path"], "label": m["path"], "file_count": len(m.get("files", []))}
        for m in module_tree
    ]
    return {"nodes": nodes, "edges": []}
```

---

## 任务 9：CLI — refresh 和 chat 命令

**文件：**
- 创建：`cli/commands/refresh.py`
- 创建：`cli/commands/chat_cmd.py`
- 修改：`cli/main.py`
- 修改：`tests/cli/test_cli.py`

---

## 任务 10：ChatPanel UI

在编写任何 Next.js 代码之前，请阅读 `node_modules/next/dist/docs/` 以了解与标准 Next.js 训练数据不同的 API 约定（根据 `web/AGENTS.md`）。

**文件：**
- 创建：`web/components/ChatPanel.tsx`
- 创建：`web/app/[owner]/[repo]/chat/page.tsx`
- 修改：`web/lib/api.ts` (添加 `createChatSession`, `getChatHistory`)
- 修改：`web/lib/ws.ts` (添加 `useChatStream`)
- 修改：`web/app/[owner]/[repo]/layout.tsx` (在侧边栏添加聊天链接)

---

## 任务 11：DependencyGraph UI

在编写任何 Next.js / React 代码之前，请重新阅读 `web/AGENTS.md` 中关于 Next.js 16 文档的说明。

**文件：**
- 创建：`web/components/DependencyGraph.tsx`
- 创建：`web/app/[owner]/[repo]/graph/page.tsx`
- 修改：`web/lib/api.ts` (添加 `getRepoGraph`)
- 修改：`web/package.json` (添加 `reactflow`)

---

## 合并兼容性：维基质量 PR (2026-03-25 合并)

在第 2 阶段分出分支后，维基质量改进 PR 已合并到 `main`
(`feat: improve wiki quality with dependency graphs, richer prompts, source annotations, and Mermaid diagrams`)。
在发布前，第 2 阶段分支必须变基到 / 合并 main 分支。

### 质量 PR 添加的内容

| 文件 | 变更 |
|------|--------|
| `worker/pipeline/dependency_graph.py` | **新模块** — 提取导入关系，构建模块集群 |
| `worker/pipeline/ast_analysis.py` | `build_enhanced_module_tree()` (每个模块的实体摘要), `analyze_file()` (按文件提取实体) |
| `worker/pipeline/wiki_planner.py` | `generate_page_plan()` 增加了 `readme`, `dep_summary`, `clusters` 可选参数 |
| `worker/pipeline/page_generator.py` | `generate_page()` 增加了 `dep_info`, `entity_details` 参数；内置每页 Mermaid 提示词 |
| `worker/pipeline/ingestion.py` | 添加了 `extract_readme()` |
| `shared/models.py` | 添加了 `WikiPage.description` 列；采用 `datetime.now(UTC)` 风格 |
| `worker/embedding/base.py` | `embed()` 增加了 `is_code: bool = False` 参数 |
| `worker/jobs.py` | 在 `run_full_index` 中编排所有增强功能 |

### 与第 2 阶段的不兼容性

| 区域 | 严重程度 | 问题 |
|------|----------|-------|
| `worker/jobs.py` | **严重** | 第 2 阶段的 `run_full_index` 丢弃了所有质量增强：没有 `build_enhanced_module_tree`，没有 `dependency_graph`…… |
| `worker/pipeline/ast_analysis.py` | **高** | 第 2 阶段刷新任务仅使用 `build_module_tree` 重新运行阶段 2 — 错过了实体分析 |
| `worker/pipeline/wiki_planner.py` | **中** | 第 2 阶段的 `run_refresh_index` 调用规划器时没有 `readme`/`dep_summary`/`clusters` |

### 合并解决计划

`main` 合并到 `phase2-chat-diagrams-refresh` 时，必须在 `worker/jobs.py` 中进行合并：

**保留自 main：**
- `build_enhanced_module_tree` 调用（阶段 2）
- 建立 `file_entities` 的 `analyze_file` 循环（阶段 2）
- `build_dependency_graph` + `summarize_dependencies`（阶段 2b）
- `extract_readme` 调用（阶段 1）
- 传递 `readme`, `dep_summary`, `clusters` 给 `generate_page_plan`
- 传递 `entity_details`, `dep_info` 给 `generate_page`
- 实体感知 RAG 分块 (`build_rag_index(..., file_entities=file_entities)`)

**保留自第 2 阶段：**
- 透传到 `filter_files` 的 `.autowikiignore`
- 阶段 2 之后的 `ast_dir / "module_tree.json"` 持久化
- 阶段 6 `synthesize_diagrams` 调用和图表预置
- `run_refresh_index` 函数（整个新函数）

**`shared/models.py`：** 接受 main 的 `WikiPage.description` + `datetime.now(UTC)` 风格；保留第 2 阶段的 `ChatSession`/`ChatMessage` 添加内容。

**`worker/pipeline/ingestion.py`：** 保留 main 的 `extract_readme` + 第 2 阶段的 `.autowikiignore` 和 `get_changed_files`/`get_affected_modules`。

---

## 覆盖率与集成验证

所有任务完成后：

- [ ] 运行完整测试套件：
  ```bash
  pytest tests/ --ignore=tests/e2e --cov=worker --cov=api --cov=shared --cov-report=term-missing
  ```
