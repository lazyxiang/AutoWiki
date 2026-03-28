# Plan: Simplify Code & Remove Redundancy

## Context

The codebase accumulated duplication across Phase 1 and Phase 2. The biggest problem was `worker/jobs.py` — two 400-line orchestrator functions (`run_full_index`, `run_refresh_index`) sharing almost identical pipeline stages. This plan extracts stage-level helpers, deduplicates LLM provider logic, simplifies the API queue, extracts a frontend utility, and DRYs the Docker config.

---

## 1. Decompose `worker/jobs.py` into stage helpers

Extract 7 private helper functions at module level. Both orchestrators call them — each function becomes a readable ~120-line flow instead of a 400-line monolith.

### Helpers extracted

| Helper | Replaces |
|---|---|
| `_make_on_retry(db_path, job_id)` | Duplicate `_on_retry` closure in both job functions |
| `_build_file_entities(files, clone_root)` | Per-file AST entity loop duplicated in stages 2 |
| `_build_module_files(module_tree, clone_root)` | Module→file path dict-building loop duplicated in stages 2b |
| `_build_module_entity_map(enhanced_tree, file_entities)` | Entity map building loop (full-index version is canonical, includes file/line enrichment) |
| `_collect_page_context(page_spec, module_entity_map, dep_summary)` | Page entity + dep-info collection loop duplicated in stage 5 |
| `_prepend_architecture_diagram(content, diagram)` | Regex-based mermaid diagram prepend duplicated in stage 6 |
| `_make_faiss_store(repo_data_dir, embedding)` | 4-line FAISSStore construction duplicated in stage 3 |

### Result

`run_full_index`: ~383 lines → ~150 lines
`run_refresh_index`: ~462 lines → ~160 lines
Total file: ~880 lines → ~660 lines

---

## 2. Deduplicate LLM provider JSON parsing

Added `_parse_json_response(raw: str) -> dict` to `worker/llm/base.py`. Strips optional markdown code fences (` ```json ` / ` ``` `) and calls `json.loads`.

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

## Files Modified

- `worker/jobs.py`
- `worker/llm/base.py`
- `worker/llm/anthropic_provider.py`
- `worker/llm/openai_provider.py`
- `worker/llm/ollama_provider.py`
- `api/queue.py`
- `web/lib/utils.ts`
- `web/app/[owner]/[repo]/layout.tsx`
- `web/app/[owner]/[repo]/page.tsx`
- `web/app/[owner]/[repo]/chat/page.tsx`
- `web/app/[owner]/[repo]/graph/page.tsx`
- `web/app/[owner]/[repo]/[slug]/page.tsx`
- `docker-compose.yml`

---

## Verification

```bash
uv run ruff check . && uv run ruff format --check .
pytest tests/ --ignore=tests/e2e   # 127 passed
cd web && npm run lint
docker compose config               # valid YAML
```
