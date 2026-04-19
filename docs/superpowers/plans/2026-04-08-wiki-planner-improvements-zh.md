# 维基规划器与生成流水线改进 — 实施计划

> **状态：已完成** — 所有 7 个计划任务已实施并合并至 `feature/wiki-planner-improvements` 分支。额外的范围项（第 7 阶段移除、`--reuse-index`、按阶段验证、基于重要性排名的文件限制）也已实施并提交。

> **对于智能体工作者：** 要求的子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 来逐项任务实施此计划。步骤使用复选框 (`- [x]`) 语法进行跟踪。

**目标：** 将维基生成流水线从扁平的单次通过系统转变为分层的多智能体生成器，具有更丰富的上下文、两阶段规划、语义验证和自底向上的页面合成。

**架构：** 七项连续改进：(1) 批量 LLM 生成，(2) 带有依赖项和文档注释上下文的丰富文件摘要，(3) 针对大型依赖组件的 BFS 种子子聚类，(4) 动态页面计数启发式算法，(5) 两阶段大纲后分配规划，(6) 语义计划验证，(7) 父页面合成子页面内容的自底向上多智能体页面生成。

**技术栈：** Python 3.12, asyncio, Tree-Sitter, FAISS, pytest (asyncio_mode=auto)

**规范：** `docs/superpowers/specs/2026-04-08-wiki-planner-improvements-design.md`

## 额外范围（超出原始计划）

- **移除了第 7 阶段** (`diagram_synthesis.py`)：冗余 — 概览（Overview）页面生成器已经通过其提示词模板发出了架构 Mermaid 图表。流水线现在为 6 个阶段。
- **`--reuse-index` / `reuse_index`**：新的布尔参数，从 CLI 贯穿到 API `IndexRequest` → `enqueue_full_index` → `run_full_index`。设置后，跳过清除和重新构建 FAISS 索引（适用于仅重新运行维基规划/生成阶段）。
- **按阶段验证** (`_validate_outline_structure`, `_validate_assignments`)：验证现在分别在第 1 阶段和第 2 阶段之后立即触发，触发阶段内重试，而不是等待最终的 `validate_wiki_plan()` 调用。延迟的第 3 阶段重试循环和错误类型分类助手（`_OUTLINE_ERROR_PREFIXES`, `_is_outline_error`）已被移除。
- **基于重要性排名的文件限制** (`_rank_files_by_importance`)：当 `to_llm_summary()` 将超过文件上限时，将选择 200 个在架构上最重要的文件（通过实体计数、入度、入口点名称奖励和浅度进行评分），而不是按字母顺序选择前 200 个。
- **`to_llm_summary` 默认更改为 `max_files=200`**：不带参数调用现在被安全地限制；传递 `0` 以选择加入 800 个文件的安全上限。
- **依赖列表截断**：每个文件的导入/外部列表限制为 10 个条目，并带有 `+N more` 后缀，以防止枢纽文件主导提示词预算。

---

### 任务 1：向 LLMProvider 添加 `generate_batch`

**文件：**
- 修改：`worker/llm/base.py:30-46` (LLMProvider ABC)
- 修改：`worker/llm/base.py:48-93` (LoggingLLMProvider)
- 测试：`tests/worker/test_llm.py`

- [x] **步骤 1：编写失败测试**

添加到 `tests/worker/test_llm.py`：

