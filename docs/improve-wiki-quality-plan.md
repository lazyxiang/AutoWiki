# Plan: Improve Wiki Generation Quality

## Context

AutoWiki currently generates basic wiki pages with minimal structure. Compared to DeepWiki/CodeWiki, the generated content lacks:
- **Rich hierarchy** — planner only gets a flat module tree grouped by top-level directory
- **Descriptions** — page specs have no description field to guide content generation
- **Dependency awareness** — no import/dependency graph extraction; modules are grouped naively
- **Source annotations** — generated pages don't cite file:line ranges
- **Architecture diagrams** — no Mermaid diagram generation or rendering
- **Entity context** — AST-extracted entities (classes, functions, line ranges) are not passed to the planner or page generator

This plan adds 6 improvements across the pipeline to produce richer, more comprehensive wiki output.

---

## 1. New Module: Dependency Graph Extraction

**New file: `worker/pipeline/dependency_graph.py`**

Extract import relationships using regex (simpler than Tree-Sitter for imports, works across all languages):

```python
@dataclass
class DependencyGraph:
    edges: dict[str, list[str]]        # file → [imported files/modules]
    clusters: list[list[str]]          # groups of tightly-connected files
    external_deps: dict[str, list[str]] # file → [external packages]
```

**Language-specific import patterns:**
- Python: `import X`, `from X import Y`
- JS/TS: `import ... from 'X'`, `require('X')`
- Go: `import "X"`
- Java/Kotlin: `import X.Y.Z`
- Rust: `use X::Y`, `mod X`
- C/C++: `#include "X"` (local only)

**Clustering:** Use simple connected-component analysis — files that import each other heavily belong in the same wiki page. This feeds into the planner for better grouping.

**Files:** `worker/pipeline/dependency_graph.py` (new)

---

## 2. Enhanced AST Analysis

**File: `worker/pipeline/ast_analysis.py`**

### 2a. Extract docstrings and signatures
Add to `_extract_entities()`:
- For Python: extract the first string literal child of class/function nodes (docstring)
- For JS/TS/Java: extract preceding comment nodes
- Extract function parameter lists as signature strings

Entity dict becomes:
```python
{
    "type": "class" | "function",
    "name": str,
    "start_line": int,
    "end_line": int,
    "signature": str | None,    # NEW: e.g. "def greet(name: str) -> str"
    "docstring": str | None,    # NEW: first 200 chars of docstring
}
```

### 2b. Enhanced module tree
New function `build_enhanced_module_tree()` that returns:
```python
{
    "path": "src/auth",
    "files": [...],
    "entities": [{"name": "User", "type": "class", ...}, ...],
    "file_count": 5,
    "class_count": 3,
    "function_count": 12,
    "summary": "Contains User, AuthService, validate_token, ..."  # top entities listed
}
```

This gives the planner much richer context about what each module contains.

---

## 3. Enhanced RAG Indexer

**File: `worker/pipeline/rag_indexer.py`**

### 3a. Richer chunk metadata
Add line number ranges and entity context to metadata:
```python
{
    "text": chunk_text,
    "file": relative_path,
    "chunk_idx": int,
    "start_line": int,      # NEW
    "end_line": int,         # NEW
    "entity": str | None,    # NEW: enclosing class/function name if known
}
```

Compute `start_line`/`end_line` by tracking character offsets during chunking.

### 3b. Entity-aware chunking
New function `chunk_file_with_entities()` that uses AST entity boundaries:
- If a function/class fits in one chunk, keep it whole
- Only split entities that exceed chunk_size
- This ensures RAG retrieval returns complete, coherent code blocks

---

## 4. Enhanced Wiki Planner

**File: `worker/pipeline/wiki_planner.py`**

### 4a. Add `description` field to PageSpec
```python
@dataclass
class PageSpec:
    title: str
    slug: str
    modules: list[str]
    parent_slug: str | None = None
    description: str | None = None    # NEW
```

