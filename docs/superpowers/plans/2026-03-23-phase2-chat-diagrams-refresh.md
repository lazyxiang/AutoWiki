# Phase 2 — Chat, Diagrams & Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add multi-turn chat, Mermaid diagram synthesis, incremental refresh, `.autowikiignore` filtering, and matching CLI/UI surfaces to AutoWiki.

**Architecture:** Six independently testable subsystems sharing the existing FastAPI + ARQ + SQLite stack; `ChatSession`/`ChatMessage` DB models are the only new shared data layer. The pipeline's Stage 6 (diagram synthesis) and the refresh job slot into the existing `worker/jobs.py` pattern without touching Stage 1–5 logic. The chat WebSocket follows the same pattern as the existing job-progress WebSocket.

**Tech Stack:** Python 3.12, FastAPI (WebSocket), ARQ, SQLAlchemy 2.0 async, FAISS, `pathspec` (new), Next.js 16.2.1 / TypeScript, `reactflow` (new frontend dep), `mermaid` (new frontend dep).

---

## Scope Check

Phase 2 contains three largely independent subsystems that could be separate plans:
- **Pipeline enhancements** — `.autowikiignore`, diagram synthesis, incremental refresh
- **Chat** — DB models, worker handler, API endpoints, CLI, ChatPanel UI
- **DependencyGraph** — graph API endpoint + frontend visualization

They share only the new DB models (Task 1) and the `GET /api/repos/{repo_id}/graph` endpoint (Task 8). All other tasks are independent. If you want to parallelise, start Task 1 first (blocking dependency), then run Tasks 2–9 in any order.

---

## File Structure

### New Files
| File | Responsibility |
|---|---|
| `worker/pipeline/diagram_synthesis.py` | Stage 6: LLM generates + validates Mermaid diagrams per wiki page |
| `worker/chat.py` | Chat session management + RAG-grounded streaming response generator |
| `api/routers/chat.py` | `POST /api/repos/{id}/chat`, `GET …/chat/{sid}`, `WS /ws/repos/{id}/chat/{sid}` |
| `cli/commands/refresh.py` | `autowiki refresh` command |
| `cli/commands/chat_cmd.py` | `autowiki chat` command |
| `web/components/ChatPanel.tsx` | Streaming multi-turn chat UI with source citations |
| `web/components/DependencyGraph.tsx` | Force-directed module graph via `reactflow` |
| `web/app/[owner]/[repo]/chat/page.tsx` | Chat route |
| `web/app/[owner]/[repo]/graph/page.tsx` | Graph route |
| `tests/worker/test_diagram_synthesis.py` | Stage 6 unit tests |
| `tests/worker/test_chat.py` | Chat worker unit tests |
| `tests/api/test_chat.py` | Chat API + WebSocket tests |

### Modified Files
| File | What Changes |
|---|---|
| `shared/models.py` | Add `ChatSession`, `ChatMessage` ORM models |
| `worker/pipeline/ingestion.py` | Add `.autowikiignore` parsing + `get_changed_files()` + `get_affected_modules()` |
| `worker/jobs.py` | Integrate Stage 6; persist module tree; add `run_refresh_index` ARQ job |
| `worker/main.py` | Register `run_refresh_index` in `WorkerSettings.functions` |
| `api/routers/repos.py` | Add `POST /{repo_id}/refresh` + `GET /{repo_id}/graph` |
| `api/queue.py` | Add `enqueue_refresh_index()` |
| `api/main.py` | Register chat router |
| `cli/main.py` | Register `refresh` and `chat` commands |
| `pyproject.toml` | Add `pathspec>=0.12` dependency |
| `tests/worker/test_ingestion.py` | Add `.autowikiignore` + `get_changed_files` tests |
| `tests/worker/test_jobs.py` | Add Stage 6 integration + refresh job tests |
| `tests/api/test_repos.py` | Add refresh + graph endpoint tests |
| `tests/cli/test_cli.py` | Add refresh + chat CLI tests |
| `web/package.json` | Add `reactflow`, `mermaid` |
| `web/lib/api.ts` | Add `createChatSession`, `getChatHistory`, `getGraph`, `submitRefresh` |
| `web/lib/ws.ts` | Add `useChatStream` WebSocket hook |

---

## Task 1: Chat DB Models

**Files:**
- Modify: `shared/models.py`
- Modify: `tests/test_database.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_database.py — add to existing file
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

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_database.py::test_chat_models_created -v
```
Expected: FAIL — `chat_sessions` not in tables

- [ ] **Step 3: Add models to `shared/models.py`**

Append after `WikiPage`:

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

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_database.py::test_chat_models_created -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add shared/models.py tests/test_database.py
git commit -m "feat: add ChatSession and ChatMessage DB models"
```

---

## Task 2: `.autowikiignore` Support

**Files:**
- Modify: `worker/pipeline/ingestion.py`
- Modify: `pyproject.toml`
- Modify: `tests/worker/test_ingestion.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/worker/test_ingestion.py — add below existing tests

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
    # No .autowikiignore — should not raise, should return main.py
    files = filter_files(tmp_path, ignore_file=tmp_path / ".autowikiignore")
    assert any(f.name == "main.py" for f in files)