```python
async def test_generate_batch_default_impl():
    """默认的 generate_batch 会并发地为每个提示词调用 generate()。"""
    from unittest.mock import AsyncMock

    from worker.llm.base import LLMProvider

    # 创建一个继承了 generate_batch 的具体子类
    class FakeLLM(LLMProvider):
        async def generate(self, prompt: str, system: str = "") -> str:
            return f"response:{prompt}"

        async def generate_structured(self, prompt, schema, system=""):
            return {}

        async def generate_stream(self, prompt, system=""):
            yield ""

    llm = FakeLLM()
    results = await llm.generate_batch(["a", "b", "c"], system="sys")
    assert results == ["response:a", "response:b", "response:c"]


async def test_generate_batch_respects_max_concurrency():
    """并行运行的调用次数最多为 max_concurrency。"""
    import asyncio

    from worker.llm.base import LLMProvider

    concurrent = 0
    max_seen = 0

    class TrackingLLM(LLMProvider):
        async def generate(self, prompt: str, system: str = "") -> str:
            nonlocal concurrent, max_seen
            concurrent += 1
            max_seen = max(max_seen, concurrent)
            await asyncio.sleep(0.01)
            concurrent -= 1
            return prompt

        async def generate_structured(self, prompt, schema, system=""):
            return {}

        async def generate_stream(self, prompt, system=""):
            yield ""

    llm = TrackingLLM()
    await llm.generate_batch([f"p{i}" for i in range(10)], max_concurrency=3)
    assert max_seen <= 3


async def test_logging_provider_wraps_generate_batch():
    """LoggingLLMProvider 将 generate_batch 委托给内部提供商。"""
    from unittest.mock import AsyncMock

    from worker.llm.base import LoggingLLMProvider, LLMProvider

    class FakeLLM(LLMProvider):
        async def generate(self, prompt: str, system: str = "") -> str:
            return f"r:{prompt}"

        async def generate_structured(self, prompt, schema, system=""):
            return {}

        async def generate_stream(self, prompt, system=""):
            yield ""

    inner = FakeLLM()
    logged = LoggingLLMProvider(inner)
    results = await logged.generate_batch(["x", "y"])
    assert results == ["r:x", "r:y"]
```

- [x] **步骤 2：运行测试以验证其失败**

运行：`uv run pytest tests/worker/test_llm.py::test_generate_batch_default_impl tests/worker/test_llm.py::test_generate_batch_respects_max_concurrency tests/worker/test_llm.py::test_logging_provider_wraps_generate_batch -v`

预期：FAIL — `generate_batch` 尚不存在。

- [x] **步骤 3：在 LLMProvider 上实现 `generate_batch`**

在 `worker/llm/base.py` 中，在顶部添加 `import asyncio`。然后在 `generate_stream` 抽象方法之后（第 45 行之后）向 `LLMProvider` 类添加此方法：

```python
    async def generate_batch(
        self,
        prompts: list[str],
        system: str = "",
        max_concurrency: int = 5,
    ) -> list[str]:
        """并发地为多个提示词生成响应。

        默认实现使用带有信号量的 asyncio.gather。
        提供商可以重写此方法以使用原生的批量 API。
        """
        sem = asyncio.Semaphore(max_concurrency)

        async def _one(prompt: str) -> str:
            async with sem:
                return await self.generate(prompt, system)

        return list(await asyncio.gather(*[_one(p) for p in prompts]))
```

- [x] **步骤 4：在 LoggingLLMProvider 上实现 `generate_batch`**

将此方法添加到 `LoggingLLMProvider` 类中（在 `generate_stream` 之后）：

```python
    async def generate_batch(
        self,
        prompts: list[str],
        system: str = "",
        max_concurrency: int = 5,
    ) -> list[str]:
        logger.debug(
            "LLM REQUEST (batch): %d prompts, system=%s",
            len(prompts),
            _truncate(system),
        )
        results = await self._provider.generate_batch(prompts, system, max_concurrency)
        logger.debug(
            "LLM RESPONSE (batch): %d responses, total %d chars",
            len(results),
            sum(len(r) for r in results),
        )
        return results
```

- [x] **步骤 5：运行测试以验证其通过**

运行：`uv run pytest tests/worker/test_llm.py -v`

预期：所有测试通过，包括 3 个新测试。

- [x] **步骤 6：Lint 并提交**

