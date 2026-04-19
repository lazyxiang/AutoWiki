# 流水线重构方案

> **[已完成 —— 部分内容已过时]** 已实施并合并。主要偏差：(1) **阶段 7 (`diagram_synthesis.py`)** 随后在 Wiki 规划器改进工作中被移除 —— 本文档中“阶段 7：架构图”部分已作废。(2) **`module_tree.json`** 已按设计被 `wiki_plan.json` 取代。(3) 从 `jobs.py` 中提取的辅助函数（`_build_file_entities`、`_build_module_files` 等）已被此处描述的 `FileAnalysis` 单次扫描重构所取代；辅助函数表中的具体函数名已不再存在。(4) `synthesize_diagrams()` 曾短暂接受过 `WikiPlan`，但随后被彻底移除。

## 背景

当前的 6 阶段流水线按顶级目录将文件分组为“模块”，这阻碍了对细粒度逻辑子模块的识别。AST 分析在每个文件上重复运行（两次）。Wiki 规划器接收的是粗放的基于目录的分组，必须以此推断结构。本次重构使 LLM 成为逻辑结构的来源，消除了冗余，并采用了新的 `wiki.json` 格式，为 Phase 4 的用户引导功能做准备。

## 新的流水线阶段

```
阶段 1：摄取 (Ingestion)         (5→20%)   — 未改变
阶段 2：AST 分析 (AST Analysis)  (20→35%)  — 单次扫描，产出 FileAnalysis
阶段 3：依赖图 (Dependency Graph) (35→45%)  — 边界更清晰，文件级输出
阶段 4：RAG 索引器 (RAG Indexer)  (45→55%)  — 未改变（使用 FileAnalysis 获取实体）
阶段 5：Wiki 规划器 (Wiki Planner) (55→70%)  — LLM 生成逻辑页面树 + 文件分配
阶段 6：页面生成器 (Page Generator) (70→97%)  — 使用 WikiPageSpec（文件、目的）
阶段 7：架构图 (Architecture Diagram) (97→100%) — 使用 WikiPlan 代替 module_tree
```

## 新的数据结构

### FileAnalysis (`worker/pipeline/ast_analysis.py`)

取代 `build_module_tree`、`build_enhanced_module_tree` 和 `_build_file_entities`。

```python
@dataclass
class FileInfo:
    rel_path: str
    entities: list[dict]    # 来自 analyze_file() 的完整实体列表
    class_count: int
    function_count: int
    summary: str            # 逗号分隔的顶部实体名称

@dataclass
class FileAnalysis:
    files: dict[str, FileInfo]   # 以 rel_path 为键

    def to_llm_summary(self, max_files: int = 200) -> str:
        """为 LLM 规划器提示词生成紧凑的单文件摘要。"""
```

### WikiPlan / WikiPageSpec (`worker/pipeline/wiki_planner.py`)

取代 `PagePlan` / `PageSpec`。

```python
@dataclass
class WikiPageSpec:
    title: str
    purpose: str                         # 曾名为 "description"
    parent: str | None = None            # 父页面标题 (非 slug)
    page_notes: list[dict] | None = None # [{"content": ""}] — Phase 4 准备
    files: list[str] | None = None       # 由 LLM 分配的 rel_paths

    @property
    def slug(self) -> str:               # 派生属性，不存储在 wiki.json 中
        return re.sub(r"[^a-z0-9-]+", "-", self.title.lower()).strip("-")

    @property
    def parent_slug(self) -> str | None:
        if self.parent is None:
            return None
        return re.sub(r"[^a-z0-9-]+", "-", self.parent.lower()).strip("-")

@dataclass
class WikiPlan:
    repo_notes: list[dict]      # [{"content": ""}] — Phase 4 准备
    pages: list[WikiPageSpec]

    def to_wiki_json(self) -> dict:
        """面向用户的 wiki.json（无 slug，无文件列表）。"""

    def to_internal_json(self) -> dict:
        """流水线内部格式（包含用于增量刷新的文件列表）。"""

    def to_api_structure(self) -> dict:
        """API 兼容格式（包含派生的 slug/parent_slug）。"""
```

