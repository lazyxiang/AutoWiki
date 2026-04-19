# Wiki 规划器与生成流水线改进

**日期**：2026-04-08
**状态**：已实现并合并至 `main`（原分支为 `feature/wiki-planner-improvements`）
**范围**：worker/llm, worker/pipeline, worker/jobs

## 实现说明（与规范的差异）

- **`to_llm_summary` 默认值**：规范中提到 `max_files=0`（无限制，安全上限 800）。实际实现为 `max_files=200`，以便省略参数的调用者保持在界限内。传递 `0` 可显式启用 800 个文件的上限。
- **依赖列表截断**：每个文件的内部和外部导入列表限制为 10 个条目，并带有 `+N more` 后缀，以防止核心文件（hub files）主导提示词。
- **阶段 7（图表合成）已移除**：完全放弃了 `diagram_synthesis.py` —— 概览页生成器的提示词模板已经会生成架构 Mermaid 图表，使得阶段 7 变得冗余。流水线现在改为 6 个阶段。
- **`--reuse-index` / `reuse_index`**：新的布尔参数，从 CLI 贯穿到 `IndexRequest` → `enqueue_full_index` → `run_full_index`。当为 true 时，保留现有的 FAISS 文件并跳过阶段 4。
- **添加分阶段验证**：阶段 1 之后立即触发 `_validate_outline_structure()`；阶段 2 之后立即触发 `_validate_assignments()`。这取代了在实现中期添加但随后被废弃的延迟阶段 3 重试循环和错误类型分类辅助工具。
- **按重要性排序的文件选择**：当文件数量超过 `max_files` 时，`_rank_files_by_importance()` 会选择架构上最重要的文件（根据实体数量、入度、入口点名称奖励、深度进行评分），而不是退回到按字母顺序选择。
- **未实现 `_split_large_cluster()`**（第 3 节）：设计了 BFS 种子子聚类函数，但从未添加到 `dependency_graph.py` 中。大型连通分量按原样传递给规划器。`_build_outline_prompt()` 函数在提示词层级处理它们：文件数 ≤ 20 的集群完整列出；文件数 > 20 的集群接收一条摘要消息（“大型集群 (N 个文件) —— 请参见上文的依赖关系以了解内部结构”）。30 个集群的安全上限适用于原始集群，而非子集群。
- **第 7 节 `generate_page_batch` 已被取代**：最初的设计是让 `generate_page_batch` 收集扁平的提示词并调用 `llm.generate_batch(prompts)`。2026-04-10 的 Wiki 页面质量重新设计将单次生成替换为 4 次传递流水线（大纲 → 草案 → 事实核查 → 修订）；`generate_page_batch` 现在通过 `asyncio.gather` 并发运行单个 `generate_page()` 调用。`LLMProvider` 上仍然存在 `llm.generate_batch()` 方法，可供将来使用。

## 问题

与 DeepWiki 相比（同一仓库生成 14 页 vs 约 30 页），AutoWiki 的 Wiki 规划器生成的规划较为肤浅且不够精确。页面生成器将所有页面视为独立的 —— 父页面无法合成子页面的内容，导致内容重复且概览过于浅显。规划器的单次 LLM 调用在结构化思维和文件分配上负荷过重，且收到的文件摘要过于稀疏，缺乏语义上下文。

## 目标

1. 父 Wiki 页面合成子页面内容，而非简单的重复（自底向上的多智能体生成）
2. Wiki 规划粒度更细，每个页面专注于 3-15 个文件
3. 文件到页面的分配更准确 —— 紧密耦合的文件应分配到同一页面
4. 规划器可扩展至任何规模的仓库，且无随意的截断

## 非目标

- 为旧接口提供向后兼容性补丁
- 成本优化（准确性优先）
- 规划期间向前端实时更新规划进度

## 实现顺序

每项改进都建立在之前的改进之上：

1. LLMProvider 上的 `generate_batch`
2. 更丰富的文件摘要
3. 依赖感知分组
4. 动态页面计数
5. 双阶段规划
6. 增强的规划验证
7. 多智能体自底向上生成

---

## 1. LLMProvider 上的 `generate_batch`

### 文件：`worker/llm/base.py`

