# 方案：提升 Wiki 生成质量

> **[已完成 —— 已被取代]** 本方案中的改进已实施（依赖图、增强型 AST 分析、架构图、RAG 索引、规划器改进）。然而，其中描述的具体 API（`build_enhanced_module_tree()`、增强型模块树 JSON 格式、`DependencyGraph` 聚类）后来在流水线重构工作和进一步的 Wiki 规划器改进工作（2026-04-08）中进行了重新设计。当前的实现使用 `FileAnalysis` / `analyze_all_files()` 代替了 `build_enhanced_module_tree()`，并使用 `WikiPlan` 代替了基于模块树的规划器。架构图生成（阶段 7）也在 PR #17 中被移除。

## 背景

AutoWiki 目前生成的 Wiki 页面结构非常基础。与 DeepWiki/CodeWiki 相比，生成的内容缺乏：
- **丰富的层次结构** —— 规划器只能获取到按顶层目录分组的扁平模块树
- **描述信息** —— 页面规范（page specs）没有用于引导内容生成的描述字段
- **依赖感知** —— 没有导入/依赖图提取；模块分组过于简单
- **源码引用** —— 生成的页面没有引用文件:行号范围
- **架构图** —— 没有 Mermaid 图表的生成或渲染
- **实体上下文** —— AST 提取的实体（类、函数、行号范围）未传递给规划器或页面生成器

本方案将在整个流水线中增加 6 项改进，以产生更丰富、更全面的 Wiki 输出。

---

## 1. 新模块：依赖图提取

**新文件：`worker/pipeline/dependency_graph.py`**

使用正则提取导入关系（对于导入，正则比 Tree-Sitter 更简单，且适用于所有语言）：

```python
@dataclass
class DependencyGraph:
    edges: dict[str, list[str]]        # 文件 → [导入的文件/模块]
    clusters: list[list[str]]          # 紧密连接的文件组
    external_deps: dict[str, list[str]] # 文件 → [外部包]
```

**特定语言的导入模式：**
- Python: `import X`, `from X import Y`
- JS/TS: `import ... from 'X'`, `require('X')`
- Go: `import "X"`
- Java/Kotlin: `import X.Y.Z`
- Rust: `use X::Y`, `mod X`
- C/C++: `#include "X"`（仅限本地）

**聚类：** 使用简单的连通分量分析 —— 互相频繁导入的文件应属于同一个 Wiki 页面。这为规划器提供了更好的分组依据。

**文件：** `worker/pipeline/dependency_graph.py`（新增）

---

## 2. 增强型 AST 分析

**文件：`worker/pipeline/ast_analysis.py`**

### 2a. 提取 docstring 和签名
在 `_extract_entities()` 中增加：
- Python：提取类/函数节点的第一个字符串字面量子节点（docstring）
- JS/TS/Java：提取前面的注释节点
- 提取函数参数列表作为签名字符串

实体字典变为：
```python
{
    "type": "class" | "function",
    "name": str,
    "start_line": int,
    "end_line": int,
    "signature": str | None,    # 新增：例如 "def greet(name: str) -> str"
    "docstring": str | None,    # 新增：docstring 的前 200 个字符
}
```

### 2b. 增强型模块树
新函数 `build_enhanced_module_tree()` 返回：
```python
{
    "path": "src/auth",
    "files": [...],
    "entities": [{"name": "User", "type": "class", ...}, ...],
    "file_count": 5,
    "class_count": 3,
    "function_count": 12,
    "summary": "包含 User, AuthService, validate_token, ..."  # 列出顶部实体
}
```

这为规划器提供了关于每个模块内容的更丰富上下文。

---

## 3. 增强型 RAG 索引器

**文件：`worker/pipeline/rag_indexer.py`**

### 3a. 更丰富的块元数据
在元数据中增加行号范围和实体上下文：
```python
{
    "text": chunk_text,
    "file": relative_path,
    "chunk_idx": int,
    "start_line": int,      # 新增
    "end_line": int,         # 新增
    "entity": str | None,    # 新增：所属的类/函数名称（如果已知）
}
```

在分块过程中通过跟踪字符偏移量来计算 `start_line`/`end_line`。

### 3b. 实体感知分块
新函数 `chunk_file_with_entities()` 使用 AST 实体边界进行分块：
- 如果一个函数/类能放进一个块中，保持其完整性
- 仅分割超过 `chunk_size` 的实体
- 这确保了 RAG 检索返回完整、连贯的代码块

---

## 4. 增强型 Wiki 规划器

**文件：`worker/pipeline/wiki_planner.py`**

