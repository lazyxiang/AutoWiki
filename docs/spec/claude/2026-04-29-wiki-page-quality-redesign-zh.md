# AutoWiki 页面生成质量 —— 设计规范

**状态：** 草案 1
**作者：** lazyxiang
**日期：** 2026-04-29
**范围：** Wiki 页面生成流水线（规划器第 2 阶段 + 页面生成器第 1–4 阶段）以及将其提取为独立 Claude 技能（Skill）的路径。
**相关项：** Issue #39, PR #40, `docs/superpowers/plans/2026-04-10-wiki-page-quality-redesign.md`

---

## 1. 执行摘要

对 AutoWiki 自身进行索引生成的 Wiki 虽然 **结构正确**，但其 **页面内容存在系统性错误**，主要体现在三个方面：相关文件未按相关性排序、编排器类文件的覆盖不足，以及相邻兄弟页面的内容相互渗透。在重新阅读实际的 `wiki_plan.json` 时，发现了第四个更严重的故障：由于第 2 阶段文件评分器仅支持 ASCII 且无法识别中文页面标题，导致整个前端/后端子树被分配到了错误的源文件。

PR #40 提高了检索、Token 和边缘预算，这虽然是必要的，但 **其本身并不能修复这些缺陷** —— 这些缺陷是结构性的（排序、所有权、范围、分词），而非容量限制导致的。

本规范提出了三层修复方案，所有方案均可通过特性标志（Feature Flags）发布，并能干净地回滚：

- **A 层 —— 规划器与提示词补丁（1 个 PR，约 350 行代码）。** 修复文件排序、兄弟页面感知大纲、初稿系统提示词中的范围约束、多查询检索（带单个文件配额）、支持 CJK 的第 2 阶段分词、强制执行文件所有权以及自适应的单页面文件预算。目标是解决 P1、P3 和前端折叠缺陷；部分缓解 P2。
- **B 层 —— 章节级起草 + BM25 检索（2 个 PR，约 600 行代码）。** 将单次起草替换为 `骨架 → 分章节起草 → 拼接` 模式，并在 FAISS 之外增加确定性的 BM25 检索器。从根源上解决 P2。
- **C 层 —— Wiki 即技能（1 个 PR + 技能包，约 1000 行代码）。** 通过将所有检索路由至 `fast_report_index.json` 和 B 层中的 BM25 检索器，移除对 FAISS 的依赖，使页面生成器可以在 Claude 技能沙箱中运行。

---

## 2. 当前状态

### 2.1 流水线形态

```
仓库 URL ──▶ 第 1 阶段：摄取 ──▶ 第 2 阶段：AST 分析 ──▶ 第 3 阶段：依赖图
        ──▶ 第 4 阶段：RAG 索引器 (FAISS) ──▶ 第 5 阶段：Wiki 规划器 (2 阶段 LLM)
        ──▶ 第 6 阶段：页面生成器 (4 阶段 LLM，每页执行，支持并发)
```

Wiki 规划器由两次 LLM 调用组成：第 1 阶段生成大纲（标题、用途、父级、可选的 `en_keywords`）；第 2 阶段从 25–40 个预过滤的候选文件中为每个页面选择 5–8 个源文件。页面生成器为每个页面运行四个阶段：大纲（快速模型）→ 初稿（主模型）→ 事实核查（快速模型）→ 有条件的修订（主模型）。

### 2.2 大纲与初稿提示词实际消耗的内容

**第 1 阶段 —— 大纲 (`worker/pipeline/page_outline.py:224-254`)**

| 来源 | 内容 | 限制 |
|---|---|---|
| `WikiPageSpec` | `title`, `purpose`, `files` (5–10) | — |
| `entity_details` | 最多 25 行（PR #40：`8·N`）实体信息：类型 / 名称 / 签名 / 文档字符串(150 字符) / 文件:行号 | `_format_entity_details` |
| `dep_info` | `depends_on` / `depended_by` / `external_deps`，各前 10 条 | 字符串拼接 |
| `child_titles` | 子页面标题（仅父页面） | — |

至关重要的是，**大纲提示词中不包含源代码。** 章节、关键主张和图表计划仅根据元数据 + 实体签名 + 依赖摘要来确定。

