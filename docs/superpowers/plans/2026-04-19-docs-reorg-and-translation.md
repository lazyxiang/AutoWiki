# Documentation Reorganization and Translation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the `docs/` directory, strip detailed technical sections from `README.md` into separate files, and provide Chinese translations for all key documentation.

**Architecture:**
- **Reorganization:** Move internal plans to `docs/design/claude-plan/`.
- **Extraction:** Move Configuration, CLI, and API sections from `README.md` to standalone files in `docs/`.
- **Bilingual Support:** Create `-zh.md` versions for all documents.

**Tech Stack:** Bash (for file operations), Markdown, LLM (for translation).

---

### Task 1: Setup and Migration

**Files:**
- Modify: `docs/` (reorganize files)
- Create: `docs/design/claude-plan/`

- [ ] **Step 1: Create new directory**
Run: `mkdir -p docs/design/claude-plan`

- [ ] **Step 2: Move planning files**
Run:
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

- [ ] **Step 3: Commit migration**
Run: `git add docs/design/claude-plan && git commit -m "docs: move legacy plans to design/claude-plan"`

---

### Task 2: README.md Refactoring and Extraction

**Files:**
- Modify: `README.md`
- Create: `docs/configuration.md`, `docs/cli.md`, `docs/api.md`

- [ ] **Step 1: Extract Configuration section**
Create `docs/configuration.md` with the content of the "Configuration" section from `README.md`.

- [ ] **Step 2: Extract CLI section**
Create `docs/cli.md` with the content of the "CLI" section from `README.md`.

- [ ] **Step 3: Extract API section**
Create `docs/api.md` with the content of the "API" section from `README.md`.

- [ ] **Step 4: Clean up README.md**
Remove the "Configuration", "CLI", and "API" sections from `README.md`.

- [ ] **Step 5: Commit changes**
Run: `git add README.md docs/configuration.md docs/cli.md docs/api.md && git commit -m "docs: extract technical details from README to standalone docs"`

---

### Task 3: Core Translation

**Files:**
- Create: `README-zh.md`, `docs/configuration-zh.md`, `docs/cli-zh.md`, `docs/api-zh.md`, `docs/architecture-guide-zh.md`

- [ ] **Step 1: Translate README.md**
- [ ] **Step 2: Translate configuration.md**
- [ ] **Step 3: Translate cli.md**
- [ ] **Step 4: Translate api.md**
- [ ] **Step 5: Translate architecture-guide.md**
- [ ] **Step 6: Commit translations**
Run: `git add README-zh.md docs/*-zh.md && git commit -m "docs: add Chinese translations for core documentation"`

---

### Task 4: Design and Superpowers Translation

**Files:**
- Create: `docs/design/claude-plan/*-zh.md`, `docs/superpowers/plans/*-zh.md`, `docs/superpowers/specs/*-zh.md`

- [ ] **Step 1: Translate docs/design/claude-plan/ files (8 files)**
- [ ] **Step 2: Translate docs/superpowers/plans/ files (9 files)**
- [ ] **Step 3: Translate docs/superpowers/specs/ files (5 files)**
- [ ] **Step 4: Commit all remaining translations**
Run: `git add docs/**/*.md && git commit -m "docs: complete Chinese translations for all design and plan documents"`