def test_get_changed_files_returns_diff(tmp_path):
    from worker.pipeline.ingestion import get_changed_files
    import git
    # init a bare repo with two commits
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

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/worker/test_ingestion.py -v -k "autowikiignore or changed_files or affected_modules"
```
Expected: FAIL — `filter_files` has no `ignore_file` parameter, `get_changed_files` not defined

- [ ] **Step 3: Add `pathspec` to `pyproject.toml`**

In the `dependencies` list, add:
```
"pathspec>=0.12",
```

Run `pip install -e .` to install it.

- [ ] **Step 4: Update `worker/pipeline/ingestion.py`**

Add import at top:
```python
import pathspec
```

Update `filter_files` signature and body:

```python
def filter_files(
    root: Path,
    max_file_bytes: int = 1024 * 1024,
    ignore_file: Path | None = None,
) -> list[Path]:
    """Return all indexable source files under root.

    If ignore_file exists and is a valid .gitignore-style file, patterns in it
    are applied to exclude additional paths.
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
        # Skip excluded directories
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        # Skip non-source extensions
        if path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        # Skip oversized files
        if path.stat().st_size > max_file_bytes:
            continue
        # Apply .autowikiignore patterns
        if spec is not None and spec.match_file(str(rel)):
            continue
        results.append(path)
    return sorted(results)
```

Add `get_changed_files` and `get_affected_modules` at the end of the file:

```python
def get_changed_files(clone_dir: Path, old_sha: str, new_sha: str) -> list[str]:
    """Return list of file paths changed between two git SHAs."""
    import git
    repo = git.Repo(clone_dir)
    diff_output = repo.git.diff("--name-only", old_sha, new_sha)
    if not diff_output:
        return []
    return [line for line in diff_output.split("\n") if line.strip()]


def get_affected_modules(changed_files: list[str], module_tree: list[dict]) -> set[str]:
    """Return the set of module paths (from module_tree) touched by changed_files."""
    module_paths = {m["path"] for m in module_tree}
    affected: set[str] = set()
    for f in changed_files:
        parts = Path(f).parts
        module = parts[0] if len(parts) > 1 else "."
        if module in module_paths:
            affected.add(module)
    return affected
```

- [ ] **Step 5: Update `worker/jobs.py` to pass `ignore_file` to `filter_files`**

In `run_full_index`, change:
```python
files = filter_files(clone_root)
```
to:
```python
autowikiignore = clone_root / ".autowikiignore"
files = filter_files(clone_root, ignore_file=autowikiignore)
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/worker/test_ingestion.py -v
```
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add worker/pipeline/ingestion.py worker/jobs.py pyproject.toml tests/worker/test_ingestion.py
git commit -m "feat: add .autowikiignore support and git change-detection helpers"
```

---

## Task 3: Stage 6 — Diagram Synthesis

**Files:**
- Create: `worker/pipeline/diagram_synthesis.py`
- Create: `tests/worker/test_diagram_synthesis.py`

- [ ] **Step 1: Write the failing tests**

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
    # First call returns invalid, second returns valid
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

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/worker/test_diagram_synthesis.py -v
```
Expected: FAIL — module not found

- [ ] **Step 3: Create `worker/pipeline/diagram_synthesis.py`**

```python
from __future__ import annotations
from typing import Any
from worker.llm.base import LLMProvider

_VALID_DIAGRAM_TYPES = (
    "graph ", "flowchart ", "sequencediagram", "classdiagram",
    "erdiagram", "statediagram", "pie ", "gantt",
)

_SYSTEM = """You are a software architecture diagram generator.
Output ONLY valid Mermaid diagram syntax. Do not include backticks,
code fences, or any explanation — just the raw Mermaid code."""

_DIAGRAM_PROMPT_TEMPLATE = """Repository: {repo_name}

Module structure:
{module_list}