**第 2 阶段 —— 初稿 (`worker/pipeline/page_draft.py:131-263`)**

继承第 1 阶段的输入，外加：
- `outline`（章节 JSON）
- `context_chunks`：来自 `FAISSStore.multi_search` 的 top-k 块 —— `k=12`（PR #40 为 30），`doc_k=1`（降低纯文档权重）
- `child_contents`：已生成子页面的结构化摘要（标题、图表、前 200 字符简介，强制限制 2000 字符）
- `repo_notes`, `page_notes`

检索查询 (`page_generator.py:224-249`)：

```python
queries = [f"{spec.title} {' '.join((spec.files or [])[:5])}"]
if spec.purpose: queries.append(spec.purpose)
if entity_details: queries.append(' '.join(top5_entity_names))
```

页面标题和前 5 个文件路径被拼接成一个查询。返回的块上没有针对每个文件的配额限制。

### 2.3 观测到的 Wiki（来自重新导出的实际数据）

对 AutoWiki 自身进行索引在 `~/Downloads/wiki_plan.json` 中生成了 26 个页面。激发本规范的选定条目如下：

| 页面 | `files`（原文，按顺序） |
|---|---|
| 依赖图谱构建 | `[wiki_planner.py, dependency_graph.py]` |
| 内容生成引擎 | `[page_outline.py, page_generator.py, outline_anchors.py, fact_check.py, test_page_outline.py, prompt_segment.py]` |
| 质量校验与修订 | `[fact_check.py, page_outline.py, page_generator.py, test_fact_check.py, prompt_segment.py]` |
| Mermaid 图表优化 | `[mermaid.py, page_outline.py, page_generator.py, test_mermaid_sanitize.py]` |
| 前端应用架构 | `[shared/config.py, wiki_planner.py, shared/models.py, mermaid.py, test_dependency_graph.py]` |
| Wiki 渲染组件 | `[wiki_planner.py, models.py, mermaid.py, page_outline.py, page_generator.py, test_mermaid_sanitize.py]` |
| 后端接口与服务 | `[web/lib/api.ts]` |
| 实时通信机制 | `[rag_indexer.py, models.py, jobs.py, page_generator.py, deep_research.py, test_deep_research.py]` |

同一导出中的跨页面文件频率：
- `worker/pipeline/wiki_planner.py` 出现在 **7 个页面** 中
- `worker/pipeline/page_generator.py` 出现在 **5 个页面** 中
- `worker/pipeline/page_outline.py` 出现在 **4 个页面** 中

前端页面包含零个 `web/` 文件；后端页面仅包含一个前端文件。

---

## 3. 问题陈述

### P1 —— 文件列表未按相关性排序

“依赖图谱构建”页面理应记录 `dependency_graph.py`，但生成的页面内容却被 `WikiPlanner` 占据。该页面的 `files` 列表将 `wiki_planner.py` 排在首位；检索查询（`spec.title + files[:5]`）和实体格式化程序都按此顺序消耗文件，因此体积更大、内容更密集的文件占据了优势。用户看到的页面标题与内容不符。

### P2 —— 编排器文件覆盖不足

“内容生成引擎”页面理应记录 `generate_page` 和 `generate_page_batch`；生成的页面几乎没有提到这两者。该页面分配了 6 个文件，但 FAISS 检索没有单文件配额，导致编排器代码（主要是委托和小型辅助函数）在分块级评分中败给了大纲/验证类代码。结果该页面读起来就像是 `page_outline.py` 的代码走读。

### P3 —— 兄弟页面内容渗透

“质量校验与修订”页面包含一个“大纲校验与生成优化”章节，记录了 `validate_outline`，而这本应属于其兄弟页面“内容生成引擎”。原因是机械性的：`page_outline.py` 同时出现在两个页面的 `files` 列表中，两个页面检索到了重叠的代码块，且大纲提示词没有告知任何一个页面其兄弟页面覆盖了哪些内容。

### P4 —— 第 2 阶段文件评分器仅支持 ASCII，无法识别非英语标题

`worker/pipeline/wiki_planner.py:733-735`：

```python
def _tokenize(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3}
```