**wiki.json 格式**（写入 `wiki/wiki.json`）：
```json
{
  "repo_notes": [{"content": ""}],
  "pages": [
    {"title": "Overview", "purpose": "高层级介绍...", "page_notes": [{"content": ""}]},
    {"title": "Engine Architecture", "purpose": "核心引擎组件...", "page_notes": [{"content": ""}]},
    {"title": "Scheduler", "purpose": "调度算法...", "parent": "Engine Architecture", "page_notes": [{"content": ""}]}
  ]
}
```

**内部格式**（写入 `ast/wiki_plan.json` —— 包含用于刷新的文件映射）：
```json
{
  "repo_notes": [{"content": ""}],
  "pages": [
    {"title": "Overview", "purpose": "...", "files": ["README.md", "main.py"]},
    {"title": "Engine Architecture", "purpose": "...", "files": ["engine/core.py", "engine/client.py"]},
    {"title": "Scheduler", "purpose": "...", "parent": "Engine Architecture", "files": ["engine/scheduler.py"]}
  ]
}
```

**API 结构**（存储在 `Repository.wiki_structure` 中 —— 包含用于前端的派生 slug）：
```json
{
  "pages": [
    {"title": "Overview", "slug": "overview", "parent_slug": null, "description": "高层级介绍..."},
    {"title": "Engine Architecture", "slug": "engine-architecture", "parent_slug": null, "description": "核心引擎..."},
    {"title": "Scheduler", "slug": "scheduler", "parent_slug": "engine-architecture", "description": "调度..."}
  ]
}
```

这保持了 API 响应中的 `description` 和 `parent_slug` 键 —— **无需修改前端/API**。

## LLM Wiki 规划器的变更

规划器提示词不再接收按目录分组的模块树，而是接收：

1. **文件级摘要**（来自 `FileAnalysis.to_llm_summary()`） —— 包含实体计数 + 顶部实体名称的扁平文件列表
2. **README 摘要**
3. **文件级依赖关系边** + 来自 `DependencyGraph` 的聚类信息

LLM 输出新的 schema：
```json
{
  "pages": [{
    "title": "string",
    "purpose": "string",
    "parent": "string | null",
    "files": ["string"]
  }]
}
```

**验证** (`validate_wiki_plan`)：
- 每个仓库文件必须出现在至少一个页面中（孤立文件将追加到 Overview 页面）
- 父页面标题必须引用现有的页面标题
- 必须至少存在一个页面
- 回退方案：包含 Overview + 每个依赖聚类一个页面的扁平规划

## 逐个文件的变更

### `worker/pipeline/ast_analysis.py`
- **移除**：`build_module_tree()`、`build_enhanced_module_tree()`
- **新增**：`FileInfo`, `FileAnalysis` 数据类, `analyze_all_files(root, files) -> FileAnalysis`
- **保留**：`analyze_file()`，所有 tree-sitter 逻辑保持不变

### `worker/pipeline/wiki_planner.py`
- **移除**：`PageSpec`, `PagePlan`, `_PLAN_SCHEMA`, `validate_page_plan()`
- **新增**：`WikiPageSpec`, `WikiPlan`, 新的 `_WIKI_PLAN_SCHEMA`, `validate_wiki_plan()`
- **修改**：`generate_page_plan()` → `generate_wiki_plan()` —— 新签名接收 `FileAnalysis` + `DependencyGraph`
- **修改**：`_build_prompt()` —— 接收文件摘要 + 依赖图，而非增强型树结构

### `worker/pipeline/page_generator.py`
- **修改**：接收 `WikiPageSpec` 而非 `PageSpec`
- **修改**：`_build_page_prompt()` —— 使用 `spec.purpose` 和 `spec.files` 代替 `spec.description` 和 `spec.modules`
- **保留**：RAG 多查询逻辑、`PageResult`、格式化辅助函数

### `worker/pipeline/dependency_graph.py`
- **移除**：`summarize_dependencies()`
- **新增**：`format_for_llm_prompt(graph, max_edges=150) -> str` —— 为规划器格式化文件级依赖
- **新增**：`summarize_page_deps(page_files, graph) -> dict` —— 为页面生成器提供每页的依赖摘要
- **保留**：`DependencyGraph`, `build_dependency_graph()`, `_compute_clusters()`

### `worker/pipeline/diagram_synthesis.py`
- **修改**：`synthesize_diagrams()` 接收 `WikiPlan` 而非 `module_tree`
- 将页面标题（而非目录名）格式化为图表节点