```bash
uv run ruff check worker/llm/base.py tests/worker/test_llm.py
uv run ruff format worker/llm/base.py tests/worker/test_llm.py
git add worker/llm/base.py tests/worker/test_llm.py
git commit -m "feat: add generate_batch to LLMProvider with default asyncio.gather impl"
```

---

### 任务 2：使用依赖项和文档注释上下文丰富文件摘要

**文件：**
- 修改：`worker/pipeline/ast_analysis.py:389-434` (`FileAnalysis.to_llm_summary`)
- 测试：`tests/worker/test_ast_analysis.py`

- [x] **步骤 1：编写失败测试**

添加到 `tests/worker/test_ast_analysis.py`：

```python
def test_to_llm_summary_with_dep_graph(tmp_path):
    """如果提供了 dep_graph，to_llm_summary 会包含导入/外部依赖和文档注释。"""
    from worker.pipeline.dependency_graph import DependencyGraph

    f1 = tmp_path / "main.py"
    f1.write_text('"""Entry point for the app."""\nimport os\nfrom models import User\ndef run():\n    pass\n')
    f2 = tmp_path / "models.py"
    f2.write_text('class User:\n    """A user model."""\n    pass\n')

    result = analyze_all_files(tmp_path, [f1, f2])

    graph = DependencyGraph(
        edges={"main.py": ["models.py"]},
        clusters=[["main.py", "models.py"]],
        external_deps={"main.py": ["os"]},
    )
    summary = result.to_llm_summary(dep_graph=graph)

    # main.py 应该包含导入和外部依赖信息
    lines = summary.splitlines()
    main_idx = next(i for i, l in enumerate(lines) if l.startswith("main.py"))
    # 下一行应包含导入信息
    dep_line = lines[main_idx + 1]
    assert "models.py" in dep_line
    assert "os" in dep_line

    # models.py 应该包含文档注释行
    models_idx = next(i for i, l in enumerate(lines) if l.startswith("models.py"))
    # 检查接下来 2 行内是否存在文档注释行
    docstring_found = any(
        "user model" in lines[models_idx + j].lower()
        for j in range(1, min(3, len(lines) - models_idx))
    )
    assert docstring_found
```

- [x] **步骤 2：运行测试以验证其失败**

运行：`uv run pytest tests/worker/test_ast_analysis.py::test_to_llm_summary_with_dep_graph tests/worker/test_ast_analysis.py::test_to_llm_summary_no_limit_by_default tests/worker/test_ast_analysis.py::test_to_llm_summary_safety_cap -v`

预期：FAIL — `to_llm_summary` 尚未接受 `dep_graph`，且默认的 `max_files=200` 会发生截断。

- [x] **步骤 3：实现丰富的 `to_llm_summary`**

替换 `worker/pipeline/ast_analysis.py` 中的 `to_llm_summary` 方法（第 389-434 行）：

```python
    def to_llm_summary(
        self,
        max_files: int = 0,
        dep_graph: "DependencyGraph | None" = None,
    ) -> str:
        """返回每个文件的摘要，带有可选的依赖项和文档注释上下文。

        Args:
            max_files: 具有完整详情的最大文件数。0 表示无限制（安全上限为 800）。
                超过上限的文件将列为纯路径。
            dep_graph: 用于导入/外部依赖信息的可选依赖图。
        """
        from worker.pipeline.dependency_graph import DependencyGraph  # noqa: F811

        sorted_keys = sorted(self.files.keys())
        cap = max_files if max_files > 0 else 800
        detailed = sorted_keys[:cap]
        overflow = sorted_keys[cap:]

        lines: list[str] = []
        for rel_path in detailed:
            info = self.files[rel_path]
            if not info.entities:
                lines.append(f"{rel_path}: (no named entities)")
            else:
                lines.append(
                    f"{rel_path}: {info.class_count} classes,"
                    f" {info.function_count} functions [{info.summary}]"
                )

            # 依赖行
            if dep_graph is not None:
                internal = dep_graph.edges.get(rel_path, [])
                external = dep_graph.external_deps.get(rel_path, [])
                if internal or external:
                    parts = []
                    if internal:
                        parts.append(f"imports: {', '.join(internal)}")
                    if external:
                        parts.append(f"external: {', '.join(external)}")
                    lines.append(f"  {' | '.join(parts)}")
                elif not info.entities:
                    pass  # 已显示 (no named entities)
                else:
                    lines.append("  (no dependencies)")

            # 来自第一个顶级实体的文档注释
            if info.entities:
                for e in info.entities:
                    if e.get("docstring"):
                        doc = e["docstring"][:120].replace("\n", " ")
                        lines.append(f'  "{doc}"')
                        break

        # 作为纯路径的溢出文件
        if overflow:
            lines.append(f"... and {len(overflow)} more files (paths only):")
            for rel_path in overflow:
                lines.append(f"  {rel_path}")

        return "\n".join(lines)
```

