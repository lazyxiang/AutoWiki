# Documentation Reorganization and Translation Design

**Goal:** Clean up the project root and `docs/` directory while providing comprehensive Chinese documentation for all design, configuration, and API materials.

**Architecture:**
- **Reorganization:** Move legacy/internal planning documents into a nested `docs/design/claude-plan/` structure.
- **Simplification:** Strip heavy technical details (Config, CLI, API) from `README.md` into dedicated, bilingual files in `docs/`.
- **Translation:** Provide a Chinese counterpart for every key documentation file using the `-zh.md` suffix.

## 1. Directory Structure Changes
- **New Directory:** `docs/design/claude-plan/`
- **Move Files (from `docs/` to `docs/design/claude-plan/`):**
  - `2026-03-25-improve-wiki-quality-plan.md`
  - `2026-03-25-improve-wiki-ux-plan.md`
  - `2026-03-27-improve-llm-retry-plan.md`
  - `2026-03-27-improve-logging-plan.md`
  - `2026-03-28-pipeline-refactoring-plan.md`
  - `2026-03-28-simplify-code-plan.md`
  - `2026-04-03-wiki-generation-language-plan.md`
  - `2026-04-15-wiki-planner-robustness-investigation.md`

## 2. README.md Refactoring
- **Removed Sections:** `Configuration`, `CLI`, `API`.
- **New Bilingual Documentation (in `docs/`):**
  - `configuration.md` / `configuration-zh.md`
  - `cli.md` / `cli-zh.md`
  - `api.md` / `api-zh.md`
- **README Translation:** Create `README-zh.md` based on the newly simplified `README.md`.

## 3. Translation Scope
All files in the following locations will have a `-zh.md` version created:
- `docs/design/claude-plan/` (8 files)
- `docs/superpowers/plans/` (9 files)
- `docs/superpowers/specs/` (4 files)
- `docs/architecture-guide.md`
- `README.md`

## 4. Implementation Strategy
- **Step 1:** Create directories and move files.
- **Step 2:** Extract `README.md` sections into new files.
- **Step 3:** Perform translations (English to Chinese).
- **Step 4:** Verify file existence and content.
