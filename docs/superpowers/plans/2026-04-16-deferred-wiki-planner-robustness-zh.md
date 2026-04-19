# 延期计划器改进 — 实施计划

> **状态: ✅ 已完成 — 在 PR #22 (`feature/deferred-planner-improvements`) 中实施。**

> **针对代办智能体:** 必需子技能: 使用 superpowers:subagent-driven-development (推荐) 或 superpowers:executing-plans 来逐任务执行此计划。步骤使用复选框 (`- [ ]`) 语法进行跟踪。

**目标:** 实施从 `docs/superpowers/plans/2026-04-15-wiki-planner-robustness.md` 中明确延期的三个项目 — (1) 层 C1 大纲锚点，(2) 层 C2 多页面文件分配，以及 (3) 独立的阶段验证工具 — 以便 Phase 1/2 计划器产生较少碎片化的页面层次结构，使一个文件可以在真正属于两个页面时同时出现在多个页面上，并允许维护者在不消耗实时 API 预算的情况下诊断计划器输出。

**架构:** 三个可独立交付的阶段。阶段 A 通过新的 `worker/pipeline/outline_anchors.py` 模块，使用目录树、包文档字符串和 README 子系统标题来丰富 `_build_outline_prompt`。阶段 B 演进分配架构 + `WikiPageSpec` 以携带 `secondary_files`（次要文件），并将这种区别贯穿于验证、增量刷新和页面生成上下文。阶段 C 为实时流水线添加了一个固件记录器 (fixture recorder) 和一个 `autowiki validate-plan` CLI，用于针对记录的固件回放计划器阶段，并报告覆盖率/大小/验证统计信息。

**技术栈:** Python 3.12, asyncio, pytest (`asyncio_mode=auto`), Typer, Anthropic/OpenAI/Gemini/Ollama SDKs。

**规范:** 此计划。参考资料:
- 父计划: `docs/superpowers/plans/2026-04-15-wiki-planner-robustness.md`（超出范围章节 + 任务 13）。
- 实施说明: `CLAUDE.md` → "Deferred Planner Improvements"。

**不在范围内:**
- 混合搜索 / GitLab / Bitbucket / MCP (Phase 5 — 单独跟踪)。
- 更改 4 步页面生成编排器 (Phase 2.5 已确定)。
- 实时 API 端到端验证（阶段 C 涵盖离线回放；实时冒烟测试超出预算）。

---

## 文件结构

**阶段 A — 大纲锚点 (Outline Anchors)**

创建:
- `worker/pipeline/outline_anchors.py` — 纯函数助手，从现有的流水线产物中合成目录树 / 包文档字符串 / README 章节锚点。
- `tests/worker/test_outline_anchors.py` — 每个助手的单元测试。

修改:
- `worker/pipeline/wiki_planner.py` — `_build_outline_prompt` 接受新的锚点输入；`generate_wiki_plan` 将 `clone_root` + `file_analysis` 传递进去。
- `worker/jobs.py` — 调用处传递 `clone_root`。
- `CLAUDE.md` — 在 "Key Implementation Notes" 下记录锚点语义。

**阶段 B — 多页面文件分配**

修改:
- `worker/pipeline/wiki_planner.py` — `_ASSIGNMENT_SCHEMA`, `_build_batch_assignment_user`, `_assign_files_in_batches`, `_validate_assignments`, `WikiPageSpec`, `WikiPlan`, `validate_wiki_plan`。
- `worker/pipeline/ingestion.py` — `get_affected_pages` 返回更丰富的结构。
- `worker/pipeline/page_generator.py` — 上下文构建器将次要文件包含为“参考”材料。
- `worker/jobs.py` — 增量刷新使用新的受影响页面结果。
- `CLAUDE.md` — 记录架构 + 刷新语义。

涉及的测试:
- `tests/worker/test_wiki_planner.py`
- `tests/worker/test_assign_files_batched.py`
- `tests/worker/test_ingestion.py`
- `tests/worker/test_page_generator.py` (如果缺失则创建)
- `tests/worker/test_jobs.py`

**阶段 C — 阶段验证工具 (Stage Validation Harness)**

创建:
- `worker/pipeline/fixture_recorder.py` — 用于转储 `outline.json`, `assignments.json` 和 `wiki_plan.json` 的助手。
- `cli/commands/validate_plan.py` — `autowiki validate-plan <repo>` Typer 命令。
- `tests/worker/test_fixture_recorder.py`
- `tests/cli/test_validate_plan.py`

修改:
- `worker/jobs.py` — 在设置了调试标志或环境变量时，在全量索引 + 刷新期间调用记录器。
- `cli/main.py` — 注册新命令。
- `CLAUDE.md` — 记录调试环境变量和 CLI 界面。

