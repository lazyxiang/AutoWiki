# 文档重组与翻译实施计划

> **致智能代理：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 来逐项任务实施此计划。步骤使用复选框（`- [ ]`）语法进行跟踪。

**目标：** 重构 `docs/` 目录，将 `README.md` 中的详细技术章节拆分为独立文件，并为所有关键文档提供中文翻译。

**架构：**
- **重组：** 将内部计划移动到 `docs/design/claude-plan/`。
- **提取：** 将 `README.md` 中的配置（Configuration）、CLI 和 API 章节移动到 `docs/` 下的独立文件中。
- **双语支持：** 为所有文档创建 `-zh.md` 版本。

**技术栈：** Bash（用于文件操作）、Markdown、LLM（用于翻译）。

---

### 任务 1：设置与迁移

**相关文件：**
- 修改：`docs/`（重组文件）
- 创建：`docs/design/claude-plan/`

- [ ] **步骤 1：创建新目录**
运行：`mkdir -p docs/design/claude-plan`

- [ ] **步骤 2：移动计划文件**
运行：
```bash
mv docs/2026-03-25-improve-wiki-quality-plan.md docs/design/claude-plan/
mv docs/2026-03-25-improve-wiki-ux-plan.md docs/design/claude-plan/
mv docs/2026-03-27-improve-llm-retry-plan.md docs/design/claude-plan/
mv docs/2026-03-27-improve-logging-plan.md docs/design/claude-plan/
mv docs/2026-03-28-pipeline-refactoring-plan.md docs/design/claude-plan/
mv docs/2026-03-28-simplify-code-plan.md docs/design/claude-plan/
mv docs/2026-04-03-wiki-generation-language-plan.md docs/design/claude-plan/
mv docs/2026-04-15-wiki-planner-robustness-investigation.md docs/design/claude-plan/
```

- [ ] **步骤 3：提交迁移**
运行：`git add docs/design/claude-plan && git commit -m "docs: move legacy plans to design/claude-plan"`

---

### 任务 2：README.md 重构与提取

**相关文件：**
- 修改：`README.md`
- 创建：`docs/configuration.md`、`docs/cli.md`、`docs/api.md`

- [ ] **步骤 1：提取配置章节**
创建 `docs/configuration.md`，内容取自 `README.md` 中的“Configuration”部分。

- [ ] **步骤 2：提取 CLI 章节**
创建 `docs/cli.md`，内容取自 `README.md` 中的“CLI”部分。

- [ ] **步骤 3：提取 API 章节**
创建 `docs/api.md`，内容取自 `README.md` 中的“API”部分。

- [ ] **步骤 4：清理 README.md**
从 `README.md` 中移除“Configuration”、“CLI”和“API”部分。

- [ ] **步骤 5：提交更改**
运行：`git add README.md docs/configuration.md docs/cli.md docs/api.md && git commit -m "docs: extract technical details from README to standalone docs"`

---

### 任务 3：核心翻译

**相关文件：**
- 创建：`README-zh.md`、`docs/configuration-zh.md`、`docs/cli-zh.md`、`docs/api-zh.md`、`docs/architecture-guide-zh.md`

- [ ] **步骤 1：翻译 README.md**
- [ ] **步骤 2：翻译 configuration.md**
- [ ] **步骤 3：翻译 cli.md**
- [ ] **步骤 4：翻译 api.md**
- [ ] **步骤 5：翻译 architecture-guide.md**
- [ ] **步骤 6：提交翻译**
运行：`git add README-zh.md docs/*-zh.md && git commit -m "docs: add Chinese translations for core documentation"`

---

### 任务 4：设计与 Superpowers 翻译

**相关文件：**
- 创建：`docs/design/claude-plan/*-zh.md`、`docs/superpowers/plans/*-zh.md`、`docs/superpowers/specs/*-zh.md`

- [ ] **步骤 1：翻译 docs/design/claude-plan/ 文件（8 个文件）**
- [ ] **步骤 2：翻译 docs/superpowers/plans/ 文件（9 个文件）**
- [ ] **步骤 3：翻译 docs/superpowers/specs/ 文件（5 个文件）**
- [ ] **步骤 4：提交所有剩余翻译**
运行：`git add docs/**/*.md && git commit -m "docs: complete Chinese translations for all design and plan documents"`
