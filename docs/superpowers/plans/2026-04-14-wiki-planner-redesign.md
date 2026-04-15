# Wiki Planner Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the Wiki Planner to support "Primary vs. Reference" file assignments and improve logical grouping of sequential modules (like pipelines).

**Architecture:** We will update `WikiPageSpec` to distinguish between `primary_files` (one-to-one mapping for code) and `reference_files` (one-to-many context). LLM prompts and validation logic will be updated to enforce these rules and prioritize functional cohesion (e.g., pipeline stages grouped under parent hubs).

**Tech Stack:** Python (Pydantic, Dataclasses), LLM (Anthropic/Google/OpenAI).

---

### Task 1: Update `WikiPageSpec` and `WikiPlan` Data Models

**Files:**
- Modify: `worker/pipeline/wiki_planner.py`

- [ ] **Step 1: Update `WikiPageSpec` fields**
    Rename `files` to `primary_files` and add `reference_files`.
    ```python
    @dataclass
    class WikiPageSpec:
        # ...
        primary_files: list[str] = field(default_factory=list)
        reference_files: list[str] = field(default_factory=list)
    ```

- [ ] **Step 2: Update `WikiPlan` serialization methods**
    Update `to_wiki_json`, `to_internal_json`, and `to_api_structure` to reflect the changes.
    `to_internal_json` should now include both `primary_files` and `reference_files`.

- [ ] **Step 3: Run existing tests to verify breakage**
    Run: `pytest tests/worker/test_wiki_planner.py`
    Expected: Failures due to missing `files` attribute.

- [ ] **Step 4: Fix `WikiPageSpec` backward compatibility (optional but recommended)**
    Add a `@property` for `files` that returns `primary_files` to ease migration of existing tests and logic during transition.
    ```python
    @property
    def files(self) -> list[str]:
        return self.primary_files
    @files.setter
    def files(self, value: list[str]):
        self.primary_files = value
    ```

- [ ] **Step 5: Commit**
    ```bash
    git add worker/pipeline/wiki_planner.py
    git commit -m "chore: update WikiPageSpec data model with primary and reference files"
    ```

---

### Task 2: Update Phase 1 (Outline) LLM Logic

**Files:**
- Modify: `worker/pipeline/wiki_planner.py`

- [ ] **Step 1: Update `_OUTLINE_SCHEMA`**
    No changes needed to the schema as it only covers titles/purposes/parents.

- [ ] **Step 2: Update `_build_outline_prompt` instructions**
    Add instructions for "Functional Cohesion" and "Sequential Flow Identification".
    - Instructions to recognize sequential modules (Pipelines, Workflows).
    - Directive to group these under a common parent.
    - Directive to treat parents as "Architectural Hubs" and children as "Implementation Details".

- [ ] **Step 3: Commit**
    ```bash
    git add worker/pipeline/wiki_planner.py
    git commit -m "feat: enhance outline prompt with pipeline and hub-leaf logic"
    ```

---

### Task 3: Update Phase 2 (Assignment) LLM Logic

**Files:**
- Modify: `worker/pipeline/wiki_planner.py`

- [ ] **Step 1: Update `_ASSIGNMENT_SCHEMA`**
    Change from `page_title` to separate `primary_page` and `reference_pages` (list).
    ```python
    _ASSIGNMENT_SCHEMA = {
        "type": "object",
        "properties": {
            "assignments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "primary_page": {"type": "string"},
                        "reference_pages": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["file", "primary_page"],
                },
            }
        },
        "required": ["assignments"],
    }
    ```

- [ ] **Step 2: Update `_build_assignment_prompt` instructions**
    - **Code Home Rule**: Every source file must have exactly one `primary_page`.
    - **Contextual Reuse Rule**: Files can be assigned to multiple `reference_pages`.
    - Hub pages should reference their children's primary files.

- [ ] **Step 3: Update `_assign_files` function**
    Update the logic to parse the new schema and populate `primary_files` and `reference_files`.

- [ ] **Step 4: Commit**
    ```bash
    git add worker/pipeline/wiki_planner.py
    git commit -m "feat: update assignment logic for primary/reference mapping"
    ```

---

### Task 4: Update Validation Logic

**Files:**
- Modify: `worker/pipeline/wiki_planner.py`

- [ ] **Step 1: Update `_validate_assignments`**
    Enforce "Primary Coverage" (every code file belongs to exactly one page).
    Ensure `reference_files` are valid paths.

- [ ] **Step 2: Update `validate_wiki_plan`**
    Update the orphan collection logic to append unassigned files to `primary_files` of the Overview/first page.

- [ ] **Step 3: Update `_fallback_plan`**
    Ensure it uses `primary_files` correctly.

- [ ] **Step 4: Commit**
    ```bash
    git add worker/pipeline/wiki_planner.py
    git commit -m "feat: implement strict primary-reference validation"
    ```

---

### Task 5: Integrate with Page Generator

**Files:**
- Modify: `worker/pipeline/page_generator.py`

- [ ] **Step 1: Update `generate_page` RAG logic**
    Combine `primary_files` and `reference_files` for embedding queries.
    ```python
    # Pass 1: Outline
    all_files = spec.primary_files + spec.reference_files
    queries = [f"{spec.title} {' '.join(all_files[:10])}"]
    ```

- [ ] **Step 2: Update Source Files Table**
    Distinguish between "Primary Files" and "Reference Files" in the generated table.
    ```python
    def _append_source_files_table(content: str, spec: WikiPageSpec) -> str:
        # ... logic to show Primary and Reference sections ...
    ```

- [ ] **Step 3: Commit**
    ```bash
    git add worker/pipeline/page_generator.py
    git commit -m "feat: integrate primary and reference files into page generation"
    ```

---

### Task 6: Final Verification and Tests

**Files:**
- Create: `tests/worker/test_wiki_planner_redesign.py`
- Modify: `tests/worker/test_wiki_planner.py`

- [ ] **Step 1: Update existing tests**
    Update mock data and assertions in `test_wiki_planner.py` to match new schema.

- [ ] **Step 2: Create a new integration test for Pipeline grouping**
    Mock a repo with a pipeline structure and verify that the planner groups them under a parent.

- [ ] **Step 3: Run all tests**
    Run: `pytest tests/worker/test_wiki_planner.py tests/worker/test_page_generator.py`
    Expected: PASS

- [ ] **Step 4: Commit**
    ```bash
    git add tests/worker/
    git commit -m "test: verify wiki planner redesign with comprehensive tests"
    ```