添加到 `LLMProvider` 抽象基类：

```python
async def generate_batch(
    self,
    prompts: list[str],
    system: str = "",
    max_concurrency: int = 5,
) -> list[str]:
    """并发为多个提示词生成响应。

    默认实现使用带有信号量的 asyncio.gather。
    提供商可以重写此方法以使用原生批处理 API。
    """
    sem = asyncio.Semaphore(max_concurrency)
    async def _one(prompt: str) -> str:
        async with sem:
            return await self.generate(prompt, system)
    return await asyncio.gather(*[_one(p) for p in prompts])
```

这是抽象基类上的一个具体方法（非抽象）—— 所有提供商都会自动继承它。无需对 `anthropic_provider.py`、`openai_provider.py`、`gemini_provider.py` 或 `ollama_provider.py` 进行更改。

### 文件：`worker/llm/base.py` — LoggingLLMProvider

添加包装器：

```python
async def generate_batch(
    self,
    prompts: list[str],
    system: str = "",
    max_concurrency: int = 5,
) -> list[str]:
    logger.debug("LLM REQUEST (batch): %d prompts, system=%s", len(prompts), _truncate(system))
    results = await self._provider.generate_batch(prompts, system, max_concurrency)
    logger.debug("LLM RESPONSE (batch): %d responses, total %d chars",
                 len(results), sum(len(r) for r in results))
    return results
```

---

## 2. 更丰富的文件摘要

### 文件：`worker/pipeline/ast_analysis.py`

修改 `FileAnalysis.to_llm_summary()` 签名：

```python
def to_llm_summary(self, max_files: int = 0, dep_graph: DependencyGraph | None = None) -> str:
```

- `max_files=0` 表示无限制。安全上限为 800 个文件 —— 超过此上限后，省略的文件将仅列出路径（无实体/文档），以便规划器仍知晓它们的存在。
- 对于每个文件，追加最多两行额外信息：
  - 导入/外部依赖行：`  imports: mod_a, mod_b | external: fastapi, pydantic`
  - 第一个顶层实体的文档字符串（截断至 120 字符）：`  "FastAPI application lifecycle and startup configuration."`
- 无实体的文件仍显示 `(no named entities)`，但如果有导入行则会显示。

### 输出格式示例

```
api/main.py: 0 classes, 1 functions [lifespan]
  imports: shared.config, shared.database | external: fastapi
  "FastAPI application lifecycle and startup configuration."
worker/jobs.py: 0 classes, 12 functions [_update_job, _update_repo, ...]
  imports: shared.config, shared.database, worker.pipeline.ast_analysis, ... | external: sqlalchemy
  "ARQ job functions that orchestrate the 6-stage wiki generation pipeline."
tests/conftest.py: 0 classes, 3 functions [fixture_repo_path, mock_llm, mock_embedding]
  (no dependencies)
```

### 调用者

更新 `wiki_planner.py` 中的 `_build_prompt()` 以及 `jobs.py` 中的 `run_full_index`/`run_refresh_index`，在调用 `to_llm_summary()` 时传递 `dep_graph`。

---

## 3. 依赖感知分组

> **[部分实现]** 实现了 `format_for_llm_prompt()` 的更改。未实现 `_split_large_cluster()` 和相关的 `_compute_clusters()` 修改 —— 请参见上文的实现说明。

### 文件：`worker/pipeline/dependency_graph.py`

~~添加函数：~~

```python
# 未实现 — 从未添加 _split_large_cluster()。
# 大型集群改为在提示词构建器层级处理（见下文）。
def _split_large_cluster(
    cluster: list[str],
    edges: dict[str, list[str]],
    max_size: int = 15,
) -> list[list[str]]:
    """使用 BFS 种子分组将大型集群拆分为子集群。

    算法：
    1. 选择导入边最多的文件作为第一个种子。
    2. 通过子图向外进行 BFS，添加文件直到达到 max_size。
    3. 剩余未访问的文件成为下一个种子的候选池。
    4. 重复直到所有文件都已分配。

    返回子集群列表，每个列表按字母顺序排序。
    """
```

~~修改 `_compute_clusters()`，对任何超过 `max_size=15` 的组件调用 `_split_large_cluster()`。~~