### `worker/pipeline/ingestion.py`
- **移除**：`get_affected_modules()`
- **新增**：`get_affected_pages(changed_files, wiki_plan) -> set[str]` —— 返回受影响页面的标题
- **保留**：其他内容保持不变

### `worker/jobs.py`
- **移除**：`_build_file_entities()`, `_build_module_entity_map()`, `_build_module_files()`, `_collect_page_context()`
- **移除**：`build_module_tree`, `build_enhanced_module_tree`, `summarize_dependencies` 的导入
- **新增**：`_collect_page_entities(page_spec, file_analysis) -> list[dict]`
- **新增**：`_collect_page_deps(page_spec, dep_graph) -> dict`
- **修改**：`run_full_index()` —— 使用 `FileAnalysis` 和 `WikiPlan` 的新阶段流程
- **修改**：`run_refresh_index()` —— 加载 `wiki_plan.json` 而非 `module_tree.json`，并使用 `get_affected_pages()`
- **持久化**：`wiki/wiki.json` (面向用户), `ast/wiki_plan.json` (包含文件列表的内部文件), `Repository.wiki_structure` (带 slug 的 API 兼容格式)

### `shared/models.py` —— 无变更
### `api/` —— 无变更
### `web/` —— 无变更

## 对 `run_refresh_index` 的影响

1. 从 `ast/wiki_plan.json` 加载之前的 `WikiPlan`（取代 `module_tree.json`）
2. `get_affected_pages(changed_files, wiki_plan)` → 具有重合文件的页面标题集合
3. 如果文件被添加/删除（不仅仅是修改），则回退到全量重新规划
4. 否则，仅重新规划受影响的页面（将现有规划上下文传递给 LLM）
5. 仅重新生成受影响的页面

## 测试更新

### `tests/worker/test_ast_analysis.py`
- 移除 `build_module_tree`, `build_enhanced_module_tree` 的测试
- 增加 `analyze_all_files`, `FileAnalysis.to_llm_summary()` 的测试

### `tests/worker/test_wiki_planner.py`
- 针对 `WikiPageSpec`/`WikiPlan`, `validate_wiki_plan()`, `generate_wiki_plan()` 进行重写

### `tests/worker/test_page_generator.py`
- 更新 `PageSpec` → `WikiPageSpec`, `modules` → `files`, `description` → `purpose`

### `tests/worker/test_dependency_graph.py`
- 移除 `test_summarize_dependencies*`
- 增加 `format_for_llm_prompt()`, `summarize_page_deps()` 的测试

### `tests/worker/test_diagram_synthesis.py`
- 更新为传递 `WikiPlan` 而非 `module_tree`

### `tests/worker/test_jobs.py`, `tests/worker/test_refresh.py`, `tests/api/test_repos.py`
- 更新 `module_tree.json` 引用 → `wiki_plan.json`
- 将 mock LLM 的返回值更新为新的 schema

### `tests/worker/test_ingestion.py`
- 使用 `test_get_affected_pages` 替换 `test_get_affected_modules`

## 实施顺序

1. `ast_analysis.py` —— 新的 `FileInfo`, `FileAnalysis`, `analyze_all_files()`；移除旧的构建器
2. `wiki_planner.py` —— 新的 `WikiPageSpec`, `WikiPlan`, schema, prompt, 校验
3. `dependency_graph.py` —— 添加 `format_for_llm_prompt()`, `summarize_page_deps()`；移除 `summarize_dependencies()`
4. `page_generator.py` —— 接收 `WikiPageSpec`，使用 `purpose`/`files`
5. `diagram_synthesis.py` —— 接收 `WikiPlan`
6. `ingestion.py` —— 使用 `get_affected_pages` 替换 `get_affected_modules`
7. `jobs.py` —— 串联所有环节，移除旧的辅助函数
8. 更新所有测试
9. 端到端验证：`pytest tests/ --ignore=tests/e2e` + `ruff check . && ruff format --check .`

## 验证

```bash
# 单元测试
pytest tests/ --ignore=tests/e2e

# Lint
uv run ruff check .
uv run ruff format --check .
cd web && npm run lint

# 手动 E2E（可选）
# autowiki index github.com/some/small-repo
# 检查 wiki.json, wiki_plan.json, 生成的页面
```