---

## 阶段 A — 大纲锚点 (Layer C1)

**目标:** 为第一阶段 (Phase-1) 的 LLM 提供三个明确的架构信号，以便内聚的子系统 (例如 `worker/pipeline/*`) 停止被粉碎并散布在对等的顶级页面中:

1. **带文件计数的前 3 层目录树** — 一个紧凑的 ASCII 树，涵盖仓库的目录结构，限制在三层深度。
2. **包文档字符串 (Package docstrings)** — 来自 `__init__.py` (Python), `mod.rs` (Rust) 和 `index.ts`/`index.js` (JS/TS) 的前导模块文档字符串。最多提取 25 个信号最强的条目。
3. **README 子系统标题** — 从 README 中提取的 `##`/`###` Markdown 标题。

这些锚点进入一个新源自纯助手模块的 "Architectural anchors" 提示词章节。`_build_outline_prompt` 获得三个新的可选参数；没有它们的行为与今天完全一致。`generate_wiki_plan` + `worker/jobs.py` 将它们喂入。

### 任务 A1: 创建带有助手和测试的 `outline_anchors` 模块

**文件:**
- 创建: `worker/pipeline/outline_anchors.py`
- 创建: `tests/worker/test_outline_anchors.py`

- [ ] **步骤 1: 编写失败的测试** (代码略，见英文版)
- [ ] **步骤 2: 运行测试并验证其失败**
- [ ] **步骤 3: 实施助手函数** (代码略，见英文版)
- [ ] **步骤 4: 运行测试并验证其通过**
- [ ] **步骤 5: 提交代码**

---

## 阶段 B — 多页面文件分配 (Layer C2)

> **部分取代说明 (PR #23):** 在此阶段描述的带有 `{file, primary_page, secondary_pages}` 的 `_ASSIGNMENT_SCHEMA` 在 PR #23 中被 `_SELECTION_SCHEMA` (`{page_title, files}`) 取代 — 这是一个以页面为中心的模型，LLM 为每个页面选择文件，而不是将文件路由到页面。`WikiPageSpec.secondary_files` 被保留，页面生成器在非空时仍会注入“参考模块”上下文，但第二阶段现在始终产生 `secondary_files=[]`。`AffectedPages` / `stale_secondary.json` 延期刷新逻辑仍然存在，并可以由未来重新填充 `secondary_files` 的工作激活。

**目标:** 允许单个源文件出现在多个维基页面上（例如，在“概览”和“核心流水线”深入探讨中都引用的共享实用程序）。LLM 为每个文件返回 `{file, primary_page, secondary_pages: [...≤2]}`。`WikiPageSpec` 携带 `files`（主要）和 `secondary_files`（共享）。增量刷新会立即重新生成受主要影响的页面，并延迟重新生成受次要影响的页面（标记为“过时”，但在同一刷新周期内不重新生成）。页面生成器将主要文件视为“拥有”的文件，将次要文件视为“参考”上下文，将其摘要连接到提示词中，而不在源文件表格中列出它们。

### 任务 B1: 演进 `WikiPageSpec` 和 `WikiPlan` 序列化

**文件:**
- 修改: `worker/pipeline/wiki_planner.py` — `WikiPageSpec`, `WikiPlan.to_*`
- 测试: `tests/worker/test_wiki_planner.py`

(步骤略，见英文版)

---

## 阶段 C — 阶段验证工具

**目标:** 让维护者能够内省计划器输出而无需花费实时 LLM 预算。实施了两个部分:

1. **固件记录器 (Fixture recorder)** — 当设置 `AUTOWIKI_RECORD_PLANNER_FIXTURES=1` 时，`worker/pipeline/fixture_recorder.py` 会将 `outline.json`, `assignments.json` 和 `wiki_plan.json` 转储到 `~/.autowiki/repos/{repo_hash}/fixtures/` 下。
2. **`autowiki validate-plan <repo>` CLI** — 读取 `ast/wiki_plan.json` 并报告:
   - 页面数量、主要文件数量和次要分配数量。
   - 每页主要/次要文件大小分布（最小值 / p50 / p90 / 最大值 / 平均值）。
   - 来自 `validate_wiki_plan` 的任何验证失败。
   - 每个页面的目录局部性分数 (directory locality score)（主要文件在顶级目录中的比例）。

该命令**不**消耗固件、回放计划器阶段或计算孤儿/覆盖率百分比。它是只读的，不进行任何写入。

> **实施说明 (PR #22/23 之后):** 固件记录器随后被移除 — `validate-plan` CLI 直接读取 `ast/wiki_plan.json` 并提供所有需要的诊断，而无需单独的固件目录。`worker/pipeline/fixture_recorder.py` 不再存在。