### 4a. 为 PageSpec 增加 `description` 字段
```python
@dataclass
class PageSpec:
    title: str
    slug: str
    modules: list[str]
    parent_slug: str | None = None
    description: str | None = None    # 新增
```

更新 `_PLAN_SCHEMA` 以包含 `description` 必填字符串字段。

### 4b. 丰富的规划器提示词
新的 `_build_prompt()` 接收并包含：
- **README 摘要**（前 2000 个字符） —— 为 LLM 提供仓库级上下文
- **带有实体摘要的增强型模块树**（每个模块的类/函数名称）
- **依赖图摘要** —— 模块之间的依赖关系
- **依赖聚类** —— 基于导入分析的建议分组

### 4c. 改进的思维链系统提示词
```
你是一位资深技术文档架构师，正在为一个软件仓库创建一份全面的 Wiki 规划。
请分析代码库结构、依赖关系和关键实体，产出一个组织良好的分层 Wiki 规划。

请分步骤思考：
1. 识别主要的架构组件及其作用
2. 将紧密耦合的模块分组成连贯的页面
3. 创建清晰的层次结构（概览 → 子系统页面 → 详情页面）
4. 为每个页面编写简短的描述，解释其涵盖的内容及原因

每个页面都应该有明确的“目的” —— 不仅仅是列出文件，还要解释概念、组件或工作流。
对于拥有 5 个以上模块的仓库，目标是建立 2-3 层的层次结构。

仅输出有效的 JSON。
```

### 4d. 更新后的用户提示词模板
```
仓库：{repo_name}

README（摘要）：
{readme_content}

带有实体的模块树：
{enhanced_module_tree_json}

依赖图：
{dependency_summary}

建议聚类（基于导入分析）：
{clusters_json}

请创建一个分层的 Wiki 规划。指导方针：
- 根据仓库复杂度，规划 5–15 个页面
- 每个页面需要：title, slug, modules, parent_slug（用于嵌套）, description（1-2 句解释页面目的）
- 必须包含一个 "Overview" 页面作为根节点（parent_slug: null），涵盖架构和项目目的
- 参考依赖聚类对相关模块进行分组
- 创建 2-3 层的层次结构：概览 → 子系统 → 详情页面
- 页面标题应描述概念/组件，而不仅仅是目录名
- 描述应解释该页面涵盖的内容以及为什么它很重要

请按照以下 schema 输出 JSON：
{schema}
```

---

## 5. 增强型 页面生成器

**文件：`worker/pipeline/page_generator.py`**

### 5a. 更丰富的生成提示词
更新后的 `_build_page_prompt()` 接收：
- `spec.description` —— 使 LLM 专注于该页面应涵盖的内容
- 依赖上下文 —— 该模块依赖什么以及被什么依赖
- 实体详情 —— 该页面涉及模块的类/函数签名及 docstring

### 5b. 新的系统提示词
```
你是一位资深技术作家，正在为一个软件仓库编写全面的 Wiki 文档。
请基于提供的源代码编写准确、结构良好的页面。

规则：
- 每一项技术声明都必须能在提供的源代码中找到依据
- 在每个小节后，添加斜体形式的“源码”引用：*源码：path/to/file.py:10-45*
- 在有助于理解的地方加入 Mermaid 图表（架构、类关系、数据流）
- 使用 ```mermaid 代码块编写图表
- 不要虚构代码中不存在的 API 或功能
- 为初次接触该代码库的开发者编写
```

### 5c. 新的用户提示词模板
```
仓库：{repo_name}
页面：{spec.title}
目的：{spec.description}
模块：{modules_list}

依赖关系：
- 该模块依赖于：{deps_out}
- 被以下模块依赖：{deps_in}

这些模块中的关键实体：
{entity_details}

相关的源代码（带有文件路径和行号）：
{context_with_line_numbers}

请为 "{spec.title}" 编写一个全面的 Wiki 页面。结构要求：

## 概览 (Overview)
简要描述该组件的角色和目的。

## 架构 (Architecture)
包含一个 Mermaid 图表，展示该组件与其他组件的关系。
（使用 ```mermaid 块。根据需要选择：flowchart, classDiagram, 或 sequenceDiagram。）

## 关键组件 (Key Components)
针对每个主要的类/函数：
- 它的作用
- 它的接口/签名
- 如何使用它
在每个子节后引用源码：*源码：file.py:line-line*

## 依赖与交互 (Dependencies & Interactions)
该模块如何与代码库的其他部分连接。

## 源码文件 (Source Files)
列出该页面涵盖的所有文件并附带简短描述。

仅输出 Markdown。
```