- [x] **步骤 4：运行测试以验证其通过**

运行：`uv run pytest tests/worker/test_ast_analysis.py -v`

预期：所有测试通过。注意：`test_analyze_all_files_to_llm_summary_truncation` 显式传递了 `max_files=2`，因此它仍然有效。

- [x] **步骤 5：更新调用者以传递 dep_graph**

在 `worker/jobs.py` 中，找到 `file_analysis.to_llm_summary()` 调用（用于第 451 行左右的 `_write_text_async`）。这仅用于调试输出，不用于规划器。规划器调用发生在 `generate_wiki_plan` → `_build_prompt` 内。更新 `worker/pipeline/wiki_planner.py` 中的 `_build_prompt` 以接受并传递 dep_graph：

在 `worker/pipeline/wiki_planner.py` 中，更新 `_build_prompt` 签名（第 279 行）以添加 `dep_graph=None` 参数，并更改 `file_summary` 的用法：

```python
def _build_prompt(
    file_summary: str,
    repo_name: str,
    readme: str | None = None,
    dep_info: str | None = None,
    clusters: list[list[str]] | None = None,
    all_files: list[str] | None = None,
) -> str:
```

这里还不需要更改 — `file_summary` 已经作为一个预先格式化的字符串传递。dep_graph 将在 `generate_wiki_plan`（第 543 行）的调用点传递：

将第 543 行从：
```python
    file_summary = file_analysis.to_llm_summary()
```
改为：
```python
    file_summary = file_analysis.to_llm_summary(dep_graph=dep_graph)
```

- [x] **步骤 6：运行完整测试套件**

运行：`uv run pytest tests/ --ignore=tests/e2e -v`

预期：全部通过。

- [x] **步骤 7：Lint 并提交**

```bash
uv run ruff check worker/pipeline/ast_analysis.py worker/pipeline/wiki_planner.py tests/worker/test_ast_analysis.py
uv run ruff format worker/pipeline/ast_analysis.py worker/pipeline/wiki_planner.py tests/worker/test_ast_analysis.py
git add worker/pipeline/ast_analysis.py worker/pipeline/wiki_planner.py tests/worker/test_ast_analysis.py
git commit -m "feat: enrich file summaries with dependency info and docstrings"
```

---

### 任务 3：依赖感知的子聚类

**文件：**
- 修改：`worker/pipeline/dependency_graph.py:308-366` (`_compute_clusters`, 添加 `_split_large_cluster`)
- 修改：`worker/pipeline/dependency_graph.py:369-423` (`format_for_llm_prompt`)
- 测试：`tests/worker/test_dependency_graph.py`

- [x] **步骤 1：编写失败测试**

添加到 `tests/worker/test_dependency_graph.py`：

