# Plan: Simplify Code & Remove Redundancy

## Context

The codebase accumulated duplication across Phase 1 and Phase 2. The biggest problem was `worker/jobs.py` — two 400-line orchestrator functions (`run_full_index`, `run_refresh_index`) sharing almost identical pipeline stages. This plan extracts stage-level helpers, deduplicates LLM provider logic, simplifies the API queue, extracts a frontend utility, and DRYs the Docker config.

---

## 1. Decompose `worker/jobs.py` into stage helpers

Extract private helper functions at module level. Both orchestrators call them — each function becomes a readable ~120-line flow instead of a 400-line monolith.

### Helpers extracted (OUTDATED: Some removed/changed in Refactoring Plan)

| Helper | Replaces | Status |
|---|---|---|
| `_make_on_retry(db_path, job_id)` | Duplicate `_on_retry` closure | **Implemented** |
| `_build_file_entities(files, clone_root)` | Per-file AST entity loop | **Removed** (see Refactoring Plan) |
| `_build_module_files(module_tree, clone_root)` | Module→file path dict-building | **Removed** (see Refactoring Plan) |
| `_build_module_entity_map(enhanced_tree, file_entities)` | Entity map building loop | **Removed** (see Refactoring Plan) |
| `_collect_page_context(page_spec, module_entity_map, dep_summary)` | Page entity + dep-info collection | **Changed** to `_collect_page_entities` |
| `_prepend_architecture_diagram(content, diagram)` | Regex-based mermaid diagram prepend | **Removed** (see Refactoring Plan) |
| `_make_faiss_store(repo_data_dir, embedding)` | FAISSStore construction | **Implemented** |

---

## 2. Deduplicate LLM provider JSON parsing

Added `_parse_json_response(raw: str) -> dict` to `worker/llm/base.py`. Strips optional Markdown code fences (` ```json ` / ` ``` `) and calls `json.loads`.

Replaced identical 6-line fence-stripping block in `generate_structured` of:
- `worker/llm/anthropic_provider.py`
- `worker/llm/openai_provider.py`
- `worker/llm/ollama_provider.py`

---

## 3. Deduplicate `api/queue.py` Redis pool

Extracted `_enqueue(job_name, **kwargs)` helper that handles `create_pool` / `enqueue_job` / `close`. Both `enqueue_full_index` and `enqueue_refresh_index` became 2-line wrappers.

---

## 4. Frontend: extract `repoId` utility

Added `repoId(owner, repo)` to `web/lib/utils.ts`. Replaced the inline `crypto.createHash(...)` SHA-256 hash in 5 route files:

- `web/app/[owner]/[repo]/layout.tsx`
- `web/app/[owner]/[repo]/page.tsx`
- `web/app/[owner]/[repo]/chat/page.tsx`
- `web/app/[owner]/[repo]/graph/page.tsx`
- `web/app/[owner]/[repo]/[slug]/page.tsx`

---

## 5. `docker-compose.yml` — YAML anchors for shared env vars

Used a YAML extension field (`x-common-env: &common-env`) to DRY the 13 identical environment variable definitions shared between the `api` and `worker` services.

---

## Status: Implemented (Phase 2)

This plan is fully implemented. Note that several helpers planned for `worker/jobs.py` were ultimately rendered unnecessary or were superseded by the **Pipeline Refactoring Plan** which moved the logic into more granular pipeline modules (like `ast_analysis.py`).
