# Wiki Planner Redesign: Dual Association Architecture

## 1. Problem Statement
The current `WikiPlanner` (Stage 5) frequently produces scattered and inaccurate wiki structures:
1. **Fragmentation**: Sequential logic (e.g., multi-stage pipelines) is often split into unrelated top-level pages.
2. **Inaccurate Assignment**: Crucial files (like `wiki_planner.py`) are sometimes assigned to irrelevant pages while being missed on the most logical ones.
3. **Rigid Mapping**: The strict "exactly one page per file" rule prevents files from being used as context on multiple relevant pages (e.g., a parent "Hub" page needing to reference its children's implementation).
4. **Content Quality**: Inaccurate file-to-page mapping leads to the `page_generator` (Stage 6) producing hallucinations or vague summaries due to lack of specific source code context.

## 2. Proposed Solution: Dual Association
We will evolve the `WikiPageSpec` and the two-phase LLM planning logic to support a **Primary vs. Reference** file distribution strategy.

### 2.1 Updated Data Model
Modify `WikiPageSpec` in `worker/pipeline/wiki_planner.py`:
- **`primary_files` (list[str])**: 
    - The "Home" for a source code file. 
    - Every source code file in the repository must be assigned as a `primary_file` to **exactly one** page.
    - Focus: Detailed AST analysis and implementation breakdown.
- **`reference_files` (list[str])**:
    - "Contextual Support" for the page.
    - Any file (code, config, docs) can be a `reference_file` on **multiple pages**.
    - Focus: High-level architectural context and cross-page synthesis.

### 2.2 Refined Planning Phases

#### Phase 1: Outline Generation (Structural Strategy)
Update prompts to prioritize **Functional Cohesion**:
- **Execution Flow Identification**: Instructions to recognize sequential modules (Pipelines, Workflows, Life-cycles) and group them under a common parent.
- **Hierarchy Mandate**: Parent pages act as "Architectural Hubs" (Overview/How it fits), while leaf pages act as "Implementation Details" (Specifics/How it works).
- **Sequential Ordering**: Use titles (e.g., "Stage 1:", "Part 2:") and logical descriptions to preserve order.

#### Phase 2: Assignment Generation (File Distribution)
Update prompts to implement **Source Code Ownership**:
- **Code Home Rule**: Every `.py`, `.ts`, etc., must have its most relevant leaf page as its `primary_home`.
- **Contextual Reuse Rule**: Hub/Parent pages should list the `primary_files` of their children as `reference_files` to provide a holistic view.
- **Entry Point Bonus**: High-level orchestration files (e.g., `main.py`) can be `primary_files` for the top-level Overview or Hub pages.

## 3. Implementation Details

### 3.1 `WikiPageSpec` and `WikiPlan`
- Rename `files` field to `primary_files` and add `reference_files`.
- Update serialization methods (`to_wiki_json`, `to_internal_json`, `to_api_structure`) to handle the new fields.
- Ensure backward compatibility or perform a clean migration of existing `wiki_plan.json` files.

### 3.2 Validation Logic
Implement strict constraints in `validate_wiki_plan`:
1. **Primary Coverage**: All source files must appear exactly once in a `primary_files` list across all pages.
2. **No Duplication**: Prevent a file from being a `primary_file` on more than one page.
3. **Path Integrity**: All files in both `primary_files` and `reference_files` must exist in the repository.
4. **Fallback**: Auto-assign unassigned code files to the "Overview" or the first page as `primary_files`.

### 3.3 Page Generator Integration
- Update `worker/pipeline/page_generator.py` to utilize both `primary_files` and `reference_files` for RAG context.
- Ensure the prompt for Stage 6 (Drafting) understands the difference between the two (e.g., "Explain implementation for Primary files, use Reference files for context").

## 4. Success Criteria
- **Coherent Structure**: Pipelines (like `worker/pipeline/`) are grouped under a single parent with stages in order.
- **Accurate Context**: `wiki_planner.py` is the `primary_file` for its specific page and a `reference_file` for the parent "Generation Engine" page.
- **No Orphaned Code**: 100% of source code files have a documentation "home."
- **Improved Wiki Quality**: Higher factual accuracy in generated Markdown due to precise file-to-topic mapping.