Update `_PLAN_SCHEMA` to include `description` as a required string field.

### 4b. Enriched planner prompt
The new `_build_prompt()` receives and includes:
- **README excerpt** (first 2000 chars) — gives the LLM repo-level context
- **Enhanced module tree** with entity summaries (class/function names per module)
- **Dependency graph summary** — which modules depend on which
- **Dependency clusters** — suggested groupings based on import analysis

### 4c. Improved system prompt with chain-of-thought
```
You are a senior technical documentation architect creating a comprehensive wiki
for a software repository. Analyze the codebase structure, dependencies, and key
entities to produce a well-organized hierarchical wiki plan.

Think step-by-step:
1. Identify the main architectural components and their roles
2. Group tightly-coupled modules into coherent pages
3. Create a clear hierarchy (Overview → subsystem pages → detail pages)
4. Write a concise description for each page explaining what it covers and why

Each page should have a clear PURPOSE — not just list files, but explain a concept,
component, or workflow. Aim for 2-3 levels of hierarchy for repos with 5+ modules.

Output ONLY valid JSON.
```

### 4d. Updated user prompt template
```
Repository: {repo_name}

README (excerpt):
{readme_content}

Module tree with entities:
{enhanced_module_tree_json}

Dependency graph:
{dependency_summary}

Suggested clusters (based on import analysis):
{clusters_json}

Create a hierarchical wiki plan. Guidelines:
- 5–15 pages depending on repository complexity
- Each page needs: title, slug, modules, parent_slug (for nesting), description (1-2 sentences explaining the page's purpose)
- MUST include an "Overview" page as root (parent_slug: null) covering architecture and project purpose
- Group related modules using the dependency clusters as a guide
- Create 2-3 levels of hierarchy: Overview → subsystem → detail pages
- Page titles should describe concepts/components, not just directory names
- Description should explain WHAT the page covers and WHY it matters

Output JSON matching this schema:
{schema}
```

---

## 5. Enhanced Page Generator

**File: `worker/pipeline/page_generator.py`**

### 5a. Richer generation prompt
Updated `_build_page_prompt()` receives:
- `spec.description` — focuses the LLM on what this page should cover
- Dependency context — what this module depends on and what depends on it
- Entity details — class/function signatures and docstrings for modules on this page

### 5b. New system prompt
```
You are a senior technical writer creating comprehensive wiki documentation for a
software repository. Write accurate, well-structured pages grounded in the provided
source code.

Rules:
- Every technical claim must be traceable to the provided source code
- After each section, add a "Source" annotation in italics: *Source: path/to/file.py:10-45*
- Include Mermaid diagrams where they aid understanding (architecture, class relationships, data flow)
- Use ```mermaid code blocks for diagrams
- Do not invent APIs or features not present in the code
- Write for developers who are new to this codebase
```

### 5c. New user prompt template
```
Repository: {repo_name}
Page: {spec.title}
Purpose: {spec.description}
Modules: {modules_list}

Dependencies:
- This module depends on: {deps_out}
- Depended on by: {deps_in}

Key entities in these modules:
{entity_details}

Relevant source code (with file paths and line numbers):
{context_with_line_numbers}

Write a comprehensive wiki page for "{spec.title}". Structure:

## Overview
Brief description of this component's role and purpose.

