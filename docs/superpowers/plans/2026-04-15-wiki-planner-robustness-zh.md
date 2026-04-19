# 维基计划器可观察性与健壮性 — 实施计划

> **针对代办智能体:** 必需子技能: 使用 superpowers:subagent-driven-development (推荐) 或 superpowers:executing-plans 来逐任务执行此计划。步骤使用复选框 (`- [ ]`) 语法进行跟踪。

**目标:** 解决导致计划器输出不佳的三个根本原因: 静默的重试/验证失败、破坏文件局部性的轮询 (round-robin) 回退，以及超出 LLM 可靠结构化输出能力的单次第 2 阶段文件分配。

**架构:** 按顺序执行的三个连续阶段。阶段 1 使每次重试和验证失败在日志中可见，从而降低未来的调试成本。阶段 2 将轮询回退替换为目录聚类 (directory-clustering)，以便在 LLM 失败时保持局部性。阶段 3 将第 2 阶段的文件分配重写为可复用缓存的分批处理，使 LLM 调用能够真正成功。质量磨光（大纲锚点、多页面分配）被延期并在阶段 4 中记录。

**技术栈:** Python 3.12, asyncio, pytest (asyncio_mode=auto), Anthropic/OpenAI/Gemini/Ollama SDKs。

**规范:** 此计划。无单独的设计文档。

**不在范围内 (明确延期):**
- 针对真实仓库独立进行每个阶段的端到端验证（在当前的测试预算下过于昂贵 — 依赖日志）。
- 层 C1 大纲锚点（README 章节、包级文档字符串、提升的聚类）。
- 层 C2 多页面文件分配（架构更改为 `primary_page` + `secondary_pages`）。
- 调用具有记录固件的流水线的独立每阶段测试套件。

---

## 文件结构

**修改的文件:**
- `worker/pipeline/wiki_planner.py` — 主要目标。添加日志记录，替换轮询，将 `_assign_files` 重写为分批处理。
- `worker/pipeline/page_outline.py` — 添加每次重试的日志记录。
- `worker/pipeline/page_draft.py` — 添加记录器 + 记录草稿失败。
- `worker/pipeline/fact_check.py` — 添加每次重试的日志记录。
- `worker/pipeline/page_generator.py` — 记录修订回退路径。
- `CLAUDE.md` — 记录新的可观察性规范 + 延期工作。

**创建的文件:**
- `worker/pipeline/pipeline_logging.py` — 共享的 `log_validation_retry` / `log_final_failure` 助手，使日志在整个流水线中具有一致的形状。
- `tests/worker/test_pipeline_logging.py` — 针对日志助手的单元测试。
- `tests/worker/test_directory_cluster_fallback.py` — 针对新的保持局部性回退的单元测试。
- `tests/worker/test_assign_files_batched.py` — 针对阶段 3 分批分配的单元测试。

---

## 阶段 1 — 全流水线可观察性

**目标:** 计划器、页面大纲、页面草稿、事实检查和修订路径中的每次重试和每次验证失败都应在正确的级别生成结构化日志。不再静默吞掉 `ValueError` / `json.JSONDecodeError`。回退执行以 `ERROR` 级别记录。

### 任务 1: 创建共享日志助手
(步骤与代码见英文版)

### 任务 2: 将日志接入 `wiki_planner._generate_outline`
(步骤与代码见英文版)

### 任务 3: 将日志接入 `wiki_planner._assign_files`
(步骤与代码见英文版)

### 任务 4: 将日志接入 `page_outline`, `page_draft`, `fact_check` 和 `page_generator`
(步骤与代码见英文版)

---

## 阶段 2 — 目录聚类回退

**目标:** 将 `_assign_files` 中的轮询回退替换为保持局部性的目录聚类算法。当 LLM 失败时，回退到“共享一个目录的文件属于其标题/目的最匹配该目录的页面”，而不是“文件在所有页面之间按字母顺序交错”。

### 任务 6: 实施 `_directory_cluster_assign`
(步骤与代码见英文版)

---

## 阶段 3 — 带有提示词缓存的分批文件分配

**目标:** 重写 `_assign_files`，将文件列表拆分为 ≤40 个文件的批次，并为每个批次调用一次 LLM。大型静态上下文（大纲 + 文件摘要 + 依赖信息）放置在可缓存的 **system** 段中，因此只有第一批支付全部费用；后续批次命中 Anthropic 的临时缓存。接受部分成功 — 只有真正未分配的文件才会触发重试。

### 任务 8: 添加 `_build_batch_assignment_system` 和 `_build_batch_assignment_user` 提示词构建器
(步骤与代码见英文版)

### 任务 9: 实施 `_assign_files_in_batches` 核心循环
(步骤与代码见英文版)

---

## 阶段 4 — 文档与交付

### 任务 13: 更新 CLAUDE.md 中的新规范和延期工作
(步骤见英文版)