对于“前端应用架构 / 介绍 Next.js 应用的路由结构与交互组件库”，这只会生成 `{"next"}` —— 唯一的 Token。随后 `_score_file_for_page` 几乎完全根据依赖图中心性和实体数量对文件进行排名，完全忽略了页面的主题。第 2 阶段 LLM 接收到的 25 个候选文件与前端毫无关系，导致生成的 `files` 列表包含零个 `web/` 文件。同样的故障级联到了“Wiki 渲染组件”、“后端接口与服务”和“实时通信机制”。

### 为什么 PR #40 还不够

PR #40 提高了容量（`top_k 12→30`, `max_files 200→500..800`, `max_edges 500→1500`, 实体上限 `25→8·N`, `max_tokens 16k→32k`）。它解决了 *截断* 和 *摘要完整性* 问题，但没有解决排序、所有权、范围或分词问题 —— 而这正是驱动 P1–P4 的四个杠杆。

### 非目标

- 在整个代码库中替换 FAISS 或基于嵌入的搜索。C 层使 BM25 成为 Wiki 路径的可行替代方案；聊天 / 深度研究可以继续使用 FAISS。
- 重新架构规划器大纲（第 1 阶段）。分析中的导出显示第 1 阶段的输出在很大程度上是正确的。
- 多语言内容生成规则；本规范仅解决检索和路由问题。

---

## 4. 根本原因分析

### 4.1 代码级证据

| 缺陷 | 来源 | 机制 |
|---|---|---|
| P1 排序 | `wiki_planner.py:334-353, 504-536` | `_SELECTION_SCHEMA.selections.items.files` 是 `array<string>` —— 没有排序提示；`wiki_planner.py:355-386` 的系统提示词和 `_build_selection_user` 的用户提示词从未要求 LLM 按相关性排序。 |
| P1 传播 | `page_generator.py:225` | `queries = [f"{spec.title} {' '.join((spec.files or [])[:5])}"]` —— 前 N 个文件路径直接流入检索查询，因此第 2 阶段生成的顺序直接决定了 RAG 上下文。 |
| P2 分块稀释 | `page_generator.py:247` | `multi_search(query_vecs, k=top_k, doc_k=1)` 按全局评分返回 top-k，没有单文件配额 —— 一个大文件就能垄断预算。 |
| P2 实体稀释 | `page_formatters.py:41` | `entities[:25]`（PR #40：`8·N`）取自全局列表，没有单文件配额；编排器文件贡献的实体较少，被排挤在外。 |
| P2 大纲盲区 | `page_outline.py:224-254` | 大纲提示词从未见过源代码 —— 仅包含实体签名和依赖摘要，因此编排器的“我委托给 A, B, C”结构在规划时是不可见的。 |
| P3 兄弟页面感知缺失 | `page_outline.py:237-238` | 仅注入了 `child_titles`；未注入兄弟页面标题和超出范围的主题。 |
| P3 文件共同所有权 | 第 4.6 阶段选择设计 | 在移除孤儿强制执行后，没有任何规则阻止两个页面声称拥有同一个文件。导出显示 `page_outline.py` 被“内容生成引擎”和“质量校验与修订”共同拥有。 |
| P4 ASCII 分词器 | `wiki_planner.py:733-735` | `_tokenize` 剥离了所有 CJK 字符；对于中文页面，`_prefilter_candidates → _score_file_for_page` 流水线实际上对标题视而不见。 |
| P4 已在别处修复 | `worker/fast_report_search.py:943-959` | 快速报告路径已经拥有一个支持 n-gram、驼峰命名拆分和路径分词的 CJK 感知分词器。Wiki 规划器尚未引入它。 |

### 4.2 为什么仅靠排序规则是不够的

即使第 2 阶段按相关性对 `files` 进行了排序，P2 依然会存在，因为检索/实体阶段没有单文件配额。因此 A 层必须结合 A1（排序）+ A4（单文件检索配额）+ A5（单文件实体配额）；三者缺一都无法为“内容生成引擎”交付合格的页面。

### 4.3 为什么仅针对大纲的修复是不够的

P3 无法通过更智能的第 1 阶段大纲来解决，因为分析显示大纲（标题 + 用途 + 父级）是正确的。泄露发生在第 2 阶段（文件选择）和第 2 次传递（初稿检索）之间。因此 A 层的修复方案分布在这两个阶段。

