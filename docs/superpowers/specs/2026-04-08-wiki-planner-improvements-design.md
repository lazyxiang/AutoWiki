# Wiki Planner & Generation Improvements — Design Spec

**Status:** COMPLETE (2026-04-12)
**Project:** AutoWiki

---

## 1. Goal

Transform the wiki generation pipeline into a hierarchical multi-agent generator with richer context, two-phase planning, and bottom-up page synthesis.

---

## 2. Key Architecture Changes

### 2.1 FileAnalysis (AST Analysis)
- SINGLE pass extraction of all entities (classes, functions, signatures, docstrings).
- Importance-ranked file summaries for LLM prompt context (capped at 200 files).
- Dependency graph extraction at the file level.

### 2.2 Two-Phase Wiki Planning
1. **Outline Phase:** LLM generates hierarchical page titles and purposes.
2. **Assignment Phase:** LLM assigns specific files to each page in the outline.

### 2.3 Bottom-Up Generation
- Pages are generated deepest-first in the hierarchy.
- Parent pages receive the generated content of their children to synthesize high-level narratives.
- Architecture diagrams are generated per-page by the LLM within Stage 6.

---

## 3. Data Flow

- **Stage 1 (Ingestion):** Shallow clone + SHA diff.
- **Stage 2 (AST):** Unified `FileAnalysis`.
- **Stage 3 (Deps):** Import extraction + clustering.
- **Stage 4 (RAG):** Embedding (skippable with `reuse_index=True`).
- **Stage 5 (Planner):** `WikiPlan` (Outline + Files).
- **Stage 6 (Generator):** Batched LLM generation of pages (recursive synthesis).

---

## Status: Implemented (Wiki Optimization Phase)

This plan was fully implemented in the Wiki Optimization Phase. The `diagram_synthesis.py` module (Stage 7) was removed as it became redundant with per-page diagrams.
