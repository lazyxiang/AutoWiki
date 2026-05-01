# AutoWiki 页面生成质量 —— 设计规范

**状态：** 草案 2（根据真实数据审查修订）
**作者：** lazyxiang
**日期：** 2026-04-29
**范围：** Wiki 页面生成流水线 —— 第二阶段文件选择（`worker/pipeline/wiki_planner.py`）以及每页生成器（`worker/pipeline/page_generator.py` + `page_outline.py` + `page_draft.py` + `fact_check.py`）。
**不属于范围：** 构建 Skill 包本身。Skill 提取将是一个独立的项目；本规范仅要求 A 层和 B 层使 Wiki 流水线保持 Skill 就绪形态（确定性、无嵌入、可重用的令牌工具）。
**相关内容：** Issue #39, PR #40, `docs/superpowers/plans/2026-04-10-wiki-page-quality-redesign.md`。

---

## 1. 执行摘要

对 AutoWiki 自身进行索引产生的 Wiki 其**结构是正确**的，但其**页面内容在四个不同的方面存在系统性错误**：分配的文件未按相关性排序，协调器（orchestrator）类文件的覆盖率不足，兄弟页面之间内容渗透，以及整个前端/后端子树被分配给错误的源文件，因为第二阶段文件评分器仅支持 ASCII（且即使有中日韩分词器，中日韩页面标题与英文源路径之间也共享零令牌 —— 需要真正的跨语言桥梁）。

PR #40 提高了检索/令牌/边缘预算，这是必要的，但**它本身并不能修复其中的任何缺陷** —— 这些缺陷是结构性的（排序、所有权、范围划分、跨语言匹配），而非容量限制。

本规范提出了两层树内补救措施，以及为以后独立的 Skill 项目保留可能性的设计约束：

- **A 层 —— 规划器与提示词补丁（1 个 PR，约 350 行代码）。** 对文件排序进行软校验，引入兄弟页面感知的大纲 + 受范围约束的草稿，具有按相关性权重分配的每文件配额的多查询检索，提取共享的中日韩感知分词器，**使 `en_keywords` 对非英文标题成为强制要求**（这是真正的跨语言桥梁，而非分词），强制执行兄弟页面间的文件所有权，并增加自适应的每页文件预算。目标是 P1、P3、P4；部分缓解 P2。
- **B 层 —— 章节级起草 + BM25 检索 + 第 4 阶段删除（2 个 PR，约 700 行代码）。** 用 `大纲 → 骨架 → 每章节起草 → 缝合` 替换单次起草，增加确定性 BM25 检索器，**直接删除第 4 阶段（FAISS 构建），从索引流水线中移除嵌入提供者，并暂时禁用深度研究（Deep Research）**（深度研究是唯一的 FAISS 消费者；它将在后续迁移到关键词检索）。聊天（Chat）已经不再依赖 FAISS。

树内没有 C 层。Skill 就绪性体现为设计约束（确定性检索、可重用工具、Wiki 索引路径中完全不依赖 FAISS/嵌入），而不是体现为一个 Skill 包。

---

## 2. 当前状态

### 2.1 流水线形态

```
Repo URL ──▶ Stage 1 Ingestion ──▶ Stage 2 AST analysis ──▶ Stage 3 Dep graph
        ──▶ Stage 4 RAG indexer (FAISS) ──▶ Stage 5 Wiki planner (2-phase LLM)
        ──▶ Stage 6 Page generator (4-pass LLM, per page, with concurrency)
```

Wiki 规划器由两个 LLM 调用组成：第一阶段生成大纲（标题、用途、父级、可选的 `en_keywords`）；第二阶段从预过滤的 25 个（PR #40 中为 40 个）候选文件集中为每个页面选择 5-8 个源文件。页面生成器为每个页面运行四个阶段：大纲（快速模型）→ 草稿（主模型）→ 事实核查（快速模型）→ 条件修订（主模型）。

### 2.2 大纲和草案提示词实际消耗的内容

**第 1 阶段 —— 大纲 (`worker/pipeline/page_outline.py:224-254`)**

| 来源 | 内容 | 限制 |
|---|---|---|
| `WikiPageSpec` | `title`, `purpose`, `files` (5–10) | — |
| `entity_details` | 最多 25 行（PR #40 中为 `8·N`）实体行：类型 / 名称 / 签名 / 文档字符串 (150 字符) / 文件:行号 | `_format_entity_details` |
| `dep_info` | `depends_on` / `depended_by` / `external_deps`，各前 10 个 | 字符串连接 |
| `child_titles` | 子级标题（仅限父级） | — |

至关重要的是，**大纲提示词中没有源代码。** 章节、关键主张和图表计划仅根据元数据 + 实体签名 + 依赖摘要来决定。

**第 2 阶段 —— 草案 (`worker/pipeline/page_draft.py:131-263`)**