## Architecture
Include a Mermaid diagram showing how this component relates to others.
(Use ```mermaid blocks. Choose: flowchart, classDiagram, or sequenceDiagram as appropriate.)

## Key Components
For each major class/function:
- What it does
- Its interface/signature
- How it's used
After each subsection, cite the source: *Source: file.py:line-line*

## Dependencies & Interactions
How this module connects to the rest of the codebase.

## Source Files
List all files covered by this page with brief descriptions.

Output Markdown only.
```

### 5d. Multi-query RAG retrieval
Instead of a single query (title + modules), generate multiple queries:
1. Page title + description
2. Each entity name in the modules
3. Module interaction terms

Merge and deduplicate results, increase `top_k` from 8 to 15.

### 5e. Source annotations in chunk context
Format RAG chunks with line numbers:
```
File: src/auth/handler.py (lines 15-42)
```python
class AuthHandler:
    ...
```
```

---

## 6. Frontend Mermaid Support

**File: `web/components/WikiPage.tsx`**

Add Mermaid diagram rendering for ```mermaid``` code blocks:
- Use `mermaid` npm package
- Create a custom `MermaidBlock` component that renders on client side
- Pass it as a custom code renderer to ReactMarkdown
- Use `useEffect` + `mermaid.render()` for dynamic rendering

**File: `web/package.json`** — add `mermaid` dependency

---

## 7. Pipeline Orchestration Updates

**File: `worker/jobs.py`**

Update `run_full_index()` to pass new data between stages:

```
Stage 1: Ingestion (5-20%) — also extract README content
Stage 2: AST Analysis (20-30%) — build_enhanced_module_tree with entities
Stage 2b: Dependency Graph (30-40%) — extract imports, build graph, compute clusters (NEW)
Stage 3: RAG Indexer (40-55%) — entity-aware chunking with line numbers
Stage 4: Wiki Planner (55-65%) — receives: enhanced_module_tree + dep_graph + README
Stage 5: Page Generator (65-100%) — receives: page spec with description + dep context + entities
```

**File: `worker/pipeline/ingestion.py`** — add `extract_readme()` function:
```python
def extract_readme(root: Path) -> str | None:
    for name in ["README.md", "README.rst", "README.txt", "README"]:
        p = root / name
        if p.exists():
            return p.read_text(errors="replace")[:3000]
    return None
```

---

## 8. Database Schema Update

**File: `shared/models.py`**

Add `description` column to WikiPage:
```python
description: Mapped[str | None] = mapped_column(Text, nullable=True)
```

---

## Files to Modify (in order)

| # | File | Change |
|---|------|--------|
| 1 | `worker/pipeline/dependency_graph.py` | **NEW** — import extraction + clustering |
| 2 | `worker/pipeline/ast_analysis.py` | Add docstrings, signatures, enhanced module tree |
| 3 | `worker/pipeline/rag_indexer.py` | Line numbers in metadata, entity-aware chunking |
| 4 | `worker/pipeline/ingestion.py` | Add `extract_readme()` |
| 5 | `worker/pipeline/wiki_planner.py` | Description field, enriched prompts, better system prompt |
| 6 | `worker/pipeline/page_generator.py` | Source annotations, diagrams, multi-query RAG, richer prompts |
| 7 | `worker/jobs.py` | Wire new stages, pass enhanced data |
| 8 | `shared/models.py` | Add description to WikiPage |
| 9 | `web/components/WikiPage.tsx` | Mermaid rendering |
| 10 | `web/package.json` | Add mermaid dependency |
| 11 | `tests/conftest.py` | Update mock_llm for new schema |
| 12 | `tests/worker/test_dependency_graph.py` | **NEW** — tests for dep extraction |
| 13 | `tests/worker/test_wiki_planner.py` | Update for description field |
| 14 | `tests/worker/test_page_generator.py` | Update for new prompt structure |
| 15 | `tests/worker/test_ast_analysis.py` | Tests for docstrings, signatures |
| 16 | `tests/worker/test_rag_indexer.py` | Tests for entity-aware chunking |

---

## Verification

1. **Unit tests**: `pytest tests/ --ignore=tests/e2e` — all existing + new tests pass
2. **Integration test**: `pytest tests/test_integration.py` — full pipeline with fixtures
3. **Manual check**: Run against the fixture repo and verify:
   - Page plan has descriptions and hierarchy
   - Generated pages include Mermaid diagrams (Mermaid code blocks)
   - Source annotations present (e.g., *Source: models.py:5-20*)
   - Dependency information included in pages
4. **Frontend**: Verify Mermaid renders in the web UI (requires `npm run dev`)
