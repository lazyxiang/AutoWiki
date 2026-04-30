# AutoWiki 架构指南

这是一份为架构师和开发人员准备的分步阅读指南，旨在帮助他们快速理解项目的
设计决策、追踪实现流程并定位关键信息。

---

## 1. AutoWiki 是什么

AutoWiki 是一个自托管、开源、由 AI 驱动的软件仓库维基生成器。
给定一个 GitHub URL，它会生成一个可浏览的多级维基 —— 包括架构
概览、模块分解、依赖图、源链接文档以及对话式问答 —— 使用用户提供的 API 密钥在本地运行。

塑造每个架构决策的三个核心设计目标：

1. **准确性** —— Tree-Sitter AST + 依赖图 + 带有事实核查 (fact-check) 阶段的多阶段 LLM 生成，
   优于仅使用 RAG 或仅使用 AST 的方法。
2. **异步生成** —— 任务被加入队列并在后台 worker 中运行；
   API 永远不会因为等待 LLM 调用而阻塞。
3. **增量刷新** —— 只有源文件发生变化的页面才需要重新生成。

---

## 2. 从何处开始：推荐阅读顺序

| 步骤 | 文档 | 确立内容 |
|------|----------|---------------------|
| 1 | `docs/superpowers/specs/2026-03-22-autowiki-design.md` | 产品目标、竞争背景、分阶段交付路线图、原始 API 表面、存储布局 |
| 2 | `CLAUDE.md` (项目根目录) | 权威的当前架构：服务拓扑、6 阶段流水线、存储布局、所有关键实现说明 |
| 3 | `docs/superpowers/specs/2026-04-08-wiki-planner-improvements-design.md` | 两阶段规划器、更丰富的文件摘要、自下而上生成、`generate_batch` —— Phase 2.5 规划器重新设计 |
| 4 | `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md` | 4 阶段页面生成（大纲 → 草案 → 事实核查 → 修订）、`PromptSegment` 缓存、快速模型拆分、`doc_k` 检索权重降低 |

有关各阶段的实现细节，请参阅相应的计划文档（见下文 §4）。

> **快速导向**：如果你只读两份文档，请阅读 `CLAUDE.md` 以了解当前状态，以及
> `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md` 以了解
> 最新的、最详细的设计原理。

---

## 3. 系统架构概览

```text
用户 (浏览器 / CLI / MCP)
    ↓
API 网关 (FastAPI)  ←→  Redis
    ↓
Worker 服务 (ARQ 任务队列)
    ↓
存储 (~/.autowiki/): SQLite + FAISS + Markdown 文件
```

### 6 阶段生成流水线 (Pipeline)

```text
第 1 阶段  摄入 (Ingestion)     浅克隆、文件过滤、提交 SHA
第 2 阶段  AST 分析             单次 Tree-Sitter 解析 → FileAnalysis（实体、计数）
第 3 阶段  依赖图               文件级导入边 → 连通分量簇
第 4 阶段  RAG 索引器           LangChain 分块 → FAISS IndexFlatIP；可通过 --reuse-index 跳过
第 5 阶段  维基规划器           两阶段 LLM：第 1 阶段大纲（标题/层次结构），第 2 阶段文件选择（每页 5-8 个）
第 6 阶段  页面生成器           自下而上，每页 4 阶段：大纲 → 草案 → 事实核查 → 修订
```

**曾经的第 7 阶段是什么？** 图表合成阶段 (`diagram_synthesis.py`) 曾在
Phase 2 中短暂加入，并在 Phase 2.5 中被移除 —— 页面生成器的提示词
已经可以生成架构 Mermaid 图表，使其变得多余。

---

## 4. 文档映射：按时间顺序演进

下表标注了每份文档的状态及其引入或取代的内容。

### 初始设计