继承第 1 阶段的输入，外加：
- `outline` (章节 JSON)
- `context_chunks`：来自 `FAISSStore.multi_search` 的前 k 个块 —— `k=12` (PR #40 中为 30)，`doc_k=1`（降低纯文档权重）
- `child_contents`：已生成的子页面的结构化摘要（标题、图表、200 字符简介，硬性 2000 字符限制）
- `repo_notes`, `page_notes`

检索查询 (`page_generator.py:224-249`)：

```python
queries = [f"{spec.title} {' '.join((spec.files or [])[:5])}"]
if spec.purpose: queries.append(spec.purpose)
if entity_details: queries.append(' '.join(top5_entity_names))
```

页面标题和前 5 个文件路径被连接成一个查询。返回的块没有按文件分配的配额。

### 2.3 观测到的 Wiki（真实数据）

将 AutoWiki 对自身进行索引在 `~/Downloads/wiki_plan.json` 中产生了 26 个页面。激发本规范的选定条目如下：

| 页面 | `files`（逐字，按顺序） |
|---|---|
| 依赖图谱构建 | `[wiki_planner.py, dependency_graph.py]` |
| 内容生成引擎 | `[page_outline.py, page_generator.py, outline_anchors.py, fact_check.py, test_page_outline.py, prompt_segment.py]` |
| 质量校验与修订 | `[fact_check.py, page_outline.py, page_generator.py, test_fact_check.py, prompt_segment.py]` |
| Mermaid 图表优化 | `[mermaid.py, page_outline.py, page_generator.py, test_mermaid_sanitize.py]` |
| 前端应用架构 | `[shared/config.py, wiki_planner.py, shared/models.py, mermaid.py, test_dependency_graph.py]` |
| Wiki 渲染组件 | `[wiki_planner.py, models.py, mermaid.py, page_outline.py, page_generator.py, test_mermaid_sanitize.py]` |
| 后端接口与服务 | `[web/lib/api.ts]` |

同一导出中的跨页文件频率：
- `worker/pipeline/wiki_planner.py` → 7 个页面
- `worker/pipeline/page_generator.py` → 5 个页面
- `worker/pipeline/page_outline.py` → 4 个页面

前端页面不包含任何 `web/` 文件；后端页面包含一个前端文件。

---

## 3. 问题陈述

### P1 —— 文件列表未按相关性排序

`依赖图谱构建` 本应记录 `dependency_graph.py`，但渲染后的页面却被 `WikiPlanner` 的内容占据。页面的 `files` 列表将 `wiki_planner.py` 排在首位；检索查询（`spec.title + files[:5]`）和实体格式化程序都按此顺序消耗文件，因此更大、更密集的文件胜出。

### P2 —— 协调器文件被系统性地欠覆盖

`内容生成引擎` 应该记录 `generate_page` 和 `generate_page_batch`；渲染后的页面几乎没有提到这两者。该页面分配了六个文件，FAISS 检索没有按文件分配的配额，而协调器代码（主要是委托和小型辅助函数）在块级评分中输给了大纲/验证代码。

### P3 —— 兄弟页面内容渗透

`质量校验与修订` 包含一个名为“大纲校验与生成优化”的章节，该章节记录了 `validate_outline`，而这本应属于兄弟页面 `内容生成引擎`。原因是机械性的：`page_outline.py` 同时存在于两个页面的 `files` 列表中，两个页面都检索到了重叠的块，且大纲提示词没有告诉任何一个页面其兄弟页面涵盖了什么内容。

### P4 —— 第二阶段文件评分器仅支持 ASCII，在非英文标题上表现失能

`worker/pipeline/wiki_planner.py:733-735`：

```python
def _tokenize(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3}
```

对于 `前端应用架构 / 介绍 Next.js 应用的路由结构与交互组件库`，这会产生 `{"next"}` —— 一个单独的令牌。随后 `_score_file_for_page` 几乎完全根据依赖图中心性和实体计数对文件进行排名，完全忽略了页面的主题。结果是：`前端应用架构.files` 包含零个 `web/` 文件。同样的失败级联发生在 `Wiki 渲染组件`、`后端接口与服务` 和 `实时通信机制`。

**仅靠纯中日韩分词器无法解决此问题。** 即使提取出 `{"前端", "组件", "应用", "架构"}`，候选代码路径也是 `web/components/...`、`web/app/...` —— 令牌集共享**零个**元素。需要一个跨语言桥梁：大纲上现有的可选 `en_keywords` 字段（`wiki_planner.py:478-481`）正是那个桥梁，但它目前是可选的且很少被填充，因此预过滤器回退到了 ASCII 分词。

### 为什么 PR #40 还不够

PR #40 提升了容量（`top_k 12→30`，`max_files 200→500..800`，`max_edges 500→1500`，实体限制 `25→8·N`，`max_tokens 16k→32k`）。它解决了*截断*和*摘要完整性*问题。但它没有解决排序、所有权、范围划分或跨语言匹配问题 —— 这正是驱动 P1-P4 的四个杠杆。

---

## 4. 根本原因分析

### 4.1 代码级证据

| 缺陷 | 来源 | 机制 |
|---|---|---|
| P1 排序 | `wiki_planner.py:334-353, 504-536` | `_SELECTION_SCHEMA.selections.items.files` 是 `array<string>` —— 没有排序提示；`wiki_planner.py:355-386` 处的系统提示词和 `_build_selection_user` 处的用户提示词从未要求 LLM 按相关性排序。 |
| P1 传播 | `page_generator.py:225` | `queries = [f"{spec.title} {' '.join((spec.files or [])[:5])}"]` —— 前 N 个文件路径直接进入检索查询，因此第二阶段产生的顺序直接塑造了 RAG 上下文。 |
| P2 块稀释 | `page_generator.py:247` | `multi_search(query_vecs, k=top_k, doc_k=1)` 根据全局分数返回前 k 个，没有按文件分配的配额 —— 一个大文件可以垄断预算。 |
| P2 实体稀释 | `page_formatters.py:41` | `entities[:25]`（PR #40 中为 `8·N`）取自一个没有按文件分配配额的全局列表；协调器文件贡献的实体较少，因此被排挤出去。 |
| P2 大纲盲区 | `page_outline.py:224-254` | 大纲提示词从未见过源代码 —— 仅看到实体签名和依赖摘要，因此协调器的“我委派给 A、B、C”结构在规划时是不可见的。 |
| P3 兄弟页面不感知 | `page_outline.py:237-238` | 仅注入了 `child_titles`；兄弟页面标题和超出范围的主题则没有。 |
| P3 文件共同所有权 | 第 4.6 阶段选择设计 | 在移除孤儿强制执行后，没有任何规则阻止两个页面声明同一个文件。导出显示 `page_outline.py` 被 `内容生成引擎` 和 `质量校验与修订` 共同拥有。 |
| P4 ASCII 分词器 | `wiki_planner.py:733-735` | `_tokenize` 去掉了所有中日韩字符；`_prefilter_candidates → _score_file_for_page` 流水线对中文页面实际上变成了“标题盲”。 |
| P4 跨语言鸿沟 | `wiki_planner.py:478-481, 808-809` | `en_keywords` 是记录在案的桥梁（“当页面标题/用途为非英文时，列出 3-8 个英文目录或模块名称”）。它被 `_score_file_for_page` 采纳，但**被声明为可选**，因此大多数计划都忽略了它；评分器随后完全没有英文信号。 |

### 4.2 为什么仅进行排序或仅修改大纲是不够的

即使第二阶段按相关性对 `files` 进行了排序，P2 仍会存在，因为检索/实体阶段没有按文件分配的配额；排名靠前的文件会占据绝对优势，以至于排名中游的文件仍然不可见。P3 无法通过更聪明的第一阶段大纲来解决，因为导出显示大纲（标题 + 用途 + 父级）是正确的 —— 泄露发生在第二阶段（文件选择）和第 2 次传递（草案检索）之间。

### 4.3 为什么 `_score_file_for_page` 不能作为 LLM 排序的验证器

`_score_file_for_page` 是一种粗糙的启发式方法（文件扩展名、实体计数、依赖图入度、页面令牌重叠）。对于多语言页面，即使在 A7 落地后，它的能力也远弱于 LLM。使用它来*覆盖* LLM 排序会在启发式方法错误时产生回归（而在架构页面上，这种情况占大多数）。因此，设计将其视为一种**软健全性检查**，而不是排序键：

1. 信任 LLM 的排序。
2. 仅在出现严重违规时重新排序：排名第 0 的文件 `_score_file_for_page ≤ 0`，或者被 `_validate_selections` 标记为“不在本页”的文件。在这些情况下，将违规文件降级到列表末尾，并记录 `wiki_planner.ordering_demotion` 事件。
3. 倾向于从源头解决排序问题：要求 LLM 提供相关性分数，而不只是一个数组 —— 参见下文 A1。

---

## 5. 解决方案设计

### 5.1 设计原则

1. **重用，不要重新发明 —— 并通过重命名来反映共享角色。** 确定性索引构建器（`worker/pipeline/fast_report_index.py`）、分词器（`worker/fast_report/search._tokenize`）、图扩展 + 切片原语（`worker/fast_report/search.py` 的其余部分）以及源切片提取器（`worker/fast_report/slices.py`）都是为快速报告编写的，但它们是领域无关的。Wiki 路径将消耗它们，因此必须将它们重命名并重新放置在 `worker/pipeline/` 下（参见 A7 / A11 / A12 / A13）。快速报告代码保留一个版本的精简重新导出垫片。没有新的外部依赖。
2. **按排名加权的公平，而非绝对公平。** 一旦文件按相关性排序，块和实体预算将遵循分级分配：每个文件有一个小的底线（这样没有文件会不可见），外加按排名加权的奖金，使排在首位的文件获得最大份额。这避免了最重要文件匮乏的情况，同时也防止了排名第二重要的文件被完全挤出。
3. **明确的范围边界。** 兄弟页面标题和超出范围的主题是一等提示词输入。
4. **通过 `en_keywords` 进行跨语言匹配，而非分词器技巧。** 中日韩分词器对于处理中日韩注释/路径/标识符是必要的，但解决前端崩溃的真正方法是：每当 `title` 为非英文时，强制要求 `en_keywords`，并将其作为 `_score_file_for_page` 中的附加信号（和验证门）。
5. **响亮地失败。** 新行为不设遗留回退。如果某个阶段耗尽了重试次数，任务将以结构化错误失败；我们不会默默地产生降级的结果。
6. **Skill 就绪性作为约束，而非交付成果。** A 层和 B 层必须使 Wiki 路径独立于 FAISS / 嵌入（在 B 层之后），并在稳定的模块路径下提取可重用工具，以便未来的 Skill 项目可以直接引用它们。

### 5.2 A 层 —— 规划器与提示词补丁

| ID | 文件 | 变更 |
|---|---|---|
| **A1** | `worker/pipeline/wiki_planner.py` | 扩展 `_SELECTION_SCHEMA`，使每个页面的 `files` 变为 `array<{path: string, relevance: number /* 1-10 */}>`；LLM 发出显式的相关性分数。更新 `_SYSTEM` and `_build_selection_user`，要求按最典型优先排序并进行相关性评分。`_validate_selections` 检查沿数组的分数是否非递增，且位置 0 处没有分数 < 3 的文件；如果违规，向 LLM 发出一个反馈重试（增加一个重试预算），然后回退到降级并记录日志。数据类 `WikiPageSpec.files` 保持为 `list[str]`（在边界处从字典中解包），保持下游代码不变。 |
| **A2** | `worker/pipeline/page_outline.py:224-254`; `page_generator.py:266-274` | 使用 `sibling_titles: list[str] \| None` 和 `out_of_scope_topics: list[str] \| None` 扩展 `generate_page_outline`。将它们作为 `Sibling pages (DO NOT cover their topics; reference by name only): ...` 和 `Out-of-scope (covered elsewhere): ...` 注入可缓存前缀。`generate_page_batch` 从 `WikiPlan`（相同 `parent`）计算兄弟页面，并从兄弟页面的 `purpose` 首句派生 `out_of_scope_topics`。 |
| **A3** | `worker/pipeline/page_draft.py:27` (`DRAFT_SYSTEM`) | 附加“范围纪律”规则：保持在分配的文件范围内；如果某个主题由提示词中列出的兄弟页面拥有，则给予其 ≤ 1 句话，并按标题引用兄弟页面。 |
| **A4** | `worker/pipeline/page_generator.py:224-249` | 用五个查询替换单个检索查询：`spec.title`、`spec.purpose`、`" ".join(en_keywords)`、前 5 个实体名称、文件词干。在 `multi_search` 之后，运行具有**按排名加权配额**的 `_balance_chunks(chunks, files=spec.files, k=top_k)`：`quota_i = max(floor, round(k · w_i / Σw))`，其中 `w_i = 1 / (rank_i + 1)`。使用默认的 `k=30, floor=2, len(files)=5`，排名第 1 的文件获得约 12 个块，第 2 个获得约 7 个，第 3 个获得约 5 个，第 4 个获得约 3 个，第 5 个获得约 3 个 —— 是分级的，而非平坦的。排名 ≥ 6 的文件仅获得底线份额。 |
| **A5** | `worker/pipeline/page_formatters.py:41` | 重构 `_format_entity_details` 以接受 `files: list[str]` 并使用与 A4 相同的按排名加权的分级配额。排名最高的文件保留最大份额；排名较低的文件保留较小的可见性底线。 |
| **A6** | `worker/pipeline/page_outline.py:_build_outline_prompt` | 新的可选参数 `signature_slices: dict[file, list[str]]`。对于每个 `spec.file`，获取 1-2 个重要性最高的实体，并将 `Signature slices` 块（文件:行范围 + 实体正文的前 4 行）发出到可缓存前缀中。切片来自 `FileAnalysis` 外加克隆根读取。 |
| **A7** | 新建 `worker/utils/tokenize.py`; `worker/fast_report/search.py`; `worker/pipeline/wiki_planner.py` | 将目前位于 `worker/fast_report/search._tokenize` 的中日韩感知分词器**提取**到 `worker/utils/tokenize.py` 中，作为公共的 `tokenize_text(text: str) -> set[str]`。更新 `worker/fast_report/search.py` 以重新导出相同的函数（快速报告的行为不变）。将 `wiki_planner._tokenize` 和 `_score_file_for_page` 处的重复项替换为共享助手。审核其他调用点（`_best_matching_page`、`_directory_cluster_assign`）并进行迁移。添加涵盖中日韩运行、中日韩+ASCII 混合、驼峰式命名和路径段的单元测试。 |
| **A8** | `worker/pipeline/wiki_planner.py:_select_files`, `_build_outline_prompt` | **当 `title` 或 `purpose` 包含中日韩字符时，使 `en_keywords` 成为强制性要求。** 第一阶段提示词收紧了语言：“如果页面标题或用途包含非拉丁字符，你必须提供 3-8 个取自列表中的目录名称、模块名称或文件基名的英文关键词。” `_validate_outline_structure` 强制执行此操作，并在违规时触发第一阶段重试。`_score_file_for_page` 对 `en_keywords` ↔ 路径段匹配的权重设为每次重叠 +4（而一般令牌重叠为 +0.5），使其成为跨语言页面的主要信号。 |
| **A9** | `worker/pipeline/wiki_planner.py:_select_files` | 验证后，运行 `_enforce_ownership(selections, outline, dep_graph, file_infos)`：对于任何被 ≥ 2 个页面拥有的文件，使用 `_score_file_for_page` 为每个所有者评分，并仅在兄弟页面（共享 `parent` 的页面）中保留评分最高的所有者；允许最多 2 个非兄弟页面所有者（架构枢纽）；否则降级。将总分配限制在 `1.5 × len(all_repo_files)`。 |
| **A10** | `worker/pipeline/wiki_planner.py:_select_files`, `_validate_selections` | 将静态的 5-8 个/硬性上限 10 个规则替换为从预过滤评分分布计算出的 `n_target = clamp(2, ceil(median_score / score_threshold), 8)`。狭窄主题获得 2-3 个文件；宽泛主题保留 5-8 个。硬性上限保持为 10。 |
| **A11** | `worker/pipeline/fast_report_index.py` → **`worker/pipeline/retrieval/repo_index.py`**; 工件 `~/.autowiki/repos/{hash}/ast/fast_report_index.json` → **`repo_index.json`** | 重命名模块和工件以反映现在的共享角色（被快速报告**以及** Wiki 生成消耗）。根据 A15，最终模块位置为 `worker/pipeline/retrieval/repo_index.py`。公共 API 重命名：`build_fast_report_index()` → `build_repo_index()`；`INDEX_VERSION` → `REPO_INDEX_VERSION`；`worker/fast_report/jobs.py` 中的加载器/验证器助手（`_load_fast_report_index`、`_validate_fast_report_index_version`、`_FastReportIndexOutdatedError`）重命名以去掉 `fast_report_` 前缀，并移至新的 `worker/pipeline/retrieval/repo_index_io.py`，以便 Wiki 和快速报告通过相同的代码路径加载相同的工件。旧的 `worker/pipeline/fast_report_index.py` 保留一个版本的弃用垫片，它从新模块重新导出并发出 `DeprecationWarning`。**磁盘迁移**：加载器如果发现磁盘上有旧名称，会自动重命名 `fast_report_index.json` → `repo_index.json`；如果两者都不存在，则强制通过第 2/3 阶段重建。 |
| **A12** | 新建 `worker/pipeline/retrieval/repo_search.py`; `worker/fast_report/search.py` | 将领域无关的检索原语从 `worker/fast_report/search.py` **提取**到 `worker/pipeline/retrieval/repo_search.py`（根据 A15 确定最终位置）：`score_file_for_query`（原为 `_score_file_multi_slice`）、`expand_candidate_paths`、`build_slice_candidates`、`apply_token_budget`、`neighbors_for_graph`，外加支持性数据类 `RankedFile`、`ScoredEntity`、`SliceCandidate`。将它们提升为公共符号。`worker/fast_report/search.py` **仅**保留快速报告特定的编排（`retrieve_code_evidence` 和问题类型管道），并从新的共享模块导入原语。B 层的 `KeywordIndex` (B1) 和章节起草器 (B2) 直接消耗 `worker/pipeline/retrieval/repo_search.py`。目前内联在 `worker/fast_report/planning.expansion_graph_for(question_type)` 下的图遍历配置文件注册表也被提升到 `repo_search.py` 中作为按配置文件键控的函数，这样 Wiki 和快速报告就可以在没有导入循环的情况下注册各自的配置文件（`"wiki_page"`、`"how_does_it_work"` 等）。 |
| **A13** | `worker/fast_report/slices.py` → **`worker/pipeline/retrieval/code_slices.py`** | 将源切片提取器（`extract_source_slice` 及其助手）移动到 `worker/pipeline/retrieval/` 下（根据 A15 确定最终位置）。通用的磁盘源窗口提取并非快速报告特定的。旧路径保留一个版本的重新导出垫片。B 层章节起草器直接消耗 `worker.pipeline.retrieval.code_slices.extract_source_slice`。 |
| **A14** | `worker/index/full.py:277`, `worker/index/refresh.py:400` | **停止持久化 `ast/file_analysis_summary.txt`。** 它是已捕获在 `repo_index.json` 中的数据（每个文件的类/函数计数、导入、首个文档字符串）的有损文本视图，从未被任何代码路径读回（已通过 `grep -rn "file_analysis_summary"` 验证），并且仅作为与发送给第一阶段的实际提示词脱节的人工调试快照存在。`wiki_planner._build_outline_prompt` 内部的内存中 `FileAnalysis.to_llm_summary()` 调用保持不变 —— 第一阶段仍然接收相同的字符串内容。对于人工调试用例，增加一个由 `AUTOWIKI_DEBUG_DUMP_PROMPTS=1` 门控的可选转储，将*实际的*第一阶段提示词（而非过时的预提示词摘要）连同任何其他提示词调试转储一起写入 `~/.autowiki/repos/{hash}/logs/phase1_prompt.txt`。在 A14 之后，`~/.autowiki/repos/{hash}/ast/` 正好包含两个文件：`repo_index.json` 和 `wiki_plan.json`。 |
| **A15** | `worker/pipeline/**` | **按阶段将 `worker/pipeline/` 重构为子包。** 若不如此，在 B 层之后，该目录将持有约 20 个混合了分析、索引、检索、规划和页面生成的同级文件。最终布局如下：<br><br>`worker/pipeline/`<br>&nbsp;&nbsp;`__init__.py` —— 重新导出公共 API 以实现向后兼容<br>&nbsp;&nbsp;`language.py`, `pipeline_logging.py` —— 跨领域<br>&nbsp;&nbsp;`ingestion.py`, `ast_analysis.py`, `dependency_graph.py` —— 第 1-3 阶段，保持在顶层<br>&nbsp;&nbsp;`retrieval/` —— `repo_index.py` (A11), `repo_index_io.py` (A11), `keyword_index.py` (B1), `repo_search.py` (A12), `code_slices.py` (A13)<br>&nbsp;&nbsp;`planner/` — `wiki_planner.py`, `outline_anchors.py`, `user_steering.py`<br>&nbsp;&nbsp;`page/` — `generator.py`（原为 `page_generator.py`）, `outline.py`（原为 `page_outline.py`）, `section_drafter.py`（B2 新增）, `formatters.py`（原为 `page_formatters.py`）, `fact_check.py`, `diagram_post_processor.py`<br><br>`page/` 内部的文件去掉冗余的 `page_` 前缀；`planner/` 内部的文件保留其 `wiki_`/全名，因为去掉它们会与类名（`Planner`、`OutlineAnchors` 等）和现有的测试名称冲突。**不与 `worker/index/` 冲突**（现有的作业编排模块）：检索位于 `worker/pipeline/retrieval/` 下，而非 `worker/pipeline/index/`。**向后兼容**：`worker/pipeline/__init__.py` 重新导出 `WikiPlanner`、`WikiPageSpec`、`WikiPlan`、`generate_page`、`generate_page_batch`、`compute_generation_order`、`PageResult`、`validate_outline`、`PageOutline`、`SectionPlan`、`DiagramPlan`、`FileAnalysis`、`DependencyGraph`。每个旧路径保留一个版本的弃用垫片文件（例如 `worker/pipeline/page_generator.py` 从 `worker/pipeline/page/generator.py` 重新导出并发出 `DeprecationWarning`）。所有测试导入在同一个 PR 中更新；保留现有的测试名称。 |

**未来的命名规则：** `worker/fast_report/` 下的任何内容都必须是快速报告特定的（编排、提示词、响应形状）。跨领域基础设施（索引、检索原语、切片、分词）位于 `worker/pipeline/` 或 `worker/utils/` 下。任何将来起源于 `worker/fast_report/` 但获得了 Wiki / 深度研究消费者的模块，必须在添加第二个消费者的同一个 PR 中搬迁。

**未来的子包规则：** 添加在 `worker/pipeline/` 下的新文件必须放置在匹配的子包中（`retrieval/` 用于索引和检索，`planner/` 用于第一/二阶段规划器关注点，`page/` 用于每页生成阶段）；只有第 1-3 阶段（`ingestion.py`、`ast_analysis.py`、`dependency_graph.py`）和跨领域助手（`language.py`、`pipeline_logging.py`）位于顶层。审查者应拒绝在顶层添加没有明确跨领域角色的新同级模块的 PR。

**对分析中的导出的综合效果：**

- `依赖图谱构建` → `[dependency_graph.py, wiki_planner.py]`（A1 揭示相关性，A9 降低 wiki_planner 共同所有权）
- `内容生成引擎` → `[page_generator.py, page_outline.py]`（A9 移除 `fact_check.py` / `test_page_outline.py` / `prompt_segment.py`；A1 提升协调器；A4/A5 赋予其专用的块和实体配额）
- `质量校验与修订` → `[fact_check.py, test_fact_check.py]`（A9 移除 `page_outline.py` 共同所有权）
- `Mermaid 图表优化` → `[mermaid.py, diagram_post_processor.py, test_mermaid_sanitize.py]`
- `前端应用架构` → `web/app/` 和 `web/components/` 文件（A7 + A8：`en_keywords = ["web", "app", "components", "next"]` 提升与这些段匹配的路径）
- `后端接口与服务` → `api/main.py`、`api/routers/*.py`、`api/queue.py`（A7 + A8：`en_keywords = ["api", "routers", "fastapi"]`）

### 5.3 B 层 —— 章节级起草 + BM25 检索；FAISS 移除

#### B1. 确定性关键词索引

添加 `worker/pipeline/retrieval/keyword_index.py`：

```python
@dataclass
class KeywordIndex:
    chunks: list[Chunk]                    # same Chunk model as FAISSStore
    bm25: BM25Okapi                        # rank_bm25 — pure Python, no native deps
    file_to_chunks: dict[str, list[int]]
    token_idf: dict[str, float]            # built via worker.utils.tokenize.tokenize_text

    @classmethod
    def build(cls, chunks, *, repo_index): ...

    def search(
        self,
        queries: list[str],
        *,
        k: int,
        files: list[str] | None = None,
        per_file_quota: int = 2,
    ) -> list[Chunk]: ...
```

为什么选择 BM25 (`rank_bm25`) 而**不是** LlamaIndex：AutoWiki 已经拥有自己的分块、规划器架构和检索表面。`rank_bm25` 是纯 Python 编写，没有原生依赖，可插入现有的 `Chunk` 数据类，并重用 A7 中提取的共享分词器。LlamaIndex 会在分块和检索之上强加一个我们不需要的不同抽象。

`KeywordIndex` 消耗重命名的 `repo_index.json` (A11) 以获取令牌 ID 和跨文件元数据，并使用提取到 `worker/pipeline/retrieval/repo_search.py` (A12) 的原语进行图扩展和切片构建。没有引入新的索引文件 —— 我们扩大了现有工件（现在使用共享名称）的消费者。

#### B2. 大纲 → 骨架 → 章节起草 → 缝合

用以下步骤替换单次起草的第 2 阶段：

```
Pass 1   Outline                        — fast_llm, unchanged shape
Pass 2a  Skeleton (NEW)                 — fast_llm
Pass 2b  Section drafting (NEW)         — main_llm, parallel per section
Pass 2c  Stitch (NEW)                   — fast_llm
Pass 3   Fact-check                     — unchanged
Pass 4   Targeted revision              — unchanged
```

**大纲 vs. 骨架 —— 明确区别：**

| | 大纲（第 1 阶段，现有） | 骨架（第 2a 阶段，新增） |
|---|---|---|
| 输出类型 | 结构化 JSON：`sections[]`（标题、种类、重点、图表计划、源文件）、`key_claims[]`、`out_of_scope_claims[]` | Markdown 文本：H1、H2 标题，每个标题下的单行章节用途，无正文文本 |
| 用途 | 决定*存在哪些主题以及顺序如何* | 决定*页面的渲染形状* —— 标题措辞、叙事流的顺序、图表放置的位置 |
| 模型 | `fast_llm` | `fast_llm` |
| 长度 | 约 200 行 JSON | 约 30 行 Markdown |
| 为什么要拆分 | 大纲回答规划者的问题（“本页应该包含什么”）；骨架回答作者的问题（“本页应该如何开头、过渡、结尾”）。拆分它们让章节起草器接收一个稳定的 Markdown 框架来填充，而不是每次都从 JSON 重新推导标题措辞。 |

`section.diagram.source_files` 是由 **LLM 在第 1 阶段产生**的，作为 `_OUTLINE_SCHEMA.sections[i].diagram.source_files` 的一部分，被 `validate_outline` 限制为 `spec.files` 的子集。LLM 被明确告知：“对于每个需要图表的章节，列出 1-3 个该图表*最相关*的源文件。” 这与现有架构相比没有变化；B 层只是在第 2b 阶段将其用于检索范围划分。

**第 2b 阶段检索：** 对于每个 `SectionPlan`，

```
queries  = [section.heading, section.focus, top entity names]
scope    = spec.files ∩ (section.diagram.source_files ∪ heuristic-derived)
chunks   = KeywordIndex.search(queries, k=8, files=scope, per_file_quota=2)
```

这正是从根源上解决 P2 的方法：协调器章节无论大纲文件有多密集，都会检索到协调器块，因为章节范围显式地与 `diagram.source_files` 相交。

**没有遗留回退。** 如果第 2a、2b 或 2c 阶段耗尽了重试次数，任务将以 `WikiGenerationError` 失败，并记录已在 `pipeline_logging` 中的结构化重试日志。没有 `legacy_full_draft` 逃生舱。

#### B3. 大纲架构中的 `out_of_scope_claims`

使用 `out_of_scope_claims: array<string>` 扩展 `_OUTLINE_SCHEMA`。事实核查提示词接收此列表；如果草稿包含与任何超出范围短语匹配的主张，事实核查将返回 `verdict="fail"`，第 4 阶段在 LLM 修订前将删掉违规句子。

#### B4. 彻底移除第 4 阶段和嵌入提供者

B 层是**一次彻底的切割**，而非带标志位的迁移。没有 `AUTOWIKI_RETRIEVAL=faiss` 逃生舱。此 PR 中的具体删除内容包括：

| 涉及项 | 操作 |
|---|---|
| `第 4 阶段` (`worker/pipeline/rag_indexer.py`) | **已删除。** 索引期间不再构建 FAISS。不再生成 `~/.autowiki/repos/{hash}/faiss.index` 和 `faiss.meta.pkl`。6 阶段流水线变为 5 阶段（摄取 → AST → 依赖图 → Wiki 规划器 → 页面生成器）。磁盘上现有的工件可以保留，也可以在下次索引时 `rm -rf`。 |
| `worker/pipeline/rag_indexer.py` | **已删除。** 索引路径中不再有消费者。 |
| `worker/index/artifacts.py` | FAISS 索引查找 / 加载器已移除。 |
| `EmbeddingProvider` 参数 | 从 `generate_page`、`generate_page_batch`、章节起草器以及 `worker/jobs.py` 中的每个作业入口点中移除。 |
| 索引中的 `make_embedding_provider` 调用点 | 已移除。索引期间不再构造提供者。 |
| `LLMConfig` / `Config` 嵌入字段 | 标记为弃用但保留（一个版本），以便现有的 `autowiki.yml` 文件不会报错；记录为忽略。 |
| `worker/embedding/*` | **保留在磁盘上**（以便深度研究稍后的迁移可以增量进行），但 Wiki 索引路径中不再有导入。导入仅保留在 `worker/research/*` 内部，该部分已被禁用 —— 参见 B5。 |
| 聊天 (Chat) | 已经不依赖 FAISS（通过 grep 验证 —— `worker/chat.py` 或 `api/` 中没有 `FAISSStore` / `EmbeddingProvider` 导入）。无需更改。 |

在 B 层之后，**索引流水线对 FAISS / 嵌入的依赖为零**。Wiki 路径中的检索原语是 `KeywordIndex` (B1) + `worker/pipeline/retrieval/repo_index.py` 工件 `repo_index.json` (A11) + `worker/pipeline/retrieval/repo_search.py` (A12) + `worker/pipeline/retrieval/code_slices.py` (A13) + `worker/utils/tokenize.py` (A7) —— 全部位于新的 `retrieval/` 子包下 (A15)。这就是 Skill 就绪性契约 —— 索引流水线可以在没有配置嵌入 API 密钥的情况下在 Skill 沙箱中运行。

#### B5. 深度研究 —— 暂时禁用

深度研究是唯一仍依赖于 `FAISSStore` + `EmbeddingProvider` 的功能（`worker/research/service.py:27,29,130-136,217-246`；`worker/research/jobs.py:15,33-43,80-82,122`）。由于第 4 阶段在 B4 中被删除，深度研究没有索引可供查询。B 层明确禁用了该功能，而不是让它处于半损坏状态：

| 暴露面 | B 层之后的行为 |
|---|---|
| CLI `autowiki research` | 以 `Deep Research is temporarily unavailable while migrating to keyword retrieval (see issue #TBD).` 退出，并返回非零状态。 |
| API `POST /api/repos/{id}/research` | 返回 HTTP 503，`detail` 中包含相同消息。 |
| API `GET /api/repos/{id}/research/{job_id}` | 对任何新请求返回 HTTP 410 (gone)；SQLite 中已有的报告仍然可读。 |
| WebSocket `/ws/repos/{id}/research/{job_id}` | 立即关闭，代码为 1011 + 原因 "feature disabled"。 |
| 前端 "Research" 入口点 | 通过 `/api/repos/{id}` 提供的配置标志隐藏 (`features.deep_research = false`)。 |
| `worker/research/jobs.py` | 任务函数已注册，但在进入时抛出 `FeatureDisabledError`；不尝试加载 FAISS。 |
| 测试 | 现有的研究测试通过 `pytest.mark.skipif(...)` 跳过；一个新测试断言 503 / CLI 退出代码。 |

后续项目将把深度研究的每步检索迁移到 `KeywordIndex` + 1 跳图扩展（快速报告使用的相同模式）。该工作**超出了本规范的范围。** 在 B 层合并之前会提交一个跟踪 issue。

文档更新：
- `CLAUDE.md` API 表面部分：将研究端点标记为 `(disabled — see issue #TBD)`。
- `docs/cli.md` / `docs/cli-zh.md`：同上。
- `README.md`：功能列表提到深度研究为“暂时禁用，正在迁移到关键词检索”。

### 5.4 Skill 就绪性准备（树内没有 Skill 包）

Skill 提取将是一个独立的项目。本规范仅要求 A 层和 B 层使 Wiki 路径处于一种无需进一步重构即可构建 Skill 的状态：

1. 分词器提取到 `worker/utils/tokenize.py` (A7)。
2. 索引构建器 + 工件重命名为 `worker/pipeline/retrieval/repo_index.py` / `repo_index.json` (A11) —— 领域中性名称标志着共享所有权。
3. 检索原语提取到 `worker/pipeline/retrieval/repo_search.py` (A12) —— 图扩展、评分、切片构建、令牌预算强制执行。
4. 源切片提取器移动到 `worker/pipeline/retrieval/code_slices.py` (A13)。
5. 检索采用 `KeywordIndex` (B1) —— 纯 Python (`rank_bm25`)，无原生依赖，无嵌入提供者。
6. 章节起草器 (B2) 仅接受 `(page_spec, repo_index, clone_root, llm, fast_llm)` 作为输入 —— 无 FAISS、无嵌入、无数据库句柄。
7. 在 B4 之后，**索引流水线中不再保留 FAISS / 嵌入代码路径** —— 整个 Wiki 生成流程在未配置嵌入提供者的情况下运行。
8. 所有重试 / 响亮失败行为都集中在 `pipeline_logging` 中；Wiki 路径中不再保留沉默的回退。

当 Skill 项目启动时，它会逐字引用第 1-4 项外加一个精简的 CLI 垫片。不需要进一步的流水线重构。未来的深度研究迁移将消耗相同的共享模块。

---

## 6. 实施计划

### 6.1 阶段划分与退出标准

| 阶段 | PR | 代码行数 | 退出标准 |
|---|---|---|---|
| **A** | `feat/wiki-quality-layer-a` | 约 600 | 所有 A1–A15 补丁已合并（A1–A10 = 行为变更；A11–A14 = 重命名 / 提取 / 工件清理；A15 = 带有弃用垫片的目录重构）；`tests/worker/test_wiki_planner.py`（相关性架构、所有权、中日韩分词器提取、强制 `en_keywords`、自适应上限）、`tests/worker/test_page_generator.py`（按排名加权的块配额）、`tests/worker/test_repo_index.py`（工件重命名 + 磁盘迁移）、`tests/worker/test_repo_search.py`（提取的原语）、`tests/worker/test_index_artifacts.py`（不再写入 `file_analysis_summary.txt`；`ast/` 正好包含 `repo_index.json` + `wiki_plan.json`）、`tests/worker/test_pipeline_layout.py`（子包边界；向后兼容的重新导出；弃用垫片警告）中的新测试通过；现有的快速报告测试通过弃用垫片保持不变且通过；对 AutoWiki 进行冒烟索引，在 §5.2 中针对所有八个缺陷页面产生符合预期的文件列表。 |
| **B1** | `feat/keyword-index` | 约 300 | 在 100 个问题的固定装置上（在删除 FAISS 之前的此 PR 期间运行），`KeywordIndex.search` 与 FAISS 的前 k 个召回率匹配度在 ±10% 以内；`rank_bm25` 已添加到 `pyproject.toml`。 |
| **B2** | `feat/section-drafting-and-stage4-removal` | 约 400 | 章节级起草已接入；**第 4 阶段已删除；`worker/pipeline/rag_indexer.py` 已删除；`EmbeddingProvider` 从每个索引调用点移除**；深度研究按 B5 禁用，并伴有 HTTP 503 / CLI 退出 / 前端隐藏；在未配置嵌入 API 密钥的情况下运行完整的自索引；聊天功能回归测试正常。 |

PR #40 先落地；每一层都假设其预算已到位。B 层是两个 PR，但作为一个单一迭代执行 —— B1 的召回率等效检查是 B2 删除 FAISS 之前的门槛。

### 6.2 测试计划

每一层都附带针对 `tests/fixtures/simple-repo/` 的确定性测试，以及断言 §5.2 中文件列表预期的自索引回归测试。

- **A 层单元测试：**
  - `test_select_files_emits_relevance_scores` —— 第二阶段结果包含每个文件的 `relevance ∈ [1,10]`，且非递增。
  - `test_validate_selections_demotes_low_relevance_in_position_zero`。
  - `test_enforce_ownership_demotes_sibling_share` —— `page_outline.py` 不能分配给两个兄弟页面。
  - `test_shared_tokenizer_handles_cjk_and_camel` —— 提取的 `tokenize_text` 涵盖中日韩运行、驼峰式命名、路径。
  - `test_phase1_requires_en_keywords_for_cjk_titles` —— 第一阶段拒绝中日韩标题页面缺少 `en_keywords` 的大纲。
  - `test_score_file_for_page_uses_en_keywords` —— 与 `en_keywords` 路径段重叠的权重提升是 `前端应用架构` 的主要信号。
  - `test_balance_chunks_rank_weighted` —— 排在首位的文件获得最大份额；每个文件至少获得底线份额。
  - `test_outline_prompt_includes_siblings`。
  - `test_draft_system_prompt_scope_rule`。
  - `test_repo_index_migration_renames_old_artifact` —— 加载器在首次读取时自动重命名 `fast_report_index.json` → `repo_index.json`。
  - `test_repo_index_deprecation_shim_emits_warning` —— 导入 `worker.pipeline.fast_report_index.build_fast_report_index` 仍可工作但会警告。
  - `test_repo_search_primitives_callable_from_pipeline` —— Wiki 和快速报告都从 `worker/pipeline/retrieval/repo_search.py` 导入，没有导入循环。
  - `test_pipeline_top_level_only_has_stages_and_helpers` —— `worker/pipeline/` 的顶层仅列出 `ingestion.py`、`ast_analysis.py`、`dependency_graph.py`、`language.py`、`pipeline_logging.py`、`__init__.py`，外加三个子包目录。
  - `test_pipeline_back_compat_reexports` —— `from worker.pipeline import WikiPlanner, generate_page, FileAnalysis, DependencyGraph` 等全部可以解析。
  - `test_pipeline_old_paths_emit_deprecation_warning` —— `import worker.pipeline.page_generator` 可工作但会发出 `DeprecationWarning`。

- **B 层集成测试：**
  - `test_keyword_index_recall_parity` —— 在固定问题集上 BM25 与 FAISS 的召回率等效性（在删除 FAISS 之前的 B1 期间运行）。
  - `test_section_drafter_independent_retrieval` —— 即使大纲文件占据页面主导地位，协调器章节也能接收到协调器块。
  - `test_out_of_scope_claims_trigger_factcheck_fail`。
  - `test_indexing_runs_without_embedding_provider` —— 在 `Config` 中未设置嵌入 API 密钥的情况下，完整的自索引顺利完成。
  - `test_stage4_artifacts_not_produced` —— 新索引运行后，不存在 `~/.autowiki/repos/{hash}/faiss.index` 和 `faiss.meta.pkl`。
  - `test_research_endpoint_returns_503` —— `POST /api/repos/{id}/research` 返回 503 并带有预期消息；CLI `autowiki research` 以非零值退出。

- **自索引回归：** 一个 CI 作业针对 AutoWiki 仓库运行 `autowiki index . --reuse-index=false` Alexander，并断言 §5.2 中列出的八个页面包含预期的主要文件作为其第一个 `files[0]`。

### 6.3 回滚与功能标志

- 针对 A9 的 `AUTOWIKI_PLANNER_OWNERSHIP=enforce|advise|off` —— 一个版本内默认为 `advise`，随后改为 `enforce`。

没有 `AUTOWIKI_RETRIEVAL` 标志，也没有草案模式标志。B 层是彻底的切割：FAISS / 嵌入代码被删除，而不是被门控。B 层回滚意味着撤销该 PR。本规范的早期草案提出了 `legacy_full_draft` 回退和 FAISS 逃生舱；根据“响亮地失败”这一设计原则，这两者都已被移除。

### 6.4 风险与缓解措施

| 风险 | 缓解措施 |
|---|---|
| LLM 发出的 `relevance` 分数全部集中在高端 | A1 要求非递增顺序，而非特定的分布；即使分数一致偏高，仍然能产生有效的排名。 |
| BM25 在 Wiki 页面章节起草方面的表现逊于 FAISS | B1 先发布，并在固定装置集上设置了针对 FAISS 的召回率等效门槛；只有门槛通过，B2 才会删除 FAISS。 |
| 迁移窗口期间深度研究中断 | B5 明确禁用暴露面（HTTP 503 + CLI 非零 + 前端隐藏），并在跟踪 issue 中承诺后续迁移到 `KeywordIndex`。用户已接受此权衡。 |
| 现有的 `~/.autowiki/repos/{hash}/faiss.*` 文件变成孤儿 | 它们在磁盘上是无害的；文档中注明它们可以被删除。不需要磁盘迁移。 |
| `autowiki.yml` 文件引用了嵌入提供者 | `Config` 中的嵌入字段弃用一个版本并发出警告；索引路径仅忽略它们。 |
| 章节起草使 LLM 调用次数激增 | 章节生成阶段在每个章节使用 `fast_llm`；`AUTOWIKI_PAGE_CONCURRENCY` 已经限制了并行度；每个章节的上下文要小得多，因此实际耗时与单次生成相当。 |
| 所有权强制执行导致“枢纽”文件（如 `models.py`）匮乏 | A9 明确允许最多 2 个非兄弟页面所有者，并通过现有的 `_compute_hub_modules` 豁免入度排名前 10% 的文件。 |
| 强制性的 `en_keywords` 导致第一阶段拒绝更多大纲 | A8 依附于现有的第一阶段重试预算（`max_retries=3`）；失败模式是额外的重试，而非硬性失败。 |
| 分词器提取 (A7) 波及到 `_directory_cluster_assign` 并更改了现有分配 | 测试将行为锚定在 AutoWiki 自索引上；与 A8/A9 一起推出，因此行为变更是故意的。 |

### 6.5 遥测

使用现有的 `pipeline_logging.log_validation_retry` / `log_final_failure` 通道。在以下位置添加结构化事件：

- `wiki_planner.ordering_demotion` —— 页面、降级的文件、原始位置、分数。
- `wiki_planner.ownership_demotion` —— 文件、降级的页面、主要页面、分数差。
- `wiki_planner.en_keywords_required` —— 页面、重试尝试。
- `page_generator.balance_chunks` —— 页面、文件、按文件分配的数量、剩余数量。
- `page_section_drafter.section_factcheck` —— 页面、章节、结论、问题计数。

---

## 7. 验收标准

当满足以下条件时，A 层发布（在全新的 AutoWiki 自索引上）：

1. §5.2 中列举的八个页面携带了那里预测的文件列表，且 `files[0]` 与预测的主要文件匹配。
2. `质量校验与修订.md` 不包含主要主题为 `validate_outline` 或 `PageOutline` 的章节。
3. `内容生成引擎.md` 包含 `generate_page`、`generate_page_batch` 或 `compute_generation_order` 符号中的至少一个。
4. `前端应用架构.md` 包含至少三个对 `web/` 下文件的引用。
5. 除 `dep_graph` 入度排名前 10% 的文件外，没有源文件被分配给两个以上的页面。
6. `wiki_plan.json` 中每个中日韩标题的页面都具有非空的 `en_keywords` 字段。

此外，当满足以下条件时，B 层发布：

7. 在 AutoWiki 自索引外加三个外部固定装置仓库上衡量，章节级事实核查通过率 ≥ 之前的全页事实核查通过率。
8. 在 `Config` / 环境变量 / `autowiki.yml` 中**完全没有配置嵌入 API 密钥**的情况下，完整的自索引顺利完成。
9. 全新索引运行不再产生 `~/.autowiki/repos/{hash}/faiss.index` 和 `faiss.meta.pkl`。
10. 树中不再存在 `worker/pipeline/rag_indexer.py`；`grep -r "FAISSStore\|EmbeddingProvider" worker/` 仅在 `worker/embedding/` 和 `worker/research/`（禁用的、等待迁移的代码）内部返回命中。
11. 深度研究暴露面（CLI、REST、WebSocket、前端入口点）全部返回 B5 中指定的禁用响应。
12. **命名卫生**（A 层子集，但作为发布门槛检查列于此处）：拥有多个消费者的任何模块/工件/公共符号上都不出现 `fast_report` 前缀；磁盘上的工件名称为 `repo_index.json`；存在 `worker/pipeline/retrieval/repo_index.py`、`worker/pipeline/retrieval/repo_search.py`、`worker/pipeline/retrieval/code_slices.py` 和 `worker/utils/tokenize.py`；`worker/pipeline/fast_report_index.py`、`worker/fast_report/slices.py` 弃用垫片在导入时发出 `DeprecationWarning`。
13. **工件极简主义**：在全新的完整索引之后，`~/.autowiki/repos/{hash}/ast/` 正好包含 `repo_index.json` 和 `wiki_plan.json` —— 没有 `file_analysis_summary.txt`，没有 `faiss.*`。
14. **目录卫生**：`ls worker/pipeline/` 正好列出 `__init__.py`、`language.py`、`pipeline_logging.py`、`ingestion.py`、`ast_analysis.py`、`dependency_graph.py`，以及三个子包目录 `retrieval/`、`planner/`、`page/`。旧的同级文件仅作为弃用垫片幸存。

---

## 8. 未决问题

- **相关性量表。** A1 的 `relevance` 应该是 `int 1-10` 还是 `float 0-1`？`int 1-10` 在 `wiki_plan.json` 中更易读，且符合人类的评分习惯；`float 0-1` 符合典型的 LLM 评分规范。建议：为了提高可读性，使用 `int 1-10`。
- **所有权介入时机。** A9 强制执行在 `_validate_selections` 之后运行；它应该获得自己的重试预算，还是依附于现有的预算？建议：依附现有预算，仅当所有权降级导致页面完全变空时增加一轮重试。
- **骨架所有权。** 第 2a 阶段（骨架）位于大纲和每章节起草之间。当第 3 阶段事实核查在标题措辞上失败时，是否应该重新生成骨架？还是仅当章节级内容失败时才重新生成？建议：仅章节级；标题措辞很少是问题所在，重新运行骨架会使已经起草好的兄弟页面失效。
- **深度研究后续时机。** 迁移到 `KeywordIndex` 应该在 B 层之后的紧接着的一个迭代中进行，还是推迟到 Skill 项目之后？建议：在 B 层合并时提交跟踪 issue；安排在 Skill 项目之后，以便可以从那里引用相同的检索模式（每步 BM25 + 通过 `repo_index.json` + `repo_search.py` 进行的 1 跳扩展）。