### 文件：`worker/pipeline/dependency_graph.py` — `format_for_llm_prompt()`

- ~~移除 `max_edges=150` 默认值。~~ 新签名：`max_edges: int = 500`。✅ **已实现。**
- 除非超过 500 个（极端仓库的安全上限），否则显示所有边。

### 规划器提示词中的集群呈现

> **[与规范不同]** `_build_outline_prompt()` 中的实际实现：显式列出文件数 ≤ 20 的集群；文件数 > 20 的集群发出一条摘要行（“大型集群 (N 个文件) —— 请参见上文的依赖关系以了解内部结构”）。上限为 30 个原始集群（非子集群）。未实现“不截断”和“子集群替换集群提示”。

~~在构建阶段 1 和阶段 2 提示词时（第 5 节），子集群取代旧的集群提示：~~
~~- 不截断集群或集群内的文件。~~
~~- 安全上限为 30 个子集群。超过此限制后，显示前 30 个 + "... and N more clusters"。~~

---

## 4. 动态页面计数

### 文件：`worker/pipeline/wiki_planner.py`

添加函数：

```python
def _suggest_page_range(file_count: int, entity_count: int) -> tuple[int, int]:
    """根据仓库复杂度建议最小/最大页面计数。"""
```

启发式表格：

| 文件数 | 实体数 | 最小 | 最大 |
|-------|----------|-----|-----|
| < 10 | 任意 | 3 | 6 |
| 10–30 | < 50 | 5 | 12 |
| 10–30 | ≥ 50 | 8 | 15 |
| 30–100 | < 150 | 10 | 25 |
| 30–100 | ≥ 150 | 15 | 35 |
| 100–300 | 任意 | 20 | 50 |
| 300+ | 任意 | 30 | 70 |

注入到阶段 1 大纲提示词中（第 5 节）：

```
- 创建 {min} 到 {max} 个页面。相比于宽泛的页面，更倾向于粒度更细的页面
  —— 涵盖 3-5 个相关文件的专注页面优于涵盖 15 个以上文件的庞杂页面。
  每个页面应具有明确且单一的职责。
```

该范围是给 LLM 的引导。强制执行发生在验证阶段（第 6 节）。

---

## 5. 双阶段规划

### 文件：`worker/pipeline/wiki_planner.py`

将 `generate_wiki_plan()` 中单个 `generate_structured` 调用替换为两个阶段。

#### 阶段 1 — 大纲生成

新函数：

```python
async def _generate_outline(
    file_summary: str,
    repo_name: str,
    llm: LLMProvider,
    readme: str | None,
    dep_info: str | None,
    clusters: list[list[str]] | None,
    page_range: tuple[int, int],
    system: str,
    on_retry: OnRetryCallback | None,
    max_retries: int = 3,
) -> list[dict]:
    """阶段 1：生成不含文件分配的页面树。"""
```

架构：

```json
{
  "pages": [
    {
      "title": "string",
      "purpose": "string",
      "parent": "string | null"
    }
  ]
}
```

系统提示词强调结构化思维：“思考概念架构。有哪些主要子系统？开发者需要学习什么？创建一个逻辑清晰的 Wiki 页面层级，帮助开发者理解此项目。”指令中不提及文件。

提示词中仍包含文件摘要和依赖信息作为上下文 —— LLM 需要了解存在哪些内容，只是不需要进行文件分配。

#### 阶段 2 — 文件分配

新函数：

```python
async def _assign_files(
    outline: list[dict],
    file_summary: str,
    dep_info: str | None,
    all_files: list[str],
    llm: LLMProvider,
    system: str,
    on_retry: OnRetryCallback | None,
    max_retries: int = 3,
) -> dict:
    """阶段 2：将每个文件分配到大纲中的某个页面。"""
```

架构：

```json
{
  "assignments": [
    {
      "file": "string",
      "page_title": "string"
    }
  ]
}
```

系统提示词：“给定此 Wiki 结构，将每个源文件分配到最合适的一个页面。紧密耦合（相互导入）的文件应尽可能分配到同一页面。”

#### 编排器

`generate_wiki_plan()` 变为：