---

## 5. 方案设计

### 5.1 设计原则

1. **复用现有的零嵌入机制。** `fast_report_index.py`（带有导入、被导入、调用点、异常触发点、外部依赖、实体 Token 的确定性单文件索引）和 `fast_report_search.py`（CJK 感知分词器、BM25 风格评分、自适应图扩展、切片提取器）已经实现了 A 层和 C 层所需的大部分功能。直接引入，不要重复造轮子。
2. **单文件公平性优于单查询最优性。** 每个分配的文件都应获得保证的最低检索和实体预算份额；在填补保底份额后的剩余预算才分配给全局排序。
3. **明确的范围边界。** 兄弟页面标题和超出范围的主题应作为一等公民提示词输入，而非埋在系统消息中的暗示。
4. **全方位的特性标志。** `AUTOWIKI_RETRIEVAL=keyword|hybrid|faiss`、`AUTOWIKI_DRAFT_MODE=section|full`，外加 `LLMConfig` 中的单项修复标志，以便快速回滚。
5. **C 层是迁移而非重写。** 技能版的页面生成器必须能与树内（in-tree）版本进行差异对比。

### 5.2 A 层 —— 规划器与提示词补丁

| ID | 文件 | 变更 |
|---|---|---|
| **A1** | `worker/pipeline/wiki_planner.py` | 为 `_SELECTION_SCHEMA.files` 添加 `ordered_by_relevance` 描述。更新 `_SYSTEM` 和 `_build_selection_user` 要求按最具代表性优先排序。在 `_validate_selections` 中，使用 `_score_file_for_page` 对每个页面的 `files` 进行评分；如果 LLM 的排序与评分负相关，则重新排序。对 `_heuristic_select_files` 和 `_directory_cluster_assign` 应用相同的排序。 |
| **A2** | `worker/pipeline/page_outline.py:224-254`; `page_generator.py:266-274` | 使用 `sibling_titles: list[str] \| None` 和 `out_of_scope_topics: list[str] \| None` 扩展 `generate_page_outline`。将它们注入可缓存的前缀中，形式为：`兄弟页面（不要覆盖其主题；仅通过名称引用）：...` 以及 `超出范围（已在别处覆盖）：...`。`generate_page_batch` 从 `WikiPlan`（相同 `parent`）计算兄弟页面，并优先从兄弟页面的 `purpose` 首句派生 `out_of_scope_topics`。 |
| **A3** | `worker/pipeline/page_draft.py:27` (`DRAFT_SYSTEM`) | 追加一条“范围约束”规则：保持在分配的文件范围内；如果某个主题由提示词中列出的兄弟页面拥有，则只用不超过 1 句话进行概括并按标题引用该兄弟页面；不要重复记录已被明确分配到别处的验证、图表后处理、分块等内容。 |
| **A4** | `worker/pipeline/page_generator.py:224-249` | 将单个检索查询替换为五个查询：`spec.title`、`spec.purpose`、`" ".join(en_keywords or _derive_keywords(spec))`、前 5 个实体名称、文件主名（stem）。执行 `multi_search` 后，运行 `_balance_chunks(chunks, files=spec.files, k=top_k, floor_per_file=2)`，确保每个分配的文件在添加全局尾部数据前至少获得两个分块。 |
| **A5** | `worker/pipeline/page_formatters.py:41` | 重构 `_format_entity_details` 以接受 `files: list[str]` 和 `max_entities`。计算 `per_file = max(3, max_entities // len(files))`，先提取单文件切片（按重要性排序），然后从全局填充剩余部分。从两个调用点传递 `files=spec.files`。 |
| **A6** | `worker/pipeline/page_outline.py:_build_outline_prompt` | 新的可选参数 `signature_slices: dict[file, list[str]]`。为每个 `spec.file` 提取 1–2 个最高重要性的实体，并在可缓存前缀中发出一个 `Signature slices` 块（文件:行号范围 + 实体正文前 4 行）。切片来自 `FileAnalysis` 加克隆根目录读取。 |
| **A7** | `worker/pipeline/wiki_planner.py:733-735` | 将仅支持 ASCII 的 `_tokenize` 替换为 `from worker.fast_report_search import _tokenize as _cjk_tokenize`。审计其他调用点（`_score_file_for_page`、`_best_matching_page`、`_directory_cluster_assign`）并迁移到相同的分词器。 |
| **A8** | `worker/pipeline/wiki_planner.py:_select_files` | 验证后，运行 `_enforce_ownership(selections, outline, dep_graph, file_infos)`：计算单文件出现次数；对于被 ≥ 2 个页面拥有的文件，使用 `_score_file_for_page` 对每个所有者进行评分，并仅在兄弟页面（共享 `parent` 的页面）中保留评分最高的所有者；允许最多 2 个非兄弟所有者（架构枢纽）；否则降级。总分配量上限设为 `1.5 × len(all_repo_files)`。 |
| **A9** | `worker/pipeline/wiki_planner.py:_select_files`, `_validate_selections` | 将静态的 5–8 / 强制上限 10 规则替换为根据预过滤评分分布计算的 `n_target = clamp(2, ceil(median_score / score_threshold), 8)`。窄主题分配 2–3 个文件；宽主题保持 5–8 个。强制上限维持在 10。 |