Generate a Mermaid architecture diagram showing the relationships between
these modules. Use `graph TD` or `flowchart TD` format. Show the main
modules as nodes and draw edges where one module depends on or calls another.
Keep it concise — maximum 15 nodes."""


def validate_mermaid(diagram: str) -> bool:
    """Return True if diagram starts with a known Mermaid diagram type keyword."""
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
    """Ask the LLM to generate a Mermaid architecture diagram for the repo.

    Retries up to `max_retries` times if the output fails Mermaid validation.
    Returns the validated diagram string, or None if all retries are exhausted.
    """
    module_list = "\n".join(
        f"- {m['path']} ({len(m.get('files', []))} files)" for m in module_tree
    )
    prompt = _DIAGRAM_PROMPT_TEMPLATE.format(
        repo_name=repo_name, module_list=module_list
    )
    last_output = ""
    for attempt in range(max_retries):
        if attempt > 0:
            prompt = (
                f"{prompt}\n\nPrevious attempt produced invalid Mermaid:\n"
                f"{last_output}\n\nPlease output valid Mermaid syntax only."
            )
        last_output = await llm.generate(prompt, system=_SYSTEM)
        if validate_mermaid(last_output.strip()):
            return last_output.strip()
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/worker/test_diagram_synthesis.py -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/diagram_synthesis.py tests/worker/test_diagram_synthesis.py
git commit -m "feat: add Stage 6 diagram synthesis with Mermaid validation"
```

---

## Task 4: Integrate Stage 6 + Persist Module Tree

**Files:**
- Modify: `worker/jobs.py`
- Modify: `tests/worker/test_jobs.py`

The changes to `run_full_index`:
1. After Stage 2, persist `module_tree` to `repos/{repo_id}/ast/module_tree.json`.
2. After Stage 5 page generation, call `synthesize_diagrams` and prepend the diagram to each page's content if one was generated.

Actually, diagram synthesis is per-repo (one architecture diagram), not per-page. Prepend it to the first page (overview) and also store it as `repos/{repo_id}/ast/architecture.mmd`. The diagram endpoint can serve it separately.

- [ ] **Step 1: Write the failing test**

```python
# tests/worker/test_jobs.py — add to existing file
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

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/worker/test_jobs.py::test_run_full_index_persists_module_tree -v
```
Expected: FAIL

- [ ] **Step 3: Update `worker/jobs.py`**

Add import:
```python
import json
from worker.pipeline.diagram_synthesis import synthesize_diagrams
```

In `run_full_index`, after Stage 2:
```python
        # Stage 2: AST Analysis
        module_tree = build_module_tree(clone_root, files)
        # Persist module tree for graph API and refresh jobs
        ast_dir = repo_data_dir / "ast"
        ast_dir.mkdir(parents=True, exist_ok=True)
        (ast_dir / "module_tree.json").write_text(json.dumps(module_tree))
        await _update_job(db_path, job_id, progress=35)
```

After Stage 5 (replace the "Done" block at the end of the loop), add Stage 6:
```python
        # Stage 6: Diagram Synthesis
        diagram = await synthesize_diagrams(module_tree, repo_name=name, llm=llm)
        if diagram and plan.pages:
            # Prepend architecture diagram to the first (overview) page
            first_slug = plan.pages[0].slug
            async with get_session(db_path) as s:
                from sqlalchemy import select as sa_select
                result = await s.execute(
                    sa_select(WikiPage).where(WikiPage.repo_id == repo_id, WikiPage.slug == first_slug)
                )
                page = result.scalar_one_or_none()
                if page is not None:
                    diagram_block = f"## Architecture\n\n```mermaid\n{diagram}\n```\n\n"
                    page.content = diagram_block + page.content
                    await s.commit()
            # Persist diagram file
            (ast_dir / "architecture.mmd").write_text(diagram)
        await _update_job(db_path, job_id, progress=100)  # remove the old "Done" progress=100 line
```

> **Note:** Remove or replace the old `progress=100` line in the Stage 5 loop — it now moves to after Stage 6.

Also update the progress bands: Stage 5 loop should go to 95 (not 100), leaving room for Stage 6:
```python
            progress = 65 + int(30 * (i + 1) / total)  # 65→95 (was 35)
            await _update_job(db_path, job_id, progress=progress)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/worker/test_jobs.py -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add worker/jobs.py tests/worker/test_jobs.py
git commit -m "feat: persist module tree and integrate Stage 6 diagram synthesis into pipeline"
```

---

## Task 5: Incremental Refresh Job

**Files:**
- Modify: `worker/jobs.py` (add `run_refresh_index`)
- Modify: `worker/main.py` (register new job)
- Modify: `api/queue.py` (add `enqueue_refresh_index`)
- Create: `tests/worker/test_refresh.py`

The refresh job:
1. Clone/fetch latest to get new HEAD SHA.
2. Diff against stored `last_commit` to find changed files.
3. Map changed files to affected modules.
4. If no affected modules: update SHA and mark done immediately.
5. Rebuild the entire FAISS index (simpler than partial update for Phase 2).
6. Re-plan pages for affected modules only.
7. Delete old wiki pages for affected modules. Insert new ones (Stages 4, 5, 6).
8. Update stored commit SHA.

- [ ] **Step 1: Write the failing tests**

```python
# tests/worker/test_refresh.py
import pytest
import uuid
import json
from unittest.mock import patch, AsyncMock
from pathlib import Path

async def test_run_refresh_index_no_changes(tmp_path, mock_llm, mock_embedding):
    """If HEAD SHA == stored last_commit, job completes with status done immediately."""
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
    """Changed files trigger re-indexing of affected modules."""
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
        # Pre-existing wiki page for the affected module
        s.add(WikiPage(id="p1", repo_id=repo_id, slug="overview", title="Overview",
                       content="old content", page_order=0))
        await s.commit()

    # Write a module_tree.json so the refresh can read it
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

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/worker/test_refresh.py -v
```
Expected: FAIL — `run_refresh_index` not defined

- [ ] **Step 3: Add `run_refresh_index` to `worker/jobs.py`**

Add imports at top:
```python
from worker.pipeline.ingestion import filter_files, clone_or_fetch, get_changed_files, get_affected_modules
```
(replace existing ingestion import line)

Add the new job function:

```python
async def run_refresh_index(
    ctx: dict,
    repo_id: str,
    job_id: str,
    owner: str,
    name: str,
    clone_root: Path | None = None,
):
    """Incremental refresh: re-run pipeline only for modules with changed files."""
    cfg = get_config()
    db_path = str(cfg.database_path)
    data_dir = cfg.data_dir
    await init_db(db_path)

    try:
        await _update_job(db_path, job_id, status="running", progress=5)

        # Stage 1: Clone/fetch to get new HEAD
        if clone_root is None:
            clone_root = data_dir / "repos" / repo_id / "clone"
        new_sha = await clone_or_fetch(clone_root, owner, name)

        # Check if anything changed
        async with get_session(db_path) as s:
            repo = await s.get(Repository, repo_id)
            old_sha = repo.last_commit or ""

        if old_sha == new_sha:
            now = datetime.now(timezone.utc)
            await _update_job(db_path, job_id, status="done", progress=100, finished_at=now)
            return

        # Find changed files and affected modules
        changed_files = get_changed_files(clone_root, old_sha, new_sha) if old_sha else []
        repo_data_dir = data_dir / "repos" / repo_id
        ast_dir = repo_data_dir / "ast"
        module_tree_path = ast_dir / "module_tree.json"
        if module_tree_path.exists():
            module_tree = json.loads(module_tree_path.read_text())
        else:
            # No prior index — fall back to full index
            await run_full_index(ctx, repo_id=repo_id, job_id=job_id, owner=owner, name=name,
                                 clone_root=clone_root)
            return

        affected_modules = get_affected_modules(changed_files, module_tree)
        if not affected_modules:
            # Files changed but outside tracked modules — update SHA only
            now = datetime.now(timezone.utc)
            await _update_job(db_path, job_id, status="done", progress=100, finished_at=now)
            await _update_repo(db_path, repo_id, last_commit=new_sha)
            return

        await _update_job(db_path, job_id, progress=20)

        # Stage 2: Re-analyze AST for all files (module tree may have changed)
        autowikiignore = clone_root / ".autowikiignore"
        files = filter_files(clone_root, ignore_file=autowikiignore)
        module_tree = build_module_tree(clone_root, files)
        ast_dir.mkdir(parents=True, exist_ok=True)
        (ast_dir / "module_tree.json").write_text(json.dumps(module_tree))
        await _update_job(db_path, job_id, progress=35)

        # Stage 3: Rebuild FAISS index from scratch
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

        # Stages 4–6: Re-plan and regenerate only affected module pages
        affected_module_tree = [m for m in module_tree if m["path"] in affected_modules]
        plan = await generate_page_plan(affected_module_tree, repo_name=name, llm=llm)
        await _update_job(db_path, job_id, progress=65)

        # Delete old pages for affected modules (slugs overlap with plan slugs)
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

        # Stage 6: Diagram Synthesis (rebuild for changed modules)
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

- [ ] **Step 4: Register in `worker/main.py`**

```python
from worker.jobs import run_full_index, run_refresh_index

class WorkerSettings:
    functions = [run_full_index, run_refresh_index]
    # ... rest unchanged
```

- [ ] **Step 5: Add `enqueue_refresh_index` to `api/queue.py`**

```python
async def enqueue_refresh_index(repo_id: str, job_id: str, owner: str, name: str) -> str:
    redis = await create_pool(RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379")))
    await redis.enqueue_job("run_refresh_index", repo_id=repo_id, job_id=job_id, owner=owner, name=name)
    await redis.close()
    return job_id
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/worker/test_refresh.py -v
```
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add worker/jobs.py worker/main.py api/queue.py tests/worker/test_refresh.py
git commit -m "feat: add incremental refresh job (commit-SHA-based module re-indexing)"
```

---

## Task 6: Chat Worker Handler

**Files:**
- Create: `worker/chat.py`
- Create: `tests/worker/test_chat.py`

- [ ] **Step 1: Write the failing tests**

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
    # Ensure repository exists for FK constraint
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


async def test_get_chat_history_ordered(tmp_path):
    from worker.chat import create_chat_session, save_message, get_chat_history
    from shared.database import init_db, dispose_db, get_session
    from shared.models import Repository

    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    async with get_session(db_path) as s:
        s.add(Repository(id="r2", owner="o", name="n", status="ready"))
        await s.commit()

    session_id = await create_chat_session("r2", db_path)
    await save_message(session_id, "user", "hello", db_path)
    await save_message(session_id, "assistant", "hi there", db_path)

    history = await get_chat_history(session_id, db_path, limit=10)
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"
    await dispose_db(db_path)


async def test_generate_chat_response_streams(mock_llm, mock_embedding):
    from worker.chat import generate_chat_response
    from worker.pipeline.rag_indexer import FAISSStore
    import numpy as np

    mock_llm.generate_stream = AsyncMock()

    async def _fake_stream(*args, **kwargs):
        for chunk in ["Hello", " world"]:
            yield chunk

    mock_llm.generate_stream = _fake_stream

    store = AsyncMock()
    store.search.return_value = [{"file": "main.py", "text": "def foo(): pass"}]

    chunks = []
    async for chunk in generate_chat_response(
        user_message="What does foo do?",
        history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        store=store,
        llm=mock_llm,
        embedding=mock_embedding,
    ):
        chunks.append(chunk)
    assert "".join(chunks) == "Hello world"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/worker/test_chat.py -v
```
Expected: FAIL — `worker.chat` not found

- [ ] **Step 3: Create `worker/chat.py`**

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

_SYSTEM = """You are a technical documentation assistant for a software repository.
Answer questions precisely using the provided source code context.
Reference specific file names and function names in your answers.
Always cite the source files you draw information from."""


async def create_chat_session(repo_id: str, db_path: str) -> str:
    session_id = str(uuid.uuid4())
    async with get_session(db_path) as s:
        s.add(ChatSession(id=session_id, repo_id=repo_id,
                          created_at=datetime.now(timezone.utc)))
        await s.commit()
    return session_id


async def get_chat_history(session_id: str, db_path: str, limit: int = 20) -> list[dict]:
    """Return up to `limit` messages for a session, oldest first."""
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
    """Stream an LLM response grounded in RAG-retrieved code chunks and conversation history."""
    query_vec = await embedding.embed(user_message)
    chunks = store.search(query_vec, k=top_k)

    context = "\n\n---\n\n".join(
        f"File: {c.get('file', 'unknown')}\n{c.get('text', '')}"
        for c in chunks
    )
    history_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in history
    )

    prompt = (
        f"Conversation history:\n{history_text}\n\n"
        f"Relevant source code:\n{context}\n\n"
        f"USER: {user_message}\n\n"
        "Answer based on the source code context. Cite file names where relevant."
    )

    async for chunk in llm.generate_stream(prompt, system=_SYSTEM):
        yield chunk
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/worker/test_chat.py -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add worker/chat.py tests/worker/test_chat.py
git commit -m "feat: add RAG-grounded chat worker handler with session persistence"
```

---

## Task 7: Chat API Endpoints

**Files:**
- Create: `api/routers/chat.py`
- Modify: `api/main.py`
- Create: `tests/api/test_chat.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_chat.py
import pytest
import uuid
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

@pytest.fixture
def client(tmp_path):
    from shared.database import init_db, dispose_db
    from shared.models import Repository
    import asyncio
    db_path = str(tmp_path / "test.db")

    async def _setup():
        from shared.database import get_session
        await init_db(db_path)
        async with get_session(db_path) as s:
            s.add(Repository(id="r1", owner="owner", name="repo", status="ready"))
            await s.commit()

    asyncio.get_event_loop().run_until_complete(_setup())

    with patch("shared.config._config", None), \
         patch("api.routers.chat.get_config") as mock_cfg:
        mock_cfg.return_value.database_path = tmp_path / "test.db"
        mock_cfg.return_value.data_dir = tmp_path
        mock_cfg.return_value.chat.history_window = 10
        from api.main import app
        yield TestClient(app)

    asyncio.get_event_loop().run_until_complete(dispose_db(db_path))


def test_create_chat_session(client):
    resp = client.post("/api/repos/r1/chat")
    assert resp.status_code == 201
    body = resp.json()
    assert "session_id" in body


def test_get_chat_history_empty(client):
    resp = client.post("/api/repos/r1/chat")
    session_id = resp.json()["session_id"]
    resp2 = client.get(f"/api/repos/r1/chat/{session_id}")
    assert resp2.status_code == 200
    assert resp2.json()["messages"] == []


def test_create_chat_session_missing_repo(client):
    resp = client.post("/api/repos/nonexistent/chat")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/api/test_chat.py -v
```
Expected: FAIL — routes not registered

- [ ] **Step 3: Create `api/routers/chat.py`**

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
    # Verify repo exists
    async with get_session(db_path) as s:
        repo = await s.get(Repository, repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="Repository not found")
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

            # History excludes the message we just saved (pass window*2 to get pairs)
            history = await get_chat_history(
                session_id, db_path, limit=cfg.chat.history_window * 2
            )
            history = history[:-1]  # remove the user message just inserted

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

- [ ] **Step 4: Register in `api/main.py`**

```python
from api.routers.chat import router as chat_router
# ... in create_app / lifespan:
app.include_router(chat_router)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/api/test_chat.py -v
```
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add api/routers/chat.py api/main.py tests/api/test_chat.py
git commit -m "feat: add chat REST and WebSocket endpoints"
```

---

## Task 8: Refresh + Graph API Endpoints

**Files:**
- Modify: `api/routers/repos.py`
- Modify: `tests/api/test_repos.py`

Two new endpoints:

**`POST /api/repos/{repo_id}/refresh`** — Creates a refresh job, enqueues `run_refresh_index`. Returns `{job_id, status}`.

**`GET /api/repos/{repo_id}/graph`** — Reads `repos/{repo_id}/ast/module_tree.json` and returns `{nodes: [...], edges: []}`. Nodes have `{id, label, file_count}`. Edges are empty for Phase 2 (no cross-module import analysis yet).

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_repos.py — add below existing tests

async def test_refresh_repo_returns_job(tmp_path):
    """POST /refresh on a ready repo returns 202 with job_id."""
    from shared.database import init_db, dispose_db, get_session
    from shared.models import Repository
    import asyncio

    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    repo_id = "rr1"
    async with get_session(db_path) as s:
        s.add(Repository(id=repo_id, owner="psf", name="requests", status="ready",
                         last_commit="abc123"))
        await s.commit()

    with patch("shared.config._config", None), \
         patch("api.routers.repos.get_config") as mock_cfg, \
         patch("api.routers.repos.enqueue_refresh_index", new_callable=AsyncMock):
        mock_cfg.return_value.database_path = tmp_path / "test.db"
        from api.main import app
        from starlette.testclient import TestClient
        with TestClient(app) as c:
            resp = c.post(f"/api/repos/{repo_id}/refresh")

    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body
    assert body["status"] == "queued"
    await dispose_db(db_path)


def test_get_graph_returns_nodes(client, tmp_path):
    import json
    resp = client.post("/api/repos", json={"url": "https://github.com/psf/requests"})
    repo_id = resp.json()["repo_id"]

    # Create a mock module_tree.json
    with patch("api.routers.repos.get_config") as mock_cfg:
        mock_cfg.return_value.database_path = tmp_path / "test.db"
        mock_cfg.return_value.data_dir = tmp_path
        ast_dir = tmp_path / "repos" / repo_id / "ast"
        ast_dir.mkdir(parents=True)
        (ast_dir / "module_tree.json").write_text(
            json.dumps([{"path": "api", "files": ["api/main.py"]},
                        {"path": "worker", "files": ["worker/jobs.py"]}])
        )
        resp2 = client.get(f"/api/repos/{repo_id}/graph")
    assert resp2.status_code == 200
    body = resp2.json()
    assert "nodes" in body
    assert len(body["nodes"]) == 2
    assert body["nodes"][0]["id"] in ("api", "worker")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/api/test_repos.py -v -k "refresh or graph"
```
Expected: FAIL

- [ ] **Step 3: Update `api/routers/repos.py`**

Add imports:
```python
import json as _json
from api.queue import enqueue_full_index, enqueue_refresh_index
```

Add endpoints:

```python
@router.post("/{repo_id}/refresh", status_code=202)
async def refresh_repo(repo_id: str):
    cfg = get_config()
    db_path = str(cfg.database_path)
    async with get_session(db_path) as s:
        repo = await s.get(Repository, repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="Repository not found")
        if repo.status not in ("ready", "error"):
            raise HTTPException(status_code=409, detail="Repository is not in a refreshable state")
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, repo_id=repo_id, type="refresh", status="queued", progress=0)
        s.add(job)
        await _update_repo_status(s, repo, "indexing")
        await s.commit()
    await enqueue_refresh_index(repo_id, job_id, repo.owner, repo.name)
    return {"repo_id": repo_id, "job_id": job_id, "status": "queued"}


@router.get("/{repo_id}/graph")
async def get_repo_graph(repo_id: str):
    cfg = get_config()
    module_tree_path = cfg.data_dir / "repos" / repo_id / "ast" / "module_tree.json"
    if not module_tree_path.exists():
        raise HTTPException(status_code=404, detail="Graph not available — run index first")
    module_tree = _json.loads(module_tree_path.read_text())
    nodes = [
        {"id": m["path"], "label": m["path"], "file_count": len(m.get("files", []))}
        for m in module_tree
    ]
    return {"nodes": nodes, "edges": []}
```

Also extract a small `_update_repo_status` helper or inline it — `refresh_repo` needs to set status without the full `_update_repo` helper since we're inside an existing session. Inline is fine:

```python
# Inside refresh_repo, replace _update_repo_status(s, repo, "indexing") with:
repo.status = "indexing"
```

- [ ] **Step 4: Run the full test suite**

```bash
pytest tests/api/ -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add api/routers/repos.py tests/api/test_repos.py
git commit -m "feat: add refresh trigger and graph data endpoints"
```

---

## Task 9: CLI — refresh and chat Commands

**Files:**
- Create: `cli/commands/refresh.py`
- Create: `cli/commands/chat_cmd.py`
- Modify: `cli/main.py`
- Modify: `tests/cli/test_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/cli/test_cli.py — add below existing tests
from unittest.mock import patch, MagicMock
import httpx

def test_refresh_cmd_success(runner):
    with patch("cli.commands.refresh.httpx.post") as mock_post, \
         patch("cli.commands.refresh.httpx.get") as mock_get:
        mock_post.return_value = MagicMock(
            status_code=202,
            json=lambda: {"repo_id": "abc", "job_id": "job1", "status": "queued"}
        )
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"status": "done", "progress": 100}
        )
        result = runner.invoke(app, ["refresh", "github.com/psf/requests"])
    assert result.exit_code == 0
    assert "Refresh complete" in result.output


def test_chat_cmd_prints_response(runner):
    # httpx.post is called twice: create session, (none after that — WS does the chat)
    # The CLI uses websockets for the actual chat; mock asyncio.run to skip WS
    with patch("cli.commands.chat_cmd.httpx.get") as mock_get, \
         patch("cli.commands.chat_cmd.httpx.post") as mock_post, \
         patch("cli.commands.chat_cmd.asyncio.run", return_value="It does foo things."):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "r1", "status": "ready"}
        )
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {"session_id": "s1"}
        )
        result = runner.invoke(app, ["chat", "github.com/psf/requests", "What does foo do?"])
    assert result.exit_code == 0
    assert "It does foo things." in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/cli/test_cli.py -v -k "refresh_cmd or chat_cmd"
```
Expected: FAIL

Note: `websockets` is already in `pyproject.toml` (`websockets>=13.0`) — no new dependency needed for the CLI.

Note: `chat.history_window` is already defined in `shared/config.py` under `ChatConfig` with default `10` — no config changes needed.

- [ ] **Step 3: Create `cli/commands/refresh.py`**

```python
from __future__ import annotations
import hashlib
import time
import typer
import httpx
from worker.pipeline.ingestion import parse_github_url

API_BASE = "http://localhost:3001"


def refresh_cmd(url: str = typer.Argument(..., help="GitHub repo URL")):
    """Trigger incremental refresh for an indexed repository."""
    try:
        owner, name = parse_github_url(url)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    repo_id = hashlib.sha256(f"github:{owner}/{name}".encode()).hexdigest()[:16]
    resp = httpx.post(f"{API_BASE}/api/repos/{repo_id}/refresh")
    if resp.status_code == 404:
        typer.echo("Repository not found. Run `autowiki index` first.", err=True)
        raise typer.Exit(1)
    if resp.status_code == 409:
        typer.echo("Repository is currently being indexed. Try again later.", err=True)
        raise typer.Exit(1)
    resp.raise_for_status()

    job_id = resp.json()["job_id"]
    typer.echo(f"Refresh job queued: {job_id}")

    with typer.progressbar(length=100, label="Refreshing") as progress:
        last = 0
        while True:
            status_resp = httpx.get(f"{API_BASE}/api/jobs/{job_id}")
            status_resp.raise_for_status()
            data = status_resp.json()
            current = data.get("progress", 0)
            progress.update(current - last)
            last = current
            if data.get("status") in ("done", "failed"):
                break
            time.sleep(2)

    if data.get("status") == "failed":
        typer.echo(f"\nRefresh failed: {data.get('error', 'unknown error')}", err=True)
        raise typer.Exit(1)
    typer.echo("\nRefresh complete.")
```

- [ ] **Step 4: Create `cli/commands/chat_cmd.py`**

```python
from __future__ import annotations
import hashlib
import typer
import httpx
from worker.pipeline.ingestion import parse_github_url

API_BASE = "http://localhost:3001"


def chat_cmd(
    url: str = typer.Argument(..., help="GitHub repo URL"),
    question: str = typer.Argument(..., help="Question to ask about the repository"),
):
    """Ask a single question about an indexed repository."""
    try:
        owner, name = parse_github_url(url)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    repo_id = hashlib.sha256(f"github:{owner}/{name}".encode()).hexdigest()[:16]

    # Verify repo exists and is ready
    repo_resp = httpx.get(f"{API_BASE}/api/repos/{repo_id}")
    if repo_resp.status_code == 404:
        typer.echo("Repository not found. Run `autowiki index` first.", err=True)
        raise typer.Exit(1)
    if repo_resp.json().get("status") != "ready":
        typer.echo("Repository is not ready. Wait for indexing to complete.", err=True)
        raise typer.Exit(1)

    # Create a chat session
    session_resp = httpx.post(f"{API_BASE}/api/repos/{repo_id}/chat")
    session_resp.raise_for_status()
    session_id = session_resp.json()["session_id"]

    # Use WebSocket for streaming — fall back to HTTP polling approach
    # For CLI simplicity, use the websockets library synchronously
    import asyncio
    import websockets
    import json as _json

    async def _ask() -> str:
        uri = f"ws://localhost:3001/ws/repos/{repo_id}/chat/{session_id}"
        async with websockets.connect(uri) as ws:
            await ws.send(_json.dumps({"content": question}))
            chunks = []
            while True:
                msg = _json.loads(await ws.recv())
                if msg["type"] == "chunk":
                    chunks.append(msg["content"])
                elif msg["type"] == "done":
                    break
                elif msg["type"] == "error":
                    raise RuntimeError(msg["content"])
            return "".join(chunks)

    answer = asyncio.run(_ask())
    typer.echo(answer)
```

- [ ] **Step 5: Register commands in `cli/main.py`**

```python
from cli.commands.refresh import refresh_cmd
from cli.commands.chat_cmd import chat_cmd

app.command("refresh")(refresh_cmd)
app.command("chat")(chat_cmd)
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/cli/test_cli.py -v
```
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add cli/commands/refresh.py cli/commands/chat_cmd.py cli/main.py tests/cli/test_cli.py
git commit -m "feat: add refresh and chat CLI commands"
```

---

## Task 10: ChatPanel UI

Before writing any Next.js code, read `node_modules/next/dist/docs/` for API conventions that differ from standard Next.js training data (per `web/AGENTS.md`).

**Files:**
- Create: `web/components/ChatPanel.tsx`
- Create: `web/app/[owner]/[repo]/chat/page.tsx`
- Modify: `web/lib/api.ts` (add `createChatSession`, `getChatHistory`)
- Modify: `web/lib/ws.ts` (add `useChatStream`)
- Modify: `web/app/[owner]/[repo]/layout.tsx` (add Chat link to sidebar)

- [ ] **Step 1: Add chat API functions to `web/lib/api.ts`**

```typescript
export async function createChatSession(repoId: string): Promise<{ session_id: string }> {
  const res = await fetch(`${apiBase()}/api/repos/${repoId}/chat`, { method: "POST" });
  if (!res.ok) throw new Error(`Failed to create chat session: ${res.status}`);
  return res.json();
}

export async function getChatHistory(repoId: string, sessionId: string): Promise<{
  messages: Array<{ role: string; content: string }>;
}> {
  const res = await fetch(`${apiBase()}/api/repos/${repoId}/chat/${sessionId}`);
  if (!res.ok) throw new Error(`Failed to get chat history: ${res.status}`);
  return res.json();
}
```

- [ ] **Step 2: Add `useChatStream` to `web/lib/ws.ts`**

```typescript
export function useChatStream(
  repoId: string,
  sessionId: string | null,
  onChunk: (chunk: string) => void,
  onDone: () => void,
  onError: (err: string) => void,
) {
  const wsRef = useRef<WebSocket | null>(null);

  const sendMessage = useCallback(
    (content: string) => {
      if (!sessionId) return;
      const wsBase = typeof window !== "undefined"
        ? window.location.origin.replace(/^http/, "ws").replace(":3000", ":3001")
        : "ws://localhost:3001";
      const ws = new WebSocket(`${wsBase}/ws/repos/${repoId}/chat/${sessionId}`);
      wsRef.current = ws;
      ws.onopen = () => ws.send(JSON.stringify({ content }));
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === "chunk") onChunk(msg.content);
        else if (msg.type === "done") { onDone(); ws.close(); }
        else if (msg.type === "error") { onError(msg.content); ws.close(); }
      };
      ws.onerror = () => onError("WebSocket error");
    },
    [repoId, sessionId, onChunk, onDone, onError],
  );

  return { sendMessage };
}
```

Add `useRef` and `useCallback` to the imports at the top of `ws.ts` if not already there.

- [ ] **Step 3: Create `web/components/ChatPanel.tsx`**

```typescript
"use client";
import { useState, useCallback, useEffect, useRef } from "react";
import { createChatSession } from "@/lib/api";
import { useChatStream } from "@/lib/ws";

interface Message {
  role: "user" | "assistant";
  content: string;
}

export default function ChatPanel({ repoId }: { repoId: string }) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const streamingRef = useRef("");

  useEffect(() => {
    createChatSession(repoId).then((d) => setSessionId(d.session_id));
  }, [repoId]);

  const handleChunk = useCallback((chunk: string) => {
    streamingRef.current += chunk;
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (last?.role === "assistant" && last.content !== streamingRef.current) {
        return [...prev.slice(0, -1), { role: "assistant", content: streamingRef.current }];
      }
      return [...prev, { role: "assistant" as const, content: chunk }];
    });
  }, []);

  const handleDone = useCallback(() => {
    setStreaming(false);
    streamingRef.current = "";
  }, []);

  const handleError = useCallback((err: string) => {
    setStreaming(false);
    setMessages((prev) => [...prev, { role: "assistant", content: `Error: ${err}` }]);
  }, []);

  const { sendMessage } = useChatStream(repoId, sessionId, handleChunk, handleDone, handleError);

  const submit = () => {
    if (!input.trim() || streaming) return;
    setMessages((prev) => [...prev, { role: "user", content: input }]);
    setStreaming(true);
    sendMessage(input);
    setInput("");
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "1rem" }}>
      <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: "0.75rem" }}>
        {messages.map((m, i) => (
          <div key={i} style={{
            alignSelf: m.role === "user" ? "flex-end" : "flex-start",
            background: m.role === "user" ? "#1e40af" : "#1f2937",
            color: "#f9fafb",
            padding: "0.75rem 1rem",
            borderRadius: "0.5rem",
            maxWidth: "80%",
            whiteSpace: "pre-wrap",
          }}>
            {m.content}
          </div>
        ))}
      </div>
      <div style={{ display: "flex", gap: "0.5rem" }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          disabled={streaming || !sessionId}
          placeholder="Ask about this codebase..."
          style={{ flex: 1, padding: "0.5rem", borderRadius: "0.25rem", background: "#374151", color: "#f9fafb", border: "1px solid #4b5563" }}
        />
        <button
          onClick={submit}
          disabled={streaming || !sessionId}
          style={{ padding: "0.5rem 1rem", background: "#2563eb", color: "white", borderRadius: "0.25rem", cursor: "pointer" }}
        >
          {streaming ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Add Chat and Graph links to `web/app/[owner]/[repo]/layout.tsx`**

In the layout's sidebar or nav, add:
```typescript
<a href={`/${params.owner}/${params.repo}/chat`}>Chat</a>
<a href={`/${params.owner}/${params.repo}/graph`}>Module Graph</a>
```
The exact placement depends on the current sidebar markup — find the wiki page links and add these alongside them.

- [ ] **Step 5: Create `web/app/[owner]/[repo]/chat/page.tsx`**

```typescript
import ChatPanel from "@/components/ChatPanel";
import crypto from "crypto";

export default function ChatPage({ params }: { params: { owner: string; repo: string } }) {
  const repoId = crypto
    .createHash("sha256")
    .update(`github:${params.owner}/${params.repo}`)
    .digest("hex")
    .slice(0, 16);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 4rem)", padding: "1rem" }}>
      <h1 style={{ marginBottom: "1rem", fontSize: "1.25rem", fontWeight: "bold" }}>
        Chat — {params.owner}/{params.repo}
      </h1>
      <ChatPanel repoId={repoId} />
    </div>
  );
}
```

- [ ] **Step 6: Build and verify**

```bash
cd web && npm run build
```
Expected: build succeeds with no TypeScript errors

- [ ] **Step 7: Commit**

```bash
git add web/components/ChatPanel.tsx web/app web/lib/api.ts web/lib/ws.ts
git commit -m "feat: add ChatPanel UI with WebSocket streaming chat"
```

---

## Task 11: DependencyGraph UI

Before writing any Next.js / React code, re-read the Next.js 16 docs note in `web/AGENTS.md`.

**Files:**
- Create: `web/components/DependencyGraph.tsx`
- Create: `web/app/[owner]/[repo]/graph/page.tsx`
- Modify: `web/lib/api.ts` (add `getRepoGraph`)
- Modify: `web/package.json` (add `reactflow`)

- [ ] **Step 1: Install `reactflow`**

Pin to v11 — v12 ships as `@xyflow/react` with a different API.

```bash
cd web && npm install "reactflow@^11"
```

- [ ] **Step 2: Add `getRepoGraph` to `web/lib/api.ts`**

```typescript
export async function getRepoGraph(repoId: string): Promise<{
  nodes: Array<{ id: string; label: string; file_count: number }>;
  edges: Array<{ source: string; target: string }>;
}> {
  const res = await fetch(`${apiBase()}/api/repos/${repoId}/graph`);
  if (!res.ok) throw new Error(`Failed to get graph: ${res.status}`);
  return res.json();
}
```

- [ ] **Step 3: Create `web/components/DependencyGraph.tsx`**

```typescript
"use client";
import { useEffect, useState } from "react";
import ReactFlow, { Node, Edge, Background, Controls } from "reactflow";
import "reactflow/dist/style.css";
import { getRepoGraph } from "@/lib/api";

export default function DependencyGraph({ repoId }: { repoId: string }) {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getRepoGraph(repoId)
      .then((data) => {
        // Arrange nodes in a circle
        const count = data.nodes.length;
        const radius = Math.max(200, count * 40);
        const flowNodes: Node[] = data.nodes.map((n, i) => ({
          id: n.id,
          data: { label: `${n.label}\n(${n.file_count} files)` },
          position: {
            x: 400 + radius * Math.cos((2 * Math.PI * i) / count),
            y: 300 + radius * Math.sin((2 * Math.PI * i) / count),
          },
          style: { background: "#1f2937", color: "#f9fafb", border: "1px solid #4b5563", borderRadius: "0.5rem", padding: "0.5rem" },
        }));
        const flowEdges: Edge[] = data.edges.map((e, i) => ({
          id: `e${i}`,
          source: e.source,
          target: e.target,
          animated: false,
        }));
        setNodes(flowNodes);
        setEdges(flowEdges);
      })
      .catch((e) => setError(e.message));
  }, [repoId]);

  if (error) return <p style={{ color: "#ef4444" }}>Failed to load graph: {error}</p>;
  if (!nodes.length) return <p style={{ color: "#9ca3af" }}>Loading module graph…</p>;

  return (
    <div style={{ width: "100%", height: "600px" }}>
      <ReactFlow nodes={nodes} edges={edges} fitView>
        <Background color="#374151" gap={16} />
        <Controls />
      </ReactFlow>
    </div>
  );
}
```

- [ ] **Step 4: Create `web/app/[owner]/[repo]/graph/page.tsx`**

```typescript
import crypto from "crypto";
import DependencyGraph from "@/components/DependencyGraph";

export default function GraphPage({ params }: { params: { owner: string; repo: string } }) {
  const repoId = crypto
    .createHash("sha256")
    .update(`github:${params.owner}/${params.repo}`)
    .digest("hex")
    .slice(0, 16);

  return (
    <div style={{ padding: "1rem" }}>
      <h1 style={{ marginBottom: "1rem", fontSize: "1.25rem", fontWeight: "bold" }}>
        Module Graph — {params.owner}/{params.repo}
      </h1>
      <DependencyGraph repoId={repoId} />
    </div>
  );
}
```

- [ ] **Step 5: Build and verify**

```bash
cd web && npm run build
```
Expected: build succeeds

- [ ] **Step 6: Run the full test suite to confirm no regressions**

```bash
pytest tests/ --ignore=tests/e2e --cov=worker --cov=api --cov=shared --cov-report=term-missing
```
Expected: all tests pass, ≥80% coverage on worker/api/shared

- [ ] **Step 7: Commit**

```bash
git add web/components/DependencyGraph.tsx web/app web/lib/api.ts web/package.json
git commit -m "feat: add DependencyGraph UI with reactflow visualization"
```

---

## Merge Compatibility: Wiki Quality PR (merged 2026-03-25)

After Phase 2 was branched, a wiki-quality improvement PR was merged to `main`
(`feat: improve wiki quality with dependency graphs, richer prompts, source annotations, and Mermaid diagrams`).
The Phase 2 branch must be rebased onto / merged with main before release.

### What the Quality PR Added

| File | Change |
|------|--------|
| `worker/pipeline/dependency_graph.py` | **New module** — extracts import relationships, builds module clusters |
| `worker/pipeline/ast_analysis.py` | `build_enhanced_module_tree()` (entity summaries per module), `analyze_file()` (per-file entity extraction) |
| `worker/pipeline/wiki_planner.py` | `generate_page_plan()` gains `readme`, `dep_summary`, `clusters` optional params |
| `worker/pipeline/page_generator.py` | `generate_page()` gains `dep_info`, `entity_details` params; per-page Mermaid prompts built in |
| `worker/pipeline/ingestion.py` | `extract_readme()` added |
| `shared/models.py` | `WikiPage.description` column added; `datetime.now(UTC)` style |
| `worker/embedding/base.py` | `embed()` gains `is_code: bool = False` param |
| `worker/jobs.py` | Orchestrates all enhancements in `run_full_index` |

### Incompatibilities with Phase 2

| Area | Severity | Issue |
|------|----------|-------|
| `worker/jobs.py` | **Critical** | Phase 2's `run_full_index` drops all quality enhancements: no `build_enhanced_module_tree`, no `dependency_graph`, no `analyze_file`/`file_entities`, no `readme`/`dep_summary` passed to planner or page generator |
| `worker/pipeline/ast_analysis.py` | **High** | Phase 2 refresh job re-runs Stage 2 using only `build_module_tree` — misses entity analysis |
| `worker/pipeline/wiki_planner.py` | **Medium** | Phase 2's `run_refresh_index` calls planner without `readme`/`dep_summary`/`clusters` — works but lower quality |
| `worker/pipeline/page_generator.py` | **Medium** | Phase 2 calls `generate_page()` without `dep_info`/`entity_details`; main already embeds Mermaid prompts per-page, making Phase 2's separate Stage 6 partially additive rather than redundant (Stage 6 generates a repo-level architecture diagram, per-page Mermaid is page-scoped) |
| `shared/models.py` | **Low** | Phase 2 branch missing `WikiPage.description` column |
| `worker/pipeline/ingestion.py` | **Low** | Phase 2 missing `extract_readme()` — no functional breakage |
| `worker/embedding/base.py` | **None** | `embed(text)` call in `worker/chat.py` still works; `is_code` defaults to False |

### Merge Resolution Plan

The merge of `main` into `phase2-chat-diagrams-refresh` must combine in `worker/jobs.py`:

**Keep from main:**
- `build_enhanced_module_tree` call (Stage 2)
- `analyze_file` loop to build `file_entities` (Stage 2)
- `build_dependency_graph` + `summarize_dependencies` (Stage 2b)
- `extract_readme` call (Stage 1)
- Pass `readme`, `dep_summary`, `clusters` to `generate_page_plan`
- Pass `entity_details`, `dep_info` to `generate_page`
- `entity_aware` RAG chunking (`build_rag_index(..., file_entities=file_entities)`)

**Keep from Phase 2:**
- `.autowikiignore` pass-through to `filter_files`
- `ast_dir / "module_tree.json"` persistence after Stage 2
- Stage 6 `synthesize_diagrams` call and diagram prepend
- `run_refresh_index` function (entire new function)

**`shared/models.py`:** Accept main's `WikiPage.description` + `datetime.now(UTC)` style; keep Phase 2's `ChatSession`/`ChatMessage` additions.

**`worker/pipeline/ingestion.py`:** Keep main's `extract_readme` + Phase 2's `.autowikiignore` and `get_changed_files`/`get_affected_modules`.

---

## Coverage & Integration Verification

After all tasks are complete:

- [ ] Run full test suite:
  ```bash
  pytest tests/ --ignore=tests/e2e --cov=worker --cov=api --cov=shared --cov-report=term-missing
  ```
  Target: ≥80% coverage

- [ ] Confirm these new modules hit coverage targets:
  - `worker/pipeline/diagram_synthesis.py`
  - `worker/chat.py`
  - `api/routers/chat.py`

- [ ] Build the web UI end-to-end:
  ```bash
  cd web && npm run build
  ```

- [ ] Tag the release:
  ```bash
  git tag v0.2.0-phase2
  ```
