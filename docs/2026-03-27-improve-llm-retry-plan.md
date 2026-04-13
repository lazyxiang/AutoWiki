# Plan: LLM Retry, Force/Resume Mode, Frontend Retry Display

## Context

AutoWiki's 5-stage wiki generation pipeline makes LLM and embedding API calls with no transient error handling. The CLI `--force` flag exists but is not wired to the API or worker. (OUTDATED: Renamed to `reuse_index` in API) The frontend shows progress but cannot visually distinguish a "waiting for retry" state from normal operation.

This plan adds:
1. **Async retry with exponential backoff** for all LLM/embedding calls
2. **Force/Resume mode** for wiki generation (force = full regeneration; default = skip already-done work) (OUTDATED: implemented as `reuse_index` in API and incremental refresh in worker)
3. **Frontend retry progress indicator** (amber state, retry message)

---

## Files to Create

### `worker/utils/__init__.py`
Empty package init.

### `worker/utils/retry.py`
Generic async retry utility:
```python
"""Async exponential backoff retry for transient LLM/embedding errors."""
... [3,465 characters omitted] ...
<p className={`text-xs ${retrying ? "text-amber-500" : "text-muted-foreground"} animate-pulse`}>
  {statusDescription}
</p>
```

### `web/components/IndexForm.tsx`
Add `force` checkbox state and pass to `submitRepo`: (OUTDATED: implemented as `reuse_index` checkbox)
```tsx
const [force, setForce] = useState(false); (OUTDATED: reuseIndex)
// In handleSubmit:
const { repo_id, job_id } = await submitRepo(url, force); (OUTDATED: reuseIndex)
// In JSX (after Input, before Button):
<label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
  <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
  Force full regeneration
</label>
```

---

## Tests to Add/Update

### `tests/worker/test_retry.py` (new)
- Test `async_retry` succeeds on first try
- Test retries on transient exception, succeeds on 2nd attempt
- Test raises after exhausting `max_retries`
- Test `on_retry` callback is called with correct arguments
- Test exponential backoff delay calculation (mock `asyncio.sleep`)
- Test non-transient exceptions propagate immediately (no retry)

### `tests/worker/test_jobs.py` (update)
- Add test for `force=True`: verify FAISS files and WikiPage records are cleared (OUTDATED: reuse_index=False)
- Add test for resume: pre-populate a page in DB, verify it's skipped in generation loop (OUTDATED: implemented via run_refresh_index)

---

## Status: Implemented (Phase 2)

This plan is fully implemented. Note the following naming and architectural changes in the final version:
- The `force` flag was renamed to `reuse_index` in the API and worker. 
- `reuse_index=False` (default) acts like the planned `force=True`, clearing artifacts (except clone) and re-embedding.
- `reuse_index=True` preserves existing FAISS indices but still regenerates wiki pages in a full index job.
- Incremental resume/refresh is handled by a separate `run_refresh_index` entry point that calculates diffs and regenerates only affected pages.
- Frontend amber retry states and `status_description` propagation are implemented exactly as planned.
