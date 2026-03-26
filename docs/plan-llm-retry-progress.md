# Plan: LLM Retry, Force/Resume Mode, Frontend Retry Display

## Context

AutoWiki's 5-stage wiki generation pipeline makes LLM and embedding API calls with no transient error handling. The CLI `--force` flag exists but is not wired to the API or worker. The frontend shows progress but cannot visually distinguish a "waiting for retry" state from normal operation.

This plan adds:
1. **Async retry with exponential backoff** for all LLM/embedding calls
2. **Force/Resume mode** for wiki generation (force = full regeneration; default = skip already-done work)
3. **Frontend retry progress indicator** (amber state, retry message)

---

## Files to Create

### `worker/utils/__init__.py`
Empty package init.

### `worker/utils/retry.py`
Generic async retry utility:
```python
"""Async exponential backoff retry for transient LLM/embedding errors."""
import asyncio, logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")
OnRetryCallback = Callable[[int, int, float, Exception], Awaitable[None]]

_TRANSIENT: list[type[Exception]] = [TimeoutError, asyncio.TimeoutError, OSError]
try:
    import anthropic
    _TRANSIENT += [anthropic.APITimeoutError, anthropic.RateLimitError,
                   anthropic.APIConnectionError, anthropic.InternalServerError]
except ImportError:
    pass
try:
    import openai
    _TRANSIENT += [openai.APITimeoutError, openai.RateLimitError,
                   openai.APIConnectionError, openai.InternalServerError]
except ImportError:
    pass
TRANSIENT_EXCEPTIONS: tuple[type[Exception], ...] = tuple(_TRANSIENT)

async def async_retry(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = 3,
    initial_delay: float = 2.0,
    backoff_factor: float = 2.0,
    max_delay: float = 60.0,
    transient_exceptions: tuple[type[Exception], ...] = TRANSIENT_EXCEPTIONS,
    on_retry: OnRetryCallback | None = None,
    **kwargs: Any,
) -> T:
    delay = initial_delay
    for attempt in range(max_retries):
        try:
            return await fn(*args, **kwargs)
        except transient_exceptions as exc:
            if attempt == max_retries - 1:
                raise
            wait = min(delay, max_delay)
            logger.warning("Transient error (attempt %d/%d): %s. Retrying in %.0fs.", attempt+1, max_retries, exc, wait)
            if on_retry is not None:
                await on_retry(attempt + 1, max_retries, wait, exc)
            await asyncio.sleep(wait)
            delay *= backoff_factor
    raise AssertionError("unreachable")
```

---

## Files to Modify

### `worker/pipeline/wiki_planner.py`
- Import `async_retry, TRANSIENT_EXCEPTIONS, OnRetryCallback` from `worker.utils.retry`
- Add `on_retry: OnRetryCallback | None = None` to `generate_page_plan` signature
- Inside the existing `for attempt in range(max_retries)` loop, wrap `llm.generate_structured` with `async_retry`:
  ```python
  raw = await async_retry(
      llm.generate_structured, prompt, schema=_PLAN_SCHEMA, system=_SYSTEM,
      on_retry=on_retry, transient_exceptions=TRANSIENT_EXCEPTIONS,
  )
  ```
- Keep the outer loop unchanged (catches ValueError/JSONDecodeError/KeyError for schema validation)

### `worker/pipeline/page_generator.py`
- Import `async_retry, TRANSIENT_EXCEPTIONS, OnRetryCallback` from `worker.utils.retry`
- Add `on_retry: OnRetryCallback | None = None` to `generate_page` signature
- Wrap each `embedding.embed(q)` call with `async_retry`
- Wrap `llm.generate(prompt, system=_SYSTEM)` with `async_retry`

### `worker/pipeline/rag_indexer.py`
- Import `async_retry, TRANSIENT_EXCEPTIONS, OnRetryCallback` from `worker.utils.retry`
- Add `on_retry: "OnRetryCallback | None" = None` to `build_rag_index` signature
- Wrap `embedding_provider.embed_batch(texts, is_code=is_code)` with `async_retry`

### `worker/jobs.py`
Add these changes inside `run_full_index`:

**New `force: bool = False` parameter** in function signature.

**Add imports**: `from sqlalchemy import delete, select`

**`on_retry` callback** (create after computing `db_path`):
```python
async def _on_retry(attempt: int, max_retries: int, wait: float, exc: Exception) -> None:
    await _update_job(
        db_path, job_id,
        status_description=f"Retry {attempt}/{max_retries} in {wait:.0f}s ({type(exc).__name__})",
    )
```