```python
from worker.pipeline.dependency_graph import _split_large_cluster


def test_split_large_cluster_small_cluster_unchanged():
    """max_size 范围内的聚类将按原样返回。"""
    cluster = ["a.py", "b.py", "c.py"]
    edges = {"a.py": ["b.py"], "b.py": ["c.py"]}
    result = _split_large_cluster(cluster, edges, max_size=15)
    assert len(result) == 1
    assert sorted(result[0]) == ["a.py", "b.py", "c.py"]


def test_split_large_cluster_splits_large():
    """超过 max_size 的聚类会被分割成子聚类。"""
    # 创建一个链：f0 -> f1 -> f2 -> ... -> f19
    files = [f"f{i}.py" for i in range(20)]
    edges = {f"f{i}.py": [f"f{i+1}.py"] for i in range(19)}
    result = _split_large_cluster(files, edges, max_size=8)
    assert len(result) >= 2
    # 涵盖所有文件
    all_files = sorted(f for sub in result for f in sub)
    assert all_files == sorted(files)
    # 每个子聚类都遵守 max_size
    for sub in result:
        assert len(sub) <= 8
```

- [x] **步骤 2：运行测试以验证其失败**

运行：`uv run pytest tests/worker/test_dependency_graph.py::test_split_large_cluster_small_cluster_unchanged tests/worker/test_dependency_graph.py::test_split_large_cluster_splits_large tests/worker/test_dependency_graph.py::test_split_large_cluster_disconnected_files tests/worker/test_dependency_graph.py::test_compute_clusters_splits_large_components tests/worker/test_dependency_graph.py::test_format_for_llm_prompt_default_500_cap -v`

预期：FAIL — `_split_large_cluster` 不存在。

- [x] **步骤 3：实现 `_split_large_cluster`**

在 `worker/pipeline/dependency_graph.py` 中的 `_compute_clusters` 之前（第 308 行之前）添加此函数：

```python
def _split_large_cluster(
    cluster: list[str],
    edges: dict[str, list[str]],
    max_size: int = 15,
) -> list[list[str]]:
    """使用 BFS 种子分组将大型聚类分割成子聚类。

    选择具有最多导入边的文件作为第一个种子，向外 BFS 以填充子聚类，
    直到达到 max_size，然后对剩余文件重复此过程。
    """
    if len(cluster) <= max_size:
        return [sorted(cluster)]

    cluster_set = set(cluster)
    # 构建子图邻接表（用于 BFS 的无向图）
    adj: dict[str, list[str]] = {f: [] for f in cluster}
    for src in cluster:
        for tgt in edges.get(src, []):
            if tgt in cluster_set:
                adj[src].append(tgt)
                adj[tgt].append(src)

    remaining = set(cluster)
    sub_clusters: list[list[str]] = []

    while remaining:
        # 选择种子：剩余文件中边最多的文件
        seed = max(remaining, key=lambda f: len([n for n in adj.get(f, []) if n in remaining]))
        # 从种子开始 BFS
        visited: list[str] = []
        queue = [seed]
        seen = {seed}
        while queue and len(visited) < max_size:
            node = queue.pop(0)
            if node not in remaining:
                continue
            visited.append(node)
            remaining.discard(node)
            for neighbor in adj.get(node, []):
                if neighbor in remaining and neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)

        sub_clusters.append(sorted(visited))

    return sub_clusters
```

- [x] **步骤 4：修改 `_compute_clusters` 以使用 `_split_large_cluster`**

替换 `_compute_clusters` 中的返回语句（第 366 行）：

从：
```python
    return [sorted(g) for g in sorted(groups.values(), key=lambda g: (-len(g), g[0]))]
```

到：
```python
    raw_clusters = sorted(groups.values(), key=lambda g: (-len(g), g[0]))
    result: list[list[str]] = []
    for g in raw_clusters:
        result.extend(_split_large_cluster(sorted(g), edges))
    return result
```

- [x] **步骤 5：更改 `format_for_llm_prompt` 默认的 `max_edges`**

在 `worker/pipeline/dependency_graph.py` 中，更改 `format_for_llm_prompt` 的签名（第 369 行）：