**对分析中的导出数据的综合影响：**

- “依赖图谱构建” → `[dependency_graph.py, wiki_planner.py]`（A1 + A8 将 wiki_planner 降级为枢纽引用）
- “内容生成引擎” → `[page_generator.py, page_outline.py]`（A8 移除了 `fact_check.py` / `test_page_outline.py` / `prompt_segment.py`；A1 提升了编排器；A4/A5 为其提供了专门的分块和实体配额）
- “质量校验与修订” → `[fact_check.py, test_fact_check.py]`（A8 移除了对 `page_outline.py` 的共同所有权）
- “Mermaid 图表优化” → `[mermaid.py, diagram_post_processor.py, test_mermaid_sanitize.py]`
- “前端应用架构” → `web/app` 和 `web/components` 下的文件（A7 修复了预过滤评分）
- “后端接口与服务” → `api/main.py`, `api/routers/*.py`, `api/queue.py` (A7)

### 5.3 B 层 —— 章节级起草 + BM25 检索

#### B1. 确定性关键字索引

添加 `worker/pipeline/keyword_index.py`：

```python
@dataclass
class KeywordIndex:
    chunks: list[Chunk]                    # 与 FAISSStore 使用相同的 Chunk 模型
    bm25: BM25Okapi                        # rank_bm25 — 纯 Python，无原生依赖
    file_to_chunks: dict[str, list[int]]
    token_idf: dict[str, float]            # 复用来自 fast_report_index 的 Token

    @classmethod
    def build(cls, chunks, *, fast_report_index): ...

    def search(
        self,
        queries: list[str],
        *,
        k: int,
        files: list[str] | None = None,
        per_file_quota: int = 2,
    ) -> list[Chunk]: ...

    def hybrid_search(
        self,
        queries: list[str],
        *,
        k: int,
        vec_store: FAISSStore | None = None,
        alpha: float = 0.5,
    ) -> list[Chunk]: ...
```

为什么使用 BM25 (`rank_bm25`) 而非 LlamaIndex：AutoWiki 已经拥有自己的分块逻辑、规划器模式和检索接口。`rank_bm25` 加上 `fast_report_search._tokenize`（CJK n-gram、驼峰拆分、路径拆分）仅需约 50 行代码即可落地一个可工作的多语言检索器，且无原生依赖，能保持在技能沙箱内。如果以后偏好 LlamaIndex，可将其 `BM25Retriever` + `QueryFusionRetriever` 封装在 `KeywordIndex.search` 之后，而非让抽象泄露到流水线中。

#### B2. 章节级起草

将单次执行的第 2 阶段替换为：

```
阶段 1    大纲                           — 使用 fast_llm，保持不变
阶段 2a   骨架 (新增)                     — 使用 fast_llm；仅输出 H1 + 章节标题
阶段 2b   章节起草 (新增，并行)            — 针对每个 SectionPlan：
          检索 = 章节标题 + 章节重心 + 实体名称
          范围 = spec.files ∩ (section.diagram.source_files ∪ 检索派生文件)
          预算 = 5–10 个分块，250–600 字
阶段 2c   拼接 (新增)                     — 使用 fast_llm；连接各章节，添加过渡句，不进行事实性改写
阶段 3    事实核查                       — 保持不变
阶段 4    有针对性的修订                  — 保持不变
```