**Define `repo_data_dir`, `index_path`, `meta_path`, `wiki_dir` early** (move `wiki_dir` definition up before Stage 3):
```python
repo_data_dir = data_dir / "repos" / repo_id
repo_data_dir.mkdir(parents=True, exist_ok=True)
index_path = repo_data_dir / "faiss.index"
meta_path = repo_data_dir / "faiss.meta.pkl"
wiki_dir = repo_data_dir / "wiki"
```

**Force cleanup block** (right after computing paths):
```python
if force:
    for p in (index_path, meta_path):
        if p.exists():
            p.unlink()
    async with get_session(db_path) as s:
        await s.execute(delete(WikiPage).where(WikiPage.repo_id == repo_id))
        await s.commit()
    if wiki_dir.exists():
        for f in wiki_dir.glob("*.md"):
            f.unlink()
```

**Resume: load existing page slugs** (before the page generation loop):
```python
existing_slugs: set[str] = set()
if not force:
    async with get_session(db_path) as s:
        result = await s.execute(select(WikiPage).where(WikiPage.repo_id == repo_id))
        existing_slugs = {p.slug for p in result.scalars().all()}
```

**Page generation loop**: skip already-generated pages:
```python
for i, page_spec in enumerate(plan.pages):
    if page_spec.slug in existing_slugs:
        progress = 65 + int(35 * (i + 1) / total)
        await _update_job(db_path, job_id, progress=progress,
                          status_description=f"Skipping existing page: {page_spec.title}")
        continue
    # ... existing generation code ...
```

**Pass `on_retry` and `force`** to pipeline functions:
- `build_rag_index(..., on_retry=_on_retry)`
- `generate_page_plan(..., on_retry=_on_retry)`
- `generate_page(..., on_retry=_on_retry)`

### `api/routers/repos.py`
```python
class IndexRequest(BaseModel):
    url: str
    force: bool = False

# In submit_repo:
await enqueue_full_index(repo_id, job_id, owner, name, force=req.force)
```

### `api/queue.py`
```python
async def enqueue_full_index(repo_id, job_id, owner, name, force: bool = False) -> str:
    ...
    await redis.enqueue_job(
        "run_full_index", repo_id=repo_id, job_id=job_id, owner=owner, name=name, force=force
    )
```

### `cli/commands/index.py`
Wire existing `--force` flag to the request body:
```python
resp = httpx.post(f"{api_url}/api/repos", json={"url": url, "force": force}, timeout=10)
```

### `api/ws/jobs.py`
Add `retrying` field to WebSocket JSON, derived from `status_description`:
```python
retrying = (
    job.status == "running"
    and bool(job.status_description)
    and job.status_description.startswith("Retry ")
)
await websocket.send_json({
    "progress": job.progress,
    "status": job.status,
    "status_description": job.status_description,
    "retrying": retrying,
})
```

### `web/lib/api.ts`
Update `submitRepo` to accept `force`:
```typescript
export async function submitRepo(url: string, force: boolean = false) {
  const res = await fetch(`${API_URL}/api/repos`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, force }),
  });
  ...
}
```

### `web/lib/ws.ts`
Add `retrying` state:
```typescript
const [retrying, setRetrying] = useState(false);
// In onmessage:
setRetrying(data.retrying ?? false);
// Return:
return { progress, status, statusDescription, retrying };
```

### `web/components/JobProgressBar.tsx`
Use `retrying` for amber styling:
```tsx
const { progress, status, statusDescription, retrying } = useJobProgress(jobId);
// In JSX:
<p className={`text-xs ${retrying ? "text-amber-500" : "text-muted-foreground"} animate-pulse`}>
  {statusDescription}
</p>
```

### `web/components/IndexForm.tsx`
Add `force` checkbox state and pass to `submitRepo`:
```tsx
const [force, setForce] = useState(false);
// In handleSubmit:
const { repo_id, job_id } = await submitRepo(url, force);
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
- Add test for `force=True`: verify FAISS files and WikiPage records are cleared
- Add test for resume: pre-populate a page in DB, verify it's skipped in generation loop

---

## Verification

```bash
# Run all tests
pytest tests/ --ignore=tests/e2e -x

# Manually test force flag
autowiki index github.com/owner/repo          # normal (resume mode)
autowiki index github.com/owner/repo --force  # full regeneration

# Manually test retry display
# Temporarily make LLM throw TimeoutError to see "Retry 1/3 in 2s" in frontend
```