从：
```python
def format_for_llm_prompt(graph: DependencyGraph, max_edges: int = 150) -> str:
```

到：
```python
def format_for_llm_prompt(graph: DependencyGraph, max_edges: int = 500) -> str:
```

- [x] **步骤 6：运行测试以验证其通过**

运行：`uv run pytest tests/worker/test_dependency_graph.py -v`

预期：全部通过。

- [x] **步骤 7：Lint 并提交**

```bash
uv run ruff check worker/pipeline/dependency_graph.py tests/worker/test_dependency_graph.py
uv run ruff format worker/pipeline/dependency_graph.py tests/worker/test_dependency_graph.py
git add worker/pipeline/dependency_graph.py tests/worker/test_dependency_graph.py
git commit -m "feat: add BFS-seed sub-clustering for large dependency components"
```

---

### 任务 4：动态页面计数启发式算法

**文件：**
- 修改：`worker/pipeline/wiki_planner.py` (添加 `_suggest_page_range`)
- 测试：`tests/worker/test_wiki_planner.py`

- [x] **步骤 1：编写失败测试**

添加到 `tests/worker/test_wiki_planner.py`：

```python
from worker.pipeline.wiki_planner import _suggest_page_range


def test_suggest_page_range_small_repo():
    assert _suggest_page_range(5, 10) == (3, 6)


def test_suggest_page_range_medium_repo_few_entities():
    assert _suggest_page_range(20, 30) == (5, 12)
```

- [x] **步骤 2：运行测试以验证其失败**

运行：`uv run pytest tests/worker/test_wiki_planner.py::test_suggest_page_range_small_repo -v`

预期：FAIL — `_suggest_page_range` 不存在。

- [x] **步骤 3：实现 `_suggest_page_range`**

在 `worker/pipeline/wiki_planner.py` 中的 `_slugify_title` 之后（第 44 行之后）添加此函数：

```python
def _suggest_page_range(file_count: int, entity_count: int) -> tuple[int, int]:
    """根据仓库复杂度建议最小/最大页面计数。"""
    if file_count < 10:
        return (3, 6)
    if file_count <= 30:
        return (8, 15) if entity_count >= 50 else (5, 12)
    if file_count <= 100:
        return (15, 35) if entity_count >= 150 else (10, 25)
    if file_count <= 300:
        return (20, 50)
    return (30, 70)
```

- [x] **步骤 4：运行测试以验证其通过**

运行：`uv run pytest tests/worker/test_wiki_planner.py -v`

预期：全部通过。

- [x] **步骤 5：Lint 并提交**

```bash
uv run ruff check worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
uv run ruff format worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
git add worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
git commit -m "feat: add dynamic page count heuristics for wiki planner"
```

---

### 任务 5：两阶段规划（大纲 + 文件分配）

**文件：**
- 修改：`worker/pipeline/wiki_planner.py` (替换 `_build_prompt`, `generate_wiki_plan`；添加 `_generate_outline`, `_assign_files`, `_build_outline_prompt`, `_build_assignment_prompt`)
- 测试：`tests/worker/test_wiki_planner.py`

这是最大的任务。我们重写了核心规划逻辑。

- [x] **步骤 1：为第 1 阶段（大纲生成）编写失败测试**

添加到 `tests/worker/test_wiki_planner.py`：

```python
async def test_generate_outline(mock_llm):
    """_generate_outline 返回包含标题/目的/父页面的页面字典列表。"""
    from worker.pipeline.wiki_planner import _generate_outline

    mock_llm.generate_structured.return_value = {
        "pages": [
            {"title": "Overview", "purpose": "Top-level overview."},
            {"title": "API", "purpose": "REST API.", "parent": "Overview"},
            {"title": "Worker", "purpose": "Background jobs.", "parent": "Overview"},
        ]
    }
    outline = await _generate_outline(
        file_summary="main.py: 0 classes, 1 functions [run]",
        repo_name="test",
        llm=mock_llm,
        readme="A test project.",
        dep_info=None,
        clusters=None,
        page_range=(3, 10),
        system="You are a planner.",
        on_retry=None,
    )
    assert len(outline) == 3
```