添加 `worker/pipeline/page_section_drafter.py`。在 `page_draft.py` 中保留 `legacy_full_draft` 入口点，以便通过 `AUTOWIKI_DRAFT_MODE=full` 立即回滚。

章节级起草是解决 P2 的根本手段：每个章节都获得独立且针对其源文件范围的检索，因此编排器章节可以检索到编排器分块，无论大纲文件内容多么密集。

#### B3. 大纲模式中的 `out_of_scope_claims`

使用 `out_of_scope_claims: array<string>` 扩展 `_OUTLINE_SCHEMA`。事实核查提示词接收此列表；如果初稿包含与任何超出范围短语匹配的主张，事实核查返回 `verdict="fail"`，第 4 阶段在 LLM 修订前剥离违规句子。

### 5.4 C 层 —— Wiki 即技能

#### C1. 技能包布局

```
skills/autowiki-page/
  SKILL.md                          # Frontmatter + 使用指南
  references/
    prompt_outline.md               # 第 1 阶段系统提示词（无嵌入引用）
    prompt_section.md               # 第 2b 阶段系统提示词
    prompt_factcheck.md             # 第 3 阶段系统提示词
  src/
    keyword_index.py                # = B 层的 KeywordIndex
    page_section_drafter.py         # = B 层的起草器
    fast_report_index_loader.py     # 封装 worker/pipeline/fast_report_index.py
    cli.py                          # python -m skill.cli generate <page_slug>
  scripts/
    build_index.sh                  # 调用 worker.cli 以复用摄取+AST+依赖逻辑
```

#### C2. 公共契约

```python
def generate_wiki_page(
    *,
    page_spec: dict,                  # title, purpose, files, parent, siblings, out_of_scope
    fast_report_index: dict,          # ast/fast_report_index.json
    clone_root: Path,                 # 克隆的源码根目录
    llm: LLMProvider,                 # 主模型
    fast_llm: LLMProvider,            # 用于大纲 / 事实核查 / 拼接
    wiki_language: Literal["en", "zh"] = "en",
) -> PageResult: ...
```

`fast_report_index` 已经携带了技能所需的所有信号：`directory_tree`、`hub_modules`、`readme_sections` 以及每个文件的 `imports / imported_by / entities / call_sites / exception_touchpoints / external_deps`。无需 FAISS，无需嵌入服务商。

#### C3. 单章节检索

```python
def retrieve_for_section(section, *, page_files, index, clone_root, profile):
    # 1) 种子 = page_files ∩ section.diagram.source_files
    # 2) 如果种子不足，通过 BM25 在 page_files 上扩展
    # 3) 通过导入 / 被导入 / 调用点进行一阶扩展，受 token_budget 限制
    # 4) 通过 fast_report_slices.extract_source_slice 提取实体级切片
    return CodeEvidence(snippets=..., citations=..., evidence_blocks=...)
```

这直接复用了 `worker/fast_report_search.py` 中的 `_expand_candidate_paths`、`_build_slice_candidates` 和 `_apply_token_budget`。一个新的 `expansion_graph_for("wiki_page")` 配置档提供了针对 Wiki 调优的默认值。

#### C4. 章节提示词中的丰富代码上下文

每个章节提示词按顺序接收：

```
## Directory tree (focused)
worker/pipeline/
  page_generator.py         ← 页面文件
  page_outline.py           ← 页面文件
  fact_check.py             ← 页面文件
  page_draft.py             ← 被 page_generator 导入 (一阶)
...

## Hub modules
- worker/pipeline/page_generator.py  入度=4
  "生成流水线的第 6 阶段..."

## Call chain (针对章节 "多阶段编排")
generate_page → generate_page_outline (page_outline.py:257)
generate_page → generate_draft       (page_draft.py:266)
generate_page → run_fact_check       (fact_check.py:149)
generate_page → run_targeted_revision (fact_check.py:323)

## Code slices (文件:行号, 完整源码)
[code-1-0] worker/pipeline/page_generator.py:190-360
   async def generate_page(spec, store, llm, fast_llm, ...): ...
[code-2-0] worker/pipeline/page_generator.py:130-163
   def compute_generation_order(plan) -> list[list[WikiPageSpec]]: ...
```