| 文档 | 状态 | 引入内容 |
|----------|--------|------------|
| `docs/superpowers/specs/2026-03-22-autowiki-design.md` | 已批准（Phase 1–2 已完成，3–5 待定） | 完整产品 PRD：目标、竞争分析、API 表面、分阶段交付 |
| `docs/superpowers/specs/2026-03-29-autowiki-frontend-design.md` | 已完成 | 视觉设计规范：三栏布局、亮色模式配色方案、组件清单 |

### Phase 1 — 核心 MVP

| 文档 | 状态 | 引入内容 |
|----------|--------|------------|
| `docs/superpowers/plans/2026-03-22-phase1-core-mvp.md` | 已完成（过时：`build_module_tree`，5 阶段计数） | 分步实现：Docker Compose, FastAPI, ARQ, SQLite, FAISS, Next.js UI |
| `docs/2026-03-25-improve-wiki-quality-plan.md` | 已完成（被取代：`build_enhanced_module_tree` API） | 依赖图、增强版 AST、架构图、规划器的实体上下文 |
| `docs/2026-03-25-improve-wiki-ux-plan.md` | 已完成 | 任务上的 `status_description`、分级侧边栏、Markdown CSS 正文样式 |
| `docs/2026-03-27-improve-llm-retry-plan.md` | 已完成（过时：“5 阶段”引用） | 指数退避重试、`async_retry`、`OnRetryCallback`、`TRANSIENT_EXCEPTIONS` |
| `docs/2026-03-27-improve-logging-plan.md` | 已完成 | `LoggingLLMProvider`、`--debug` 标志、`error.log` / `task.log` / `llm.log` |
| `docs/2026-03-28-pipeline-refactoring-plan.md` | 已完成（过时：第 7 阶段部分，一些 helper 名称） | `FileAnalysis` 单次 AST、`WikiPlan`、`wiki_plan.json`、移除 `module_tree.json` |
| `docs/2026-03-28-simplify-code-plan.md` | 已完成（过时：helper 名称被重构取代） | 将 `jobs.py` 分解为各阶段的 helper、去重 |

### Phase 2 — 聊天、图表与刷新

| 文档 | 状态 | 引入内容 |
|----------|--------|------------|
| `docs/superpowers/plans/2026-03-23-phase2-chat-diagrams-refresh.md` | 已完成（过时：`diagram_synthesis.py`, `module_tree.json`, `get_affected_modules`） | 多轮聊天、增量刷新、`.autowikiignore`、依赖图 UI |
| `docs/superpowers/plans/2026-03-29-frontend-redesign.md` | 已完成 | 三栏维基布局、ReactFlow 依赖图、ChatDrawer、RefreshButton |
| `docs/2026-04-03-wiki-generation-language-plan.md` | 已完成（过时：步骤 4–5 引用了第 7 阶段） | 从前端 → API → worker → LLM 提示词透传的 `wiki_language` 参数 (EN/ZH) |

### Phase 2.5 — 维基质量提升