1. 通过 `_suggest_page_range()` 计算 `page_range`
2. 调用 `_generate_outline()` —— 验证失败时重试最多 `max_retries` 次
3. 调用 `_assign_files()` —— 验证失败时重试最多 `max_retries` 次
4. 合并大纲 + 分配结果为 `WikiPlan`
5. 运行 `validate_wiki_plan()`

回退机制：
- 阶段 1 在所有重试后仍失败 → 基于集群的回退规划（现有逻辑）
- 阶段 2 在所有重试后仍失败 → 在大纲页面间轮询分发文件（按路径排序）

#### 已移除内容

旧的 `_build_prompt()` 函数被分阶段的提示词构建器取代。旧的 `_WIKI_PLAN_SCHEMA` 被两个分阶段的架构取代。

---

## 6. 增强的规划验证

### 文件：`worker/pipeline/wiki_planner.py`

更新 `validate_wiki_plan()` 签名：

```python
def validate_wiki_plan(
    raw: dict,
    all_files: list[str] | None = None,
    existing_titles: set[str] | None = None,
    clusters: list[list[str]] | None = None,
    page_range: tuple[int, int] | None = None,
) -> WikiPlan:
```

新的验证规则（除了最后一条外，其余均引发 `ValueError` 以触发 LLM 重试）：

| 规则 | 错误消息模板 |
|------|----------------------|
| 任何页面的文件数 > 25 | `"页面 '{title}' 包含 {n} 个文件 —— 请拆分为文件数 ≤ 25 的专注子页面"` |
| 非概览页的文件数为 0 | `"页面 '{title}' 未分配任何文件 —— 请分配文件或移除该页面"` |
| 层级深度 > 4 | `"Wiki 层级深达 {depth} 层 —— 请展平至最多 4 层"` |
| 文件数 > 30 的仓库采用扁平规划（深度=1） | `"所有页面均为顶层 —— 请为包含 {n} 个文件的仓库创建 2-3 个层级"` |
| 页面计数 < `page_range[0]` | `"规划包含 {n} 个页面，但最小要求为 {min} —— 请创建粒度更细的页面"` |
| 集群文件分散在 3 个以上页面中 | 仅记录警告（不触发重试） |

验证顺序：先进行结构检查（现有），后进行语义检查（新增）。这确保了 LLM 在每次重试时都能获得最具操作性的错误消息。

---

## 7. 多智能体自底向上生成

> **[被 2026-04-10 的重新设计部分取代]** 按设计实现了 `compute_generation_order()` 和自底向上的 `jobs.py` 编排循环。`generate_page()` 和 `generate_page_batch()` 已被取代：4 次传递页面质量重新设计（参见 `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md`）完全替换了单次生成。`generate_page()` 现在接受 `fast_llm` 并按 大纲 → 草案 → 事实核查 → 修订 顺序运行。`generate_page_batch()` 现在通过 `asyncio.gather` 并发运行单个 `generate_page()` 调用 —— 它**不**再收集扁平提示词并调用 `llm.generate_batch()`。本节中的父页面指令模板也被 2026-04-10 规范第 6 节中描述的 4 次传递父页面流程所取代。

### 文件：`worker/pipeline/page_generator.py`

#### `compute_generation_order()`

```python
def compute_generation_order(plan: WikiPlan) -> list[list[WikiPageSpec]]:
    """返回按深度分组的页面，最深层优先。

    返回：[[最深层页面], ..., [根页面]]
    """
```

算法：构建 标题→子页面 映射。从根节点开始 BFS 计算深度。按深度分组并反转顺序。

#### 更新后的 `generate_page()`

新参数：

```python
async def generate_page(
    spec: WikiPageSpec,
    store: FAISSStore,
    llm: LLMProvider,
    embedding: EmbeddingProvider,
    repo_name: str,
    child_contents: list[PageResult] | None = None,
    ...
) -> PageResult:
```

当提供 `child_contents` 时：
- 在提示词中追加一个“子页面”部分，包含每个子页面的完整 Markdown
- 切换到父页面专用指令模板（见下文）
- 对于子页面已涵盖的内容，跳过 RAG 检索（仍为父页面自身的文件进行检索）

#### 父页面指令模板