“调用链”（Call chain）数据块源自 `fast_report_index.json` 的 `call_sites` —— 这是 Wiki 路径目前未消耗的信号。C 层要求 Wiki 流水线同时在技能内部和树内版本中消耗它。

#### C5. 迁移路径

1. 保留 FAISS 路径作为默认值；添加 `AUTOWIKI_RETRIEVAL=keyword|hybrid|faiss`。
2. `page_generator.generate_page_batch` 从环境变量选择检索器：`FAISSStore` ↔ `KeywordIndex`。
3. `skills/autowiki-page` 调用相同的 `page_section_drafter`，但仅持有 `KeywordIndex`。
4. 一旦两个检索器都发布，第 6 阶段（“混合搜索”）自然成为 `hybrid` 选项。

---

## 6. 实施计划

### 6.1 阶段划分与退出准则

| 阶段 | PR | 代码量 | 退出准则 |
|---|---|---|---|
| **A** | `feat/wiki-quality-layer-a` | ~350 | 所有 A1–A9 补丁合并；`tests/worker/test_wiki_planner.py`（排序、所有权、CJK 分词、自适应上限）和 `tests/worker/test_page_generator.py`（单文件分块保底）中的新测试通过；对 AutoWiki 的冒烟索引产出第 5.2 节中预测的所有 8 个缺陷页面的文件列表。 |
| **B1** | `feat/keyword-index` | ~300 | `KeywordIndex.search` 在 100 个问题的固定测试集上与 FAISS top-k 召回率匹配度在 ±10% 以内；`rank_bm25` 添加至 `pyproject.toml`。 |
| **B2** | `feat/section-drafting` | ~300 | `AUTOWIKI_DRAFT_MODE=section` 生成的页面章节级事实核查通过率 ≥ 当前全页通过率；旧模式可通过环境变量访问。 |
| **C** | `feat/wiki-skill` | ~1000 | 在未配置嵌入服务商的情况下运行，`python -m skills.autowiki-page.cli generate <slug>` 为 AutoWiki 自索引产出的页面与 B 阶段输出在差异等效性上一致（在事实核查判定范围内）。 |

PR #40 先落地；每一层都假设其预算已到位。

### 6.2 测试计划

每一层发布时都带有针对 `tests/fixtures/simple-repo/` 的确定性测试，以及断言第 5.2 节中文件列表预期的自索引回归测试。

- **A 层单元测试：**
  - `test_select_files_orders_by_relevance` —— 对于中文标题“依赖图谱”页面，第 2 阶段优先返回 `dependency_graph.py`。
  - `test_enforce_ownership_demotes_sibling_share` —— `page_outline.py` 不能分配给两个兄弟页面。
  - `test_cjk_tokenize_prefilter` —— `_score_file_for_page` 为“前端应用架构”将 `web/components/*.tsx` 排在最高位。
  - `test_balance_chunks_floor` —— 当 `k ≥ 2·len(files)` 时，每个 `spec.file` 至少获得 2 个分块。
  - `test_outline_prompt_includes_siblings` —— 兄弟页面标题 + 超出范围主题出现在可缓存前缀中。
  - `test_draft_system_prompt_scope_rule` —— 新的“范围约束”规则存在。

- **B 层集成测试：**
  - `test_keyword_index_recall_parity` —— 固定问题集上的 BM25 与 FAISS 召回率对比。
  - `test_section_drafter_independent_retrieval` —— 即使大纲文件占据主导，编排器章节也能接收到编排器分块。
  - `test_out_of_scope_claims_trigger_factcheck_fail`。

- **C 层冒烟测试：**
  - `test_skill_cli_generates_without_faiss` —— 在未配置 FAISS 服务商的临时目录中运行技能 CLI。
  - `test_skill_consumes_call_chain` —— 章节提示词包含源自 `call_sites` 的 `Call chain` 块。

- **自索引回归：** 一个 CI 任务运行 `autowiki index . --reuse-index=false` 对 AutoWiki 仓库进行索引，并断言第 5.2 节中列出的 8 个页面的 `files[0]` 包含预期的主要文件。

### 6.3 回滚与特性标志