### 5d. 多查询 RAG 检索
不再使用单一查询（标题 + 模块），而是生成多个查询：
1. 页面标题 + 描述
2. 模块中的每个实体名称
3. 模块交互术语

合并并去重结果，将 `top_k` 从 8 增加到 15。

### 5e. 块上下文中的源码引用
将 RAG 块格式化并带上行号：
~~~markdown
文件：src/auth/handler.py (第 15-42 行)
```python
class AuthHandler:
    ...
```
~~~

---

## 6. 前端 Mermaid 支持

**文件：`web/components/WikiPage.tsx`**

为 ```mermaid``` 代码块增加 Mermaid 图表渲染：
- 使用 `mermaid` npm 包
- 创建一个自定义的 `MermaidBlock` 组件在客户端渲染
- 将其作为自定义代码渲染器传递给 ReactMarkdown
- 使用 `useEffect` + `mermaid.render()` 进行动态渲染

**文件：`web/package.json`** —— 增加 `mermaid` 依赖

---

## 7. 流水线编排更新

**文件：`worker/jobs.py`**

更新 `run_full_index()` 以在各阶段间传递新数据：

```
阶段 1：摄取 (5-20%) —— 同时提取 README 内容
阶段 2：AST 分析 (20-30%) —— 构建带有实体的增强型模块树
阶段 2b：依赖图 (30-40%) —— 提取导入，构建图，计算聚类（新增）
阶段 3：RAG 索引器 (40-55%) —— 带有行号的实体感知分块
阶段 4：Wiki 规划器 (55-65%) —— 接收：增强型模块树 + 依赖图 + README
阶段 5：页面生成器 (65-100%) —— 接收：带有描述的页面规范 + 依赖上下文 + 实体
```

**文件：`worker/pipeline/ingestion.py`** —— 增加 `extract_readme()` 函数：
```python
def extract_readme(root: Path) -> str | None:
    for name in ["README.md", "README.rst", "README.txt", "README"]:
        p = root / name
        if p.exists():
            return p.read_text(errors="replace")[:3000]
    return None
```

---

## 8. 数据库 Schema 更新

**文件：`shared/models.py`**

在 WikiPage 中增加 `description` 列：
```python
description: Mapped[str | None] = mapped_column(Text, nullable=True)
```

---

## 待修改文件（按顺序）

| # | 文件 | 修改内容 |
|---|------|--------|
| 1 | `worker/pipeline/dependency_graph.py` | **新增** —— 导入提取 + 聚类 |
| 2 | `worker/pipeline/ast_analysis.py` | 增加 docstring、签名、增强型模块树 |
| 3 | `worker/pipeline/rag_indexer.py` | 元数据中的行号，实体感知分块 |
| 4 | `worker/pipeline/ingestion.py` | 增加 `extract_readme()` |
| 5 | `worker/pipeline/wiki_planner.py` | 描述字段，丰富的提示词，更好的系统提示词 |
| 6 | `worker/pipeline/page_generator.py` | 源码引用，图表，多查询 RAG，更丰富的提示词 |
| 7 | `worker/jobs.py` | 串联新阶段，传递增强数据 |
| 8 | `shared/models.py` | 在 WikiPage 中增加描述 |
| 9 | `web/components/WikiPage.tsx` | Mermaid 渲染 |
| 10 | `web/package.json` | 增加 mermaid 依赖 |
| 11 | `tests/conftest.py` | 为新 schema 更新 mock_llm |
| 12 | `tests/worker/test_dependency_graph.py` | **新增** —— 依赖提取测试 |
| 13 | `tests/worker/test_wiki_planner.py` | 为描述字段更新测试 |
| 14 | `tests/worker/test_page_generator.py` | 为新提示词结构更新测试 |
| 15 | `tests/worker/test_ast_analysis.py` | docstring、签名测试 |
| 16 | `tests/worker/test_rag_indexer.py` | 实体感知分块测试 |

---

## 验证

1. **单元测试**：`pytest tests/ --ignore=tests/e2e` —— 所有现有 + 新增测试通过
2. **集成测试**：`pytest tests/test_integration.py` —— 使用 fixture 的完整流水线
3. **手动检查**：针对 fixture 仓库运行并验证：
   - 页面规划包含描述和层次结构
   - 生成的页面包含 Mermaid 图表（Mermaid 代码块）
   - 存在源码引用（例如：*源码：models.py:5-20*）
   - 页面中包含依赖信息
4. **前端**：验证 Web UI 中的 Mermaid 渲染（需要 `npm run dev`）
