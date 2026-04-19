# 文档重组与翻译设计

**目标：** 清理项目根目录和 `docs/` 目录，同时为所有设计、配置和 API 资料提供全面的中文文档。

**架构：**
- **重组：** 将旧有的/内部的规划文档移动到嵌套的 `docs/design/claude-plan/` 结构中。
- **简化：** 从 `README.md` 中剥离繁重的技术细节（配置、CLI、API），放入 `docs/` 目录下专门的双语文件中。
- **翻译：** 使用 `-zh.md` 后缀为每个关键文档文件提供对应的中文版本。

## 1. 目录结构变更
- **新目录：** `docs/design/claude-plan/`
- **移动文件（从 `docs/` 移动到 `docs/design/claude-plan/`）：**
  - `2026-03-25-improve-wiki-quality-plan.md`
  - `2026-03-25-improve-wiki-ux-plan.md`
  - `2026-03-27-improve-llm-retry-plan.md`
  - `2026-03-27-improve-logging-plan.md`
  - `2026-03-28-pipeline-refactoring-plan.md`
  - `2026-03-28-simplify-code-plan.md`
  - `2026-04-03-wiki-generation-language-plan.md`
  - `2026-04-15-wiki-planner-robustness-investigation.md`

## 2. README.md 重构
- **移除章节：** `Configuration`（配置）、`CLI`、`API`。
- **新的双语文档（位于 `docs/`）：**
  - `configuration.md` / `configuration-zh.md`
  - `cli.md` / `cli-zh.md`
  - `api.md` / `api-zh.md`
- **README 翻译：** 基于新简化的 `README.md` 创建 `README-zh.md`。

## 3. 翻译范围
以下位置的所有文件都将创建 `-zh.md` 版本：
- `docs/design/claude-plan/` (8 个文件)
- `docs/superpowers/plans/` (9 个文件)
- `docs/superpowers/specs/` (4 个文件)
- `docs/architecture-guide.md`
- `README.md`

## 4. 实施策略
- **第 1 步：** 创建目录并移动文件。
- **第 2 步：** 将 `README.md` 中的章节提取到新文件中。
- **第 3 步：** 进行翻译（英译中）。
- **第 4 步：** 验证文件存在性及内容。