```
为 "{title}" 编写一个 Wiki 页面，作为其子页面的入口点。结构如下：

## 概览 (Overview)
该子系统/区域的功能及存在意义。高层叙述。

## 架构 (Architecture)
子组件如何协同工作。包含一个 Mermaid 图表，展示子组件之间的关系和数据流。

## 关键设计决策 (Key Design Decisions)
跨多个子组件的重要架构选择。

## 工作原理 (How It Works)
连接各个子组件的端到端流程。

不要重复子页面的内容 —— 请按名称引用它们。
仅输出 Markdown。
```

#### 批量生成辅助工具

> **[已取代]** 最初的设计是收集扁平提示词并调用 `llm.generate_batch(prompts)`。已被 4 次传递页面质量重新设计取代：每个页面现在运行一个完整的 4 次传递 `generate_page()` 流水线。`generate_page_batch()` 通过 `asyncio.gather` 并发编排这些调用。

当前签名（供参考）：

```python
async def generate_page_batch(
    specs_with_children: list[tuple[WikiPageSpec, list[PageResult] | None]],
    store: FAISSStore,
    llm: LLMProvider,
    fast_llm: LLMProvider,
    embedding: EmbeddingProvider,
    repo_name: str,
    file_analysis: FileAnalysis,
    dep_graph: DependencyGraph,
    on_retry: OnRetryCallback | None = None,
    wiki_language: str = "en",
) -> list[PageResult]:
    """使用多阶段流水线批量生成所有页面。

    通过 asyncio.gather 和信号量（最大 5）为所有规格并发运行 
    generate_page()（4 阶段：大纲 → 草案 → 事实核查 → 修订）。
    """
```

### 文件：`worker/jobs.py`

#### `run_full_index` — 替换扁平循环

替换第 539-582 行（扁平的 `for i, page_spec in enumerate(plan.pages)` 循环）：

```python
# 阶段 6：自底向上页面生成
levels = compute_generation_order(plan)
generated: dict[str, PageResult] = {}

for depth_idx, level in enumerate(levels):
    # 为该层级的每个页面收集子页面内容
    specs_with_children = []
    for page_spec in level:
        children = [
            generated[p.slug]
            for p in plan.pages
            if p.parent == page_spec.title and p.slug in generated
        ]
        specs_with_children.append((page_spec, children or None))

    results = await generate_page_batch(
        specs_with_children, store, llm, embedding,
        repo_name=name, file_analysis=file_analysis,
        dep_graph=dep_graph, on_retry=_on_retry,
        wiki_language=wiki_language,
    )

    for result, (page_spec, _) in zip(results, specs_with_children):
        generated[result.slug] = result
        # 写入数据库和磁盘
        page_order = ...  # 跨所有层级的顺序
        async with get_session(db_path) as s:
            s.add(WikiPage(...))
            await s.commit()
        await _write_text_async(wiki_dir / f"{result.slug}.md", result.content)

    # 更新进度
    await _update_job(db_path, job_id, progress=..., status_description=...)
```

#### `run_refresh_index` — 同样的自底向上处理

增量刷新路径的阶段 6 也采用同样的自底向上顺序。如果父页面正在重新生成，则已保留（未更改）的页面可作为 `child_contents` 使用 —— 从磁盘加载它们的内容。

---

## 测试策略

每项改进都有对应的测试补充：

1. **generate_batch**：测试默认的 gather 实现、测试最大并发限制、测试 LoggingLLMProvider 包装。
2. **更丰富的摘要**：测试带有 `dep_graph` 参数的 `to_llm_summary()`，验证导入/文档字符串行。
3. ~~**子聚类**：测试针对不同规模集群的 `_split_large_cluster()`，验证是否遵守 max_size~~ —— 未实现，无需测试。
4. **页面范围**：在每个边界测试 `_suggest_page_range()`。
5. **双阶段规划**：独立测试 `_generate_outline()` 和 `_assign_files()`，测试编排器的回退机制。
6. **验证**：测试每条新规则是否以正确的消息触发 ValueError。
7. **自底向上生成**：测试针对不同树结构的 `compute_generation_order()`，测试 `generate_page_batch()`，测试父页面提示词是否包含子页面内容。