| 文档 | 状态 | 引入内容 |
|----------|--------|------------|
| `docs/superpowers/specs/2026-04-08-wiki-planner-improvements-design.md` | 已实现，已合并至 main | 两阶段规划器、`generate_batch`、更丰富的文件摘要、`_suggest_page_range`、自下而上生成、按阶段验证 |
| `docs/superpowers/plans/2026-04-08-wiki-planner-improvements.md` | 已完成 | 上述内容的分步任务列表；实现偏差记录在顶部 |
| `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md` | 已实现，已合并至 main (PRs #15, #17) | 4 阶段页面生成、`PromptSegment` / 提示词缓存、`fast_model` 拆分、`doc_k` 权重降低、图表标题强制执行 |
| `docs/superpowers/plans/2026-04-10-wiki-page-quality-redesign.md` | 已完成 | 上述内容的分步任务列表 |

### Phase 3 & 4 — 深度研究与用户引导

| 文档 | 状态 | 引入内容 |
|----------|--------|------------|
| `docs/superpowers/plans/2026-04-14-phase3&4-deep-research-and-steering.md` | 已完成 (PR #20) | 多步 RAG 研究、LLM 规划器、综合报告、`autowiki research` CLI、通过 `wiki.json` 进行用户引导 |

### Phase 4.5 — 规划器健壮性增强

| 文档 | 状态 | 引入内容 |
|----------|--------|------------|
| `docs/2026-04-15-wiki-planner-robustness-investigation.md` | 已完成 | 调查结果：大纲碎片化根因、三种候选修复方案 |
| `docs/superpowers/plans/2026-04-15-wiki-planner-robustness.md` | 已完成 | 调查建议修复方案的实现计划，已在 PR #22 之前的提交中应用 |
| `docs/superpowers/plans/2026-04-16-deferred-wiki-planner-robustness.md` | **已完成 (PR #22)** | C1 层大纲锚点、`autowiki validate-plan` 离线测试框架 |

### Phase 4.6 — 以页面为中心的文件选择

| 文档 | 状态 | 引入内容 |
|----------|--------|------------|
| `docs/superpowers/plans/2026-04-17-page-centric-file-selection.md` | **已完成 (PR #23)** | 第 2 阶段从以文件为中心的分配改为以页面为中心的选择（每页 5–8 个文件）；基于评分的预过滤 + 回退 (`_score_file_for_page`, `_heuristic_select_files`)；`WikiPlan.all_repo_files` 用于正确的刷新覆盖 |

---

## 5. 端到端追踪一个功能

### 维基页面是如何生成的

1. **请求进入** —— `api/routers/repos.py` 中的 `POST /api/repos` 通过
   `api/queue.py` → Redis 将任务加入队列。

2. **Worker 领取任务** —— `worker/index/full.py` 中的 `run_full_index()` 是顶层
   编排器。阅读它可以了解阶段排序和进度报告。

3. **第 1–4 阶段** (`worker/pipeline/ingestion.py`, `ast_analysis.py`,
   `dependency_graph.py`, `rag_indexer.py`) 构建规划器所需的证据。
   关键输出：`FileAnalysis` 对象和 `FAISSStore`。

4. **第 5 阶段 —— 规划器** (`worker/pipeline/wiki_planner.py`):
   - 第 1 阶段：`_build_outline_prompt()`（带有来自 `outline_anchors.py` 的架构锚点） → LLM 调用 → `_validate_outline_structure()`；最多重试 `max_retries` 次并提供反馈
   - 第 2 阶段：`_select_files_in_batches()`（12 页一批，可缓存的系统提示词） → `_validate_assignments()`；重试并提供反馈；最后失败时回退到 `_heuristic_select_files()`（基于评分的启发式方法）
   - 结果：一个 `WikiPlan`（`WikiPageSpec` 列表，每个包含标题、目的、`files`、父页面）
   - 修正：第 2 阶段现在为每个页面选择 5–8 个代表性文件（最多 10 个），而不是将每个文件分配给一个页面。`WikiPlan.all_repo_files` 被持久化以在增量刷新期间实现正确的文件差异检测。
   - 离线诊断：`autowiki validate-plan <repo>` 读取 `ast/wiki_plan.json`
   - 设计原理：`docs/superpowers/specs/2026-04-08-wiki-planner-improvements-design.md` §5 和 `docs/superpowers/plans/2026-04-17-page-centric-file-selection.md`。

5. **第 6 阶段 —— 页面生成器** (`worker/pipeline/page_generator.py`):
   - `compute_generation_order(plan)` 自深向浅返回页面（先叶子页面，后父页面）
   - 对于每个深度级别，`generate_page_batch()` 并发运行页面
   - 每个页面通过 `generate_page()` 生成：
     - 第 1 步：通过快速模型进行 `generate_page_outline()` (`worker/pipeline/page_outline.py`)
     - 第 2 步：通过主模型进行 `generate_draft()` (`worker/pipeline/page_draft.py`)
     - 第 3 步：通过快速模型运行 `run_fact_check()` (`worker/pipeline/fact_check.py`)
     - 第 4 步：仅在事实核查结果为 `"fail"` 时，通过主模型运行 `run_targeted_revision()`
     - 后处理：`ensure_diagram_headers()` (`worker/pipeline/diagram_post_processor.py`)
   - 设计原理：`docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md` §5

6. **存储** —— 页面被写入 `~/.autowiki/repos/{hash}/wiki/*.md` 和
   SQLite 的 `wiki_pages` 表中。`wiki_plan.json` 保存到 `ast/`。

### 父页面与叶子页面的区别

父页面（在层次结构中有子页面的页面）最后生成 —— 在
其所有子页面之后。`generate_page()` 接收 `child_contents: list[PageResult]`，
即已经过事实核查的子页面 Markdown 字符串。父页面的提示词
使用这些子内容作为其主要证据，而不是原始 RAG 分块。
参见 `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md` §6。

### 增量刷新是如何工作的

`worker/index/refresh.py` 中的 `run_refresh_index()` 加载保存的 `wiki_plan.json`，
识别出自上次提交 SHA 以来发生变化的文件，并仅通过第 6 阶段流水线重新运行受影响的页面。
未更改的页面从磁盘读取，并可以为需要重新生成的父页面提供 `child_contents`。

---

## 6. 关键设计决策 —— 在何处寻找原理

| 决策 | 文档位置 |
|----------|-----------------|
| 两阶段规划器（先大纲后文件分配） | `docs/superpowers/specs/2026-04-08-wiki-planner-improvements-design.md` §5 |
| 自下而上生成（先子页面后父页面） | `docs/superpowers/specs/2026-04-08-wiki-planner-improvements-design.md` §7 |
| 4 阶段页面生成 | `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md` §3, §5 |
| `PromptSegment` 和 Anthropic 提示词缓存 | `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md` §8 |
| `doc_k` 降低过时设计文档权重 | `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md` §4 |
| `fast_model` / `fast_llm` 拆分 | `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md` §8.4 |
| 移除第 7 阶段（图表合成） | `docs/superpowers/specs/2026-04-08-wiki-planner-improvements-design.md` 实现说明 |
| `FileAnalysis` 单次 AST（取代了 `build_module_tree`） | `docs/2026-03-28-pipeline-refactoring-plan.md` |
| `wiki_plan.json` 对比旧的 `module_tree.json` | `docs/2026-03-28-pipeline-refactoring-plan.md` |
| `reuse_index` / `--reuse-index` | `CLAUDE.md` 实现说明；`docs/superpowers/specs/2026-04-08-wiki-planner-improvements-design.md` 实现说明 |
| 异步重试 / `OnRetryCallback` | `docs/2026-03-27-improve-llm-retry-plan.md` |
| 增量刷新逻辑 | `docs/superpowers/plans/2026-03-23-phase2-chat-diagrams-refresh.md` |

---

## 7. 仍计划中的工作（尚未实现）

> **注**：深度研究模式 (Phase 3) 和通过 `.autowiki/wiki.json` 进行用户引导 (Phase 4) 均**已实现** —— 在 PR #20 中交付。以下项是仍待处理的功能。

| 阶段 | 功能 | 参考 |
|-------|---------|-----------|
| Phase 5 | MCP 服务器 (`read_wiki_structure`, `read_wiki_page`, `search_wiki`, `ask_question`, `deep_research`) | `CLAUDE.md` API 表面 |
| Phase 5 | GitLab / Bitbucket 支持 | `docs/superpowers/specs/2026-03-22-autowiki-design.md` |
| Phase 5 | 混合搜索 (BM25 + 向量) | `docs/superpowers/specs/2026-03-22-autowiki-design.md` |
| 暂缓 | 用于自动刷新的 GitHub webhooks | `docs/superpowers/specs/2026-03-22-autowiki-design.md` §9 |
| 存根 | `cache_ttl: long` (1 小时 Anthropic 缓存) | `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md` 实现说明 |