- `AUTOWIKI_RETRIEVAL=keyword|hybrid|faiss`（在 B 层落地前默认为 `faiss`；之后切换为 `hybrid`）。
- `AUTOWIKI_DRAFT_MODE=section|full`（在 B 层退出前默认为 `full`；之后切换为 `section`）。
- A8 使用 `AUTOWIKI_PLANNER_OWNERSHIP=enforce|advise|off`（一个版本默认为 `advise`，之后改为 `enforce`）。
- A7 使用 `AUTOWIKI_PHASE2_TOKENIZER=cjk|ascii`（立即默认为 `cjk`；保留 `ascii` 一个版本作为紧急出口）。

### 6.4 风险与对策

| 风险 | 对策 |
|---|---|
| BM25 在自然语言查询（聊天 / 深度研究）上表现弱于 FAISS | B 层为非 Wiki 路径保留 FAISS；当两者都可用时，`hybrid_search` 进行内插。 |
| 章节级起草导致 LLM 调用次数激增 | 章节起草阶段使用 `fast_llm`；`AUTOWIKI_PAGE_CONCURRENCY` 已限制并发量；单章节上下文更小，因此实际耗时与单次生成相当。 |
| 所有权强制执行导致“枢纽”文件（如 `models.py`）分配不足 | A8 明确允许最多 2 个非兄弟所有者，并利用现有的 `_compute_hub_modules` 豁免入度排名前 10% 的文件。 |
| CJK 分词器变更波及 `_directory_cluster_assign` 并导致意外的分配变更 | 测试会将新行为固定在 AutoWiki 自索引预期上；`AUTOWIKI_PHASE2_TOKENIZER` 标志提供即时回滚。 |
| 技能版本与树内流水线发生偏移 | C 层逐字移植模块；CI 通过两个入口点运行相同的固定装置并对比渲染后的 Markdown。 |

### 6.5 遥测

使用现有的 `pipeline_logging.log_validation_retry` / `log_final_failure` 渠道。在以下位置添加结构化事件：

- `wiki_planner.ownership_demotion` —— 文件、降级页面、主要页面、评分差值。
- `page_generator.balance_chunks` —— 页面、文件、单文件分配量、剩余。
- `page_section_drafter.section_factcheck` —— 页面、章节、判定、问题数量。

这些数据让我们能衡量 A 层的所有权和所有权规则是否真的在实际仓库中生效，而非仅仅通过日志分析进行推测。

---

## 7. 验收标准

当新的 AutoWiki 自索引满足以下条件时，A 层发布：

1. 第 5.2 节中列出的 8 个页面带有预测的文件列表。
2. `质量校验与修订.md` 不包含以 `validate_outline` 或 `PageOutline` 为主要主题的章节。
3. `内容生成引擎.md` 包含 `generate_page`, `generate_page_batch` 或 `compute_generation_order` 符号中的至少一个。
4. `前端应用架构.md` 包含至少 3 处对 `web/` 目录下文件的引用。
5. 除了 `dep_graph` 入度排名前 10% 的文件外，没有任何源文件被分配给超过 2 个页面。

此外，当满足以下条件时，B 层发布：

6. 章节级事实核查通过率 ≥ 先前的全页事实核查通过率（在 AutoWiki 自索引及三个外部固定仓库上测量）。

此外，当满足以下条件时，C 层发布：

7. 在未配置嵌入服务商的情况下运行 `python -m skills.autowiki-page.cli generate <slug>`，对 AutoWiki 自索引生成的输出与 B 层树内输出在相同 slug 下完全一致（忽略空白符差异）。

---

## 8. 开放问题

- A8 所有权强制执行应在 `_validate_selections` 之前还是之后运行？在之前运行可能会拒绝原本符合模式的 LLM 结果；在之后运行则有重新触发验证的风险。目前的建议是“之后运行，并额外给予一次重试预算”。
- 我们是否希望章节草稿保留各自的 `repo_notes` 块，还是让拼接阶段在顶部统一注入？建议：仅在拼接阶段注入，以保持章节提示词专注于代码。
- C 层章节提示词与 B 层章节提示词 —— 保持同步，还是当技能特有约束（如仅限文件系统）出现时进行分支？建议：保持同步，直到出现具体的背离。
