# 方案：LLM 重试、强制/恢复模式、前端重试显示

> **[已完成]** 已实施并合并。背景中提到的“5 阶段流水线”已成为历史；流水线现在共有 6 个阶段。`--force` 标志在后来的工作中被 `--reuse-index` 语义所取代。

## 背景

AutoWiki 的 ~~5 阶段~~ Wiki 生成流水线在进行 LLM 和 Embedding API 调用时，缺乏对瞬时错误的处理。CLI 的 `--force` 标志虽然存在，但尚未接入 API 或 Worker。前端虽然能显示进度，但无法直观区分“正在等待重试”与正常运行状态。

本方案增加了：
1. **带指数退避的异步重试**：适用于所有 LLM/Embedding 调用。
2. **强制/恢复模式**：用于 Wiki 生成（force = 全量重新生成；默认 = 跳过已完成的工作）。
3. **前端重试进度指示器**（琥珀色状态，显示重试消息）。

---

## 新增文件

### `worker/utils/__init__.py`
空的包初始化文件。

### `worker/utils/retry.py`
通用的异步重试工具：
```python
"""针对 LLM/Embedding 瞬时错误的异步指数退避重试。"""
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
            logger.warning("瞬时错误 (第 %d/%d 次尝试): %s。将在 %.0fs 后重试。", attempt+1, max_retries, exc, wait)
            if on_retry is not None:
                await on_retry(attempt + 1, max_retries, wait, exc)
            await asyncio.sleep(wait)
            delay *= backoff_factor
    raise AssertionError("不可达代码")
```

---

## 待修改文件

### `worker/pipeline/wiki_planner.py`
- 从 `worker.utils.retry` 导入 `async_retry, TRANSIENT_EXCEPTIONS, OnRetryCallback`。
- 在 `generate_page_plan` 签名中增加 `on_retry: OnRetryCallback | None = None`。
- 在现有的 `for attempt in range(max_retries)` 循环内部，使用 `async_retry` 包装 `llm.generate_structured`：
  ```python
  raw = await async_retry(
      llm.generate_structured, prompt, schema=_PLAN_SCHEMA, system=_SYSTEM,
      on_retry=on_retry, transient_exceptions=TRANSIENT_EXCEPTIONS,
  )
  ```
- 保持外层循环不变（用于捕获 schema 校验失败的 ValueError/JSONDecodeError/KeyError）。

### `worker/pipeline/page_generator.py`
- 从 `worker.utils.retry` 导入 `async_retry, TRANSIENT_EXCEPTIONS, OnRetryCallback`。
- 在 `generate_page` 签名中增加 `on_retry: OnRetryCallback | None = None`。
- 使用 `async_retry` 包装每个 `embedding.embed(q)` 调用。
- 使用 `async_retry` 包装 `llm.generate(prompt, system=_SYSTEM)`。

### `worker/pipeline/rag_indexer.py`
- 从 `worker.utils.retry` 导入 `async_retry, TRANSIENT_EXCEPTIONS, OnRetryCallback`。
- 在 `build_rag_index` 签名中增加 `on_retry: "OnRetryCallback | None" = None`。
- 使用 `async_retry` 包装 `embedding_provider.embed_batch(texts, is_code=is_code)`。

### `worker/jobs.py`
在 `run_full_index` 内部进行如下修改：

**函数签名中增加 `force: bool = False` 参数**。

**添加导入**：`from sqlalchemy import delete, select`

**`on_retry` 回调**（在计算完 `db_path` 后创建）：
```python
async def _on_retry(attempt: int, max_retries: int, wait: float, exc: Exception) -> None:
    await _update_job(
        db_path, job_id,
        status_description=f"正在重试 {attempt}/{max_retries}，将在 {wait:.0f}s 后开始 ({type(exc).__name__})",
    )
```

