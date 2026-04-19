# 以页面为中心的文件选择实施计划

> **针对代办智能体:** 必需子技能: 使用 superpowers:subagent-driven-development (推荐) 或 superpowers:executing-plans 来逐任务执行此计划。步骤使用复选框 (`- [ ]`) 语法进行跟踪。

**目标:** 将当前的“将每个文件划分到正好一个页面”的计划器第二阶段 (Phase 2) 替换为以页面为中心的选择模型，该模型为每个维基页面选择 5-8 个（范围 3-10 个）具有代表性的源代码文件，从而消除强制全覆盖约束。

**架构:** 维基计划器的第二阶段将其 LLM 架构从以文件为中心的分配 (`{file → primary_page}`) 更改为以页面为中心的选择 (`{page_title → [files]}`)。预过滤步骤使用现有的令牌匹配逻辑，在 LLM 选择最佳的 5-8 个文件之前，将每个页面的候选池缩小到约 25 个文件。一个评分函数（实体密度 + 依赖入度 + 文件类型 + 语义对齐）为启发式回退提供支持。`WikiPlan` 获得一个 `all_repo_files` 快照，以便增量刷新可以检测添加/删除的文件，而无需从现在更小的 `page.files` 列表中派生它们。

**技术栈:** Python, asyncio, 不含 pydantic 的 dataclass, FAISS (未更改), ARQ worker, 现有的 `async_retry` / `PromptSegment` / `pipeline_logging` 模式。

---

## 设计决策

### 文件计数目标: 5–8，范围 3–10

- 硬上限 `MAX_FILES_PER_PAGE = 10`（从 50 下调）。`page_outline.py:242` 计算 `n_sections = max(3, len(files) // 2)` — 对于 5-10 个文件，这将产生 3-5 个大纲章节，这是合适的粒度。
- 验证中强制执行硬下限: 1 个文件（结构化父页面/概览页面可能合法地拥有 0 个，并像以前一样获得豁免）。
- 提示词指示 LLM 目标为 **5–8**，接受 3–10。
- `page_generator.py:202` 仅获取 `spec.files[:5]` 用于 RAG 查询字符串。所选文件在 LLM 选择后按**相关性得分**（降序）排序，因此 `[:5]` 始终产生最高得分的子集。第 6 到第 10 个文件仅影响实体提取和源文件表格。

### RAG 检索不受更小的 `page.files` 影响

FAISS 索引是从所有仓库文件构建的，并且在搜索时没有限制 (`jobs.py` 中的 `build_rag_index`)。`spec.files` 仅为查询字符串贡献路径令牌（第 202 行: `f"{spec.title} {' '.join(spec.files[:5])}"`），并驱动实体提取/依赖摘要。语义相似性将自动从非选定文件中浮现出相关的块。`spec.purpose`（第 203-204 行）提供了独立于所选文件的专题覆盖。生成的散文质量保持不变；实体列表和源文件表格变得更加集中 — 这是一项改进。

### `secondary_files` 保留但不由新的 Phase 2 生成

现有的 `secondary_files` 字段及其页面生成器注入（“参考模块”块）保持不变。新的选择步骤仅返回主要选择；`secondary_files` 默认为 `[]`。这避免了破坏 `page_generator.py:370–374`，并且是一个安全的空操作 — 未来的工作可以将跨页面引用作为明确的选择选项重新引入。

### 移除孤儿强制执行

`validate_wiki_plan` 的“关键孤儿”检查 (`VALIDATION_FAILURE: N core source files are missing`) 被删除。未被任何页面选择的文件被有意忽略。低优先级文件日志（第 1337-1340 行）也被移除，因为该概念不再适用。

### 用于刷新的 `WikiPlan.all_repo_files`

`jobs.py:932` 目前计算 `old_all_files = {f for p in old_plan.pages for f in p.files}`。由于 `page.files` 现在每个页面仅包含 3-10 个文件，这个并集比实际仓库小得多，会产生一个严重虚高的 `added_files` 集合，这会导致概览页面在每次刷新时都被标记为过时。修复: 在 `WikiPlan` 中添加 `all_repo_files: list[str]`，持久化在 `ast/wiki_plan.json` 中，并在刷新对比中读取它。

---

## 更改的文件

| 文件 | 更改内容 |
|---|---|
| `worker/pipeline/wiki_planner.py` | 主要更改: 常量、架构、评分、预过滤、提示词构建器、批量执行器、回退、验证、`WikiPlan.all_repo_files` |
| `worker/jobs.py` | 从计划 JSON 中加载 `all_repo_files`；在刷新对比中使用它（第 833–848, 932–935 行） |
| `tests/test_wiki_planner.py` | 针对评分、预过滤、选择验证、回退、序列化的新测试 |

`page_generator.py`, `page_draft.py`, `page_outline.py`, `ingestion.py`, 或 `fixture_recorder.py` 没有更改。