- [x] **步骤 2：为第 2 阶段（文件分配）编写失败测试**

添加到 `tests/worker/test_wiki_planner.py`：

```python
async def test_assign_files(mock_llm):
    """_assign_files 返回一个将页面标题映射到文件列表的字典。"""
    from worker.pipeline.wiki_planner import _assign_files

    mock_llm.generate_structured.return_value = {
        "assignments": [
            {"file": "main.py", "page_title": "Overview"},
            {"file": "api.py", "page_title": "API"},
            {"file": "worker.py", "page_title": "Worker"},
        ]
    }
    outline = [
        {"title": "Overview", "purpose": "Top-level."},
        {"title": "API", "purpose": "REST API."},
        {"title": "Worker", "purpose": "Jobs."},
    ]
    result = await _assign_files(
        outline=outline,
        file_summary="main.py: ...\napi.py: ...\nworker.py: ...",
        dep_info=None,
        all_files=["main.py", "api.py", "worker.py"],
        llm=mock_llm,
        system="Assign files.",
        on_retry=None,
    )
    assert result["Overview"] == ["main.py"]
```

- [x] **步骤 3：为更新后的编排器编写失败测试**

- [x] **步骤 4：运行测试以验证其失败**

- [x] **步骤 5：实现新的 Schema 和提示词构建器**

在 `worker/pipeline/wiki_planner.py` 中，替换 `_WIKI_PLAN_SCHEMA` 和 `_build_prompt`。

- [x] **步骤 6：实现 `_generate_outline`**

- [x] **步骤 7：实现 `_assign_files`**

- [x] **步骤 8：重写 `generate_wiki_plan` 以使用两阶段方法**

- [x] **步骤 9：移除旧的 `_WIKI_PLAN_SCHEMA` 和 `_build_prompt`**

- [x] **步骤 10：更新 `conftest.py` 模拟以支持两阶段调用**

- [x] **步骤 11：运行完整测试套件**

- [x] **步骤 12：Lint 并提交**

---

### 任务 6：增强的计划验证

**文件：**
- 修改：`worker/pipeline/wiki_planner.py` (`validate_wiki_plan`)
- 测试：`tests/worker/test_wiki_planner.py`

- [x] **步骤 1：编写失败测试**

添加到 `tests/worker/test_wiki_planner.py`：

```python
def test_validate_rejects_page_over_25_files():
    raw = {
        "pages": [
            {"title": "Mega Page", "purpose": "Too many files.", "files": [f"f{i}.py" for i in range(30)]},
        ]
    }
    with pytest.raises(ValueError, match="split into focused sub-pages"):
        validate_wiki_plan(raw)
```

- [x] **步骤 2：运行测试以验证其失败**

- [x] **步骤 3：实现增强验证**

更新 `worker/pipeline/wiki_planner.py` 中的 `validate_wiki_plan`。

---

### 任务 7：多智能体自底向上页面生成

**文件：**
- 修改：`worker/pipeline/page_generator.py` (添加 `compute_generation_order`, `generate_page_batch`，更新 `generate_page` 和提示词)
- 修改：`worker/jobs.py` (替换 `run_full_index` 和 `run_refresh_index` 中的扁平循环)
- 测试：`tests/worker/test_page_generator.py`
- 测试：`tests/worker/test_jobs.py`

---

### 任务 8：最终集成验证与清理

**文件：**
- 任务 1-7 中所有修改的文件
- 测试：`tests/test_integration.py`

- [x] **步骤 1：运行完整测试套件**

- [x] **步骤 2：运行 Linter**

- [x] **步骤 3：运行前端 Lint**

- [x] **步骤 4：验证没有陈旧的导入或死代码**

- [x] **步骤 5：提交任何清理内容**