**提前定义 `repo_data_dir`, `index_path`, `meta_path`, `wiki_dir`**（将 `wiki_dir` 的定义移至阶段 3 之前）：
```python
repo_data_dir = data_dir / "repos" / repo_id
repo_data_dir.mkdir(parents=True, exist_ok=True)
index_path = repo_data_dir / "faiss.index"
meta_path = repo_data_dir / "faiss.meta.pkl"
wiki_dir = repo_data_dir / "wiki"
```

**强制清理代码块**（在计算完路径后立即执行）：
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

**恢复：加载现有页面 slug**（在页面生成循环之前）：
```python
existing_slugs: set[str] = set()
if not force:
    async with get_session(db_path) as s:
        result = await s.execute(select(WikiPage).where(WikiPage.repo_id == repo_id))
        existing_slugs = {p.slug for p in result.scalars().all()}
```

**页面生成循环**：跳过已生成的页面：
```python
for i, page_spec in enumerate(plan.pages):
    if page_spec.slug in existing_slugs:
        progress = 65 + int(35 * (i + 1) / total)
        await _update_job(db_path, job_id, progress=progress,
                          status_description=f"跳过已存在页面：{page_spec.title}")
        continue
    # ... 现有生成代码 ...
```

**向流水线函数传递 `on_retry` 和 `force`**：
- `build_rag_index(..., on_retry=_on_retry)`
- `generate_page_plan(..., on_retry=_on_retry)`
- `generate_page(..., on_retry=_on_retry)`

### `api/routers/repos.py`
```python
class IndexRequest(BaseModel):
    url: str
    force: bool = False

# 在 submit_repo 中：
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
将现有的 `--force` 标志接入请求体：
```python
resp = httpx.post(f"{api_url}/api/repos", json={"url": url, "force": force}, timeout=10)
```

### `api/ws/jobs.py`
在 WebSocket JSON 中添加 `retrying` 字段，由 `status_description` 推导得出：
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
更新 `submitRepo` 以接收 `force` 参数：
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
添加 `retrying` 状态：
```typescript
const [retrying, setRetrying] = useState(false);
// 在 onmessage 中：
setRetrying(data.retrying ?? false);
// 返回：
return { progress, status, statusDescription, retrying };
```

### `web/components/JobProgressBar.tsx`
使用 `retrying` 状态应用琥珀色样式：
```tsx
const { progress, status, statusDescription, retrying } = useJobProgress(jobId);
// 在 JSX 中：
<p className={`text-xs ${retrying ? "text-amber-500" : "text-muted-foreground"} animate-pulse`}>
  {statusDescription}
</p>
```

### `web/components/IndexForm.tsx`
添加 `force` 复选框状态并传递给 `submitRepo`：
```tsx
const [force, setForce] = useState(false);
// 在 handleSubmit 中：
const { repo_id, job_id } = await submitRepo(url, force);
// 在 JSX 中 (在 Input 之后, Button 之前):
<label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
  <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
  强制全量生成
</label>
```

---

## 新增/更新测试

### `tests/worker/test_retry.py` (新增)
- 测试 `async_retry` 在第一次尝试时成功。
- 测试在瞬时异常时重试，并在第二次尝试时成功。
- 测试在耗尽 `max_retries` 后抛出异常。
- 测试 `on_retry` 回调被带正确参数调用。
- 测试指数退避延迟计算（模拟 `asyncio.sleep`）。
- 测试非瞬时异常立即传播（不重试）。

### `tests/worker/test_jobs.py` (更新)
- 增加 `force=True` 测试：验证 FAISS 文件和 WikiPage 记录已被清理。
- 增加恢复模式测试：在 DB 中预置一个页面，验证它在生成循环中被跳过。

---

## 验证

```bash
# 运行所有测试
pytest tests/ --ignore=tests/e2e -x

# 手动测试 force 标志
autowiki index github.com/owner/repo          # 正常（恢复模式）
autowiki index github.com/owner/repo --force  # 全量重新生成

# 手动测试重试显示
# 临时让 LLM 抛出 TimeoutError，观察前端是否显示 "正在重试 1/3，将在 2s 后开始"
```
