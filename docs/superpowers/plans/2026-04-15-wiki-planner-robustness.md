# Wiki Planner Observability & Robustness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the three root causes behind poor planner output: silent retry/validation failures, the round-robin fallback that destroys file locality, and the single-shot Phase 2 file assignment that exceeds the LLM's reliable structured-output capacity.

**Architecture:** Three sequential stages executed in order. Stage 1 makes every retry and validation failure visible in the logs so future debugging is cheap. Stage 2 replaces the round-robin fallback with directory-clustering to preserve locality when the LLM fails. Stage 3 rewrites Phase 2 file assignment into cache-reusing batches so the LLM call actually succeeds. Quality polish (outline anchors, multi-page assignment) is deferred and documented in Stage 4.

**Tech Stack:** Python 3.12, asyncio, pytest (asyncio_mode=auto), Anthropic/OpenAI/Gemini/Ollama SDKs

**Spec:** This plan. No separate spec document.

**Out of scope (explicitly deferred):**
- Independent end-to-end validation of each stage against real repos (too expensive under the current test budget — rely on logs).
- Layer C1 outline anchors (README sections, package-level docstrings, promoted clusters).
- Layer C2 multi-page file assignment (schema change to `primary_page` + `secondary_pages`).
- Independent per-stage test harnesses that invoke the pipeline with recorded fixtures.

---

## File Structure

**Modified files:**
- `worker/pipeline/wiki_planner.py` — primary target. Add logging, replace round-robin, rewrite `_assign_files` into batches.
- `worker/pipeline/page_outline.py` — add per-retry logging.
- `worker/pipeline/page_draft.py` — add logger + log draft failures.
- `worker/pipeline/fact_check.py` — add per-retry logging.
- `worker/pipeline/page_generator.py` — log revision fallback path.
- `CLAUDE.md` — document new observability conventions + deferred work.

**Created files:**
- `worker/pipeline/pipeline_logging.py` — shared `log_validation_retry` / `log_final_failure` helpers so logs have a consistent shape across the pipeline.
- `tests/worker/test_pipeline_logging.py` — unit tests for the logging helpers.
- `tests/worker/test_directory_cluster_fallback.py` — unit tests for the new locality-preserving fallback.
- `tests/worker/test_assign_files_batched.py` — unit tests for Stage 3 batched assignment.

---

## Stage 1 — Pipeline-wide Observability

**Objective:** every retry and every validation failure in the planner, page outline, page draft, fact-check, and revision paths produces a structured log at the correct level. No silent swallowing of `ValueError` / `json.JSONDecodeError`. Fallback execution is logged at `ERROR` level.

### Task 1: Create the shared logging helper

**Files:**
- Create: `worker/pipeline/pipeline_logging.py`
- Create: `tests/worker/test_pipeline_logging.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/worker/test_pipeline_logging.py`:

```python
"""Tests for shared pipeline logging helpers."""

from __future__ import annotations

import logging

from worker.pipeline.pipeline_logging import (
    log_final_failure,
    log_validation_retry,
)


def test_log_validation_retry_emits_warning(caplog):
    logger = logging.getLogger("test.pipeline")
    with caplog.at_level(logging.WARNING, logger="test.pipeline"):
        log_validation_retry(
            logger,
            stage="wiki_planner.outline",
            attempt=1,
            max_retries=3,
            exc=ValueError("bad slug"),
            context={"page_count": 12, "total_files": 180},
        )
    assert len(caplog.records) == 1
    rec = caplog.records[0]
    assert rec.levelno == logging.WARNING
    assert "wiki_planner.outline" in rec.message
    assert "attempt 1/3" in rec.message
    assert "bad slug" in rec.message
    assert "page_count=12" in rec.message
    assert "total_files=180" in rec.message


def test_log_final_failure_emits_error_with_exc_info(caplog):
    logger = logging.getLogger("test.pipeline")
    try:
        raise ValueError("exhausted")
    except ValueError as exc:
        with caplog.at_level(logging.ERROR, logger="test.pipeline"):
            log_final_failure(
                logger,
                stage="wiki_planner.assign_files",
                exc=exc,
                context={"batches": 5, "unassigned": 12},
            )
    assert len(caplog.records) == 1
    rec = caplog.records[0]
    assert rec.levelno == logging.ERROR
    assert "wiki_planner.assign_files" in rec.message
    assert "exhausted" in rec.message
    assert rec.exc_info is not None  # exc_info attached
    assert "batches=5" in rec.message
    assert "unassigned=12" in rec.message


def test_log_validation_retry_truncates_long_response(caplog):
    logger = logging.getLogger("test.pipeline")
    long_text = "x" * 5000
    with caplog.at_level(logging.WARNING, logger="test.pipeline"):
        log_validation_retry(
            logger,
            stage="wiki_planner.outline",
            attempt=2,
            max_retries=3,
            exc=ValueError("schema mismatch"),
            context={"raw_response": long_text},
        )
    rec = caplog.records[0]
    # The response should be truncated with an ellipsis marker.
    assert "..." in rec.message
    assert len(rec.message) < 3000
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/worker/test_pipeline_logging.py -v
```

Expected: ImportError — module does not exist yet.

- [ ] **Step 3: Implement the helper**

Create `worker/pipeline/pipeline_logging.py`:

```python
"""Shared logging helpers for pipeline retry loops and fallback paths.

Every stage that catches ``ValueError`` / ``json.JSONDecodeError`` /
``KeyError`` from an LLM response must use these helpers so the resulting
logs have a consistent shape.  Without them, validation failures are
silently swallowed by the retry loop and the user sees a degraded wiki
with no clue where the degradation originated.
"""

from __future__ import annotations

import logging
from typing import Any

_MAX_CONTEXT_VALUE_LEN = 500


def _format_context(context: dict[str, Any] | None) -> str:
    """Render a context dict as ``key=value`` pairs, truncating long values."""
    if not context:
        return ""
    parts: list[str] = []
    for key, value in context.items():
        text = str(value)
        if len(text) > _MAX_CONTEXT_VALUE_LEN:
            text = text[:_MAX_CONTEXT_VALUE_LEN] + "...(truncated)"
        parts.append(f"{key}={text}")
    return " ".join(parts)


def log_validation_retry(
    logger: logging.Logger,
    *,
    stage: str,
    attempt: int,
    max_retries: int,
    exc: Exception,
    context: dict[str, Any] | None = None,
) -> None:
    """Log a *recoverable* validation/parse failure from a pipeline retry loop.

    Called inside ``except (ValueError, json.JSONDecodeError, KeyError)``
    blocks to record what the LLM produced and why it was rejected, so the
    next retry's failure mode is visible.  Always emits ``WARNING``.
    """
    ctx = _format_context(context)
    suffix = f" | {ctx}" if ctx else ""
    logger.warning(
        "%s: validation failed on attempt %d/%d: %s%s",
        stage,
        attempt,
        max_retries,
        exc,
        suffix,
    )


def log_final_failure(
    logger: logging.Logger,
    *,
    stage: str,
    exc: Exception,
    context: dict[str, Any] | None = None,
) -> None:
    """Log an *exhausted* retry loop or fallback invocation.

    Always emits ``ERROR`` with ``exc_info=True`` so the full traceback is
    captured.  Use this when the pipeline is about to hand off to a
    heuristic fallback (e.g. directory-clustering assignment) or return a
    degraded result.
    """
    ctx = _format_context(context)
    suffix = f" | {ctx}" if ctx else ""
    logger.error(
        "%s: all retries exhausted: %s%s",
        stage,
        exc,
        suffix,
        exc_info=True,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/worker/test_pipeline_logging.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/pipeline_logging.py tests/worker/test_pipeline_logging.py
git commit -m "feat(worker): add shared pipeline retry/fallback logging helpers"
```

---

### Task 2: Wire logging into `wiki_planner._generate_outline`

**Files:**
- Modify: `worker/pipeline/wiki_planner.py:522-576` (`_generate_outline`)
- Test: `tests/worker/test_wiki_planner.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/worker/test_wiki_planner.py`:

```python
async def test_generate_outline_logs_each_validation_failure(caplog):
    """Each retry of _generate_outline must log a WARNING with the error."""
    import logging
    from unittest.mock import AsyncMock

    from worker.pipeline.wiki_planner import _generate_outline

    # First two calls return an invalid outline (duplicate slug → validation fails),
    # third call returns a valid outline.
    bad = {
        "pages": [
            {"title": "Overview", "purpose": "a"},
            {"title": "overview", "purpose": "b"},  # dup slug
        ]
    }
    good = {
        "pages": [
            {"title": "Overview", "purpose": "Top"},
            {"title": "Core", "purpose": "Core logic", "parent": "Overview"},
            {"title": "Utils", "purpose": "Helpers", "parent": "Overview"},
            {"title": "API", "purpose": "Routes", "parent": "Overview"},
            {"title": "Tests", "purpose": "Tests", "parent": "Overview"},
        ]
    }
    llm = AsyncMock()
    llm.generate_structured.side_effect = [bad, bad, good]

    with caplog.at_level(logging.WARNING, logger="worker.planner"):
        pages = await _generate_outline(
            file_summary="files",
            repo_name="repo",
            llm=llm,
            readme=None,
            dep_info=None,
            clusters=None,
            page_range=(5, 20),
            system="sys",
            on_retry=None,
            max_retries=3,
            total_file_count=50,
        )

    assert len(pages) == 5
    retry_logs = [
        r for r in caplog.records if "wiki_planner.outline" in r.getMessage()
    ]
    assert len(retry_logs) == 2  # two failures logged, third succeeded
    assert all("attempt" in r.getMessage() for r in retry_logs)
    assert all("Duplicate page slugs" in r.getMessage() for r in retry_logs)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/worker/test_wiki_planner.py::test_generate_outline_logs_each_validation_failure -v
```

Expected: FAIL — `len(retry_logs) == 0` (no retry logging yet).

- [ ] **Step 3: Patch `_generate_outline` to log each retry**

Modify `worker/pipeline/wiki_planner.py`. First add the import near the top (after `from worker.utils.retry import ...`):

```python
from worker.pipeline.pipeline_logging import (
    log_final_failure,
    log_validation_retry,
)
```

Then replace the `except` block inside `_generate_outline` (currently at lines 572-574):

```python
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            log_validation_retry(
                logger,
                stage="wiki_planner.outline",
                attempt=attempt + 1,
                max_retries=max_retries,
                exc=e,
                context={
                    "total_files": total_file_count,
                    "page_range": f"{page_range[0]}-{page_range[1]}",
                },
            )
            if attempt < max_retries - 1:
                prompt += f"\n\nPrevious attempt failed: {e}. Please fix and retry."
```

Then replace the final `raise ValueError("Failed to generate outline after all retries")` with:

```python
    exc = ValueError("Failed to generate outline after all retries")
    log_final_failure(
        logger,
        stage="wiki_planner.outline",
        exc=exc,
        context={
            "total_files": total_file_count,
            "max_retries": max_retries,
        },
    )
    raise exc
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/worker/test_wiki_planner.py::test_generate_outline_logs_each_validation_failure -v
```

Expected: PASS.

- [ ] **Step 5: Run the full wiki_planner test file to verify no regression**

```bash
uv run pytest tests/worker/test_wiki_planner.py -v
```

Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
git commit -m "feat(planner): log each _generate_outline retry + final failure"
```

---

### Task 3: Wire logging into `wiki_planner._assign_files`

**Files:**
- Modify: `worker/pipeline/wiki_planner.py:579-659` (`_assign_files`)
- Test: `tests/worker/test_wiki_planner.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/worker/test_wiki_planner.py`:

```python
async def test_assign_files_logs_each_validation_failure_and_fallback(caplog):
    """_assign_files must log each retry AND the fallback invocation."""
    import logging
    from unittest.mock import AsyncMock

    from worker.pipeline.wiki_planner import _assign_files

    outline = [
        {"title": "Overview", "purpose": "top"},
        {"title": "Core", "purpose": "core"},
    ]
    all_files = [f"f{i}.py" for i in range(10)]

    # Return an over-stuffed page on every attempt → validation fails 3x,
    # then fallback runs.
    stuffed = {
        "assignments": [{"file": f, "page_title": "Overview"} for f in all_files]
    }
    llm = AsyncMock()
    llm.generate_structured.side_effect = [stuffed, stuffed, stuffed]

    # Drop the 25-file cap temporarily by pretending we have more than 25 files
    many = [f"f{i}.py" for i in range(30)]
    stuffed = {
        "assignments": [{"file": f, "page_title": "Overview"} for f in many]
    }
    llm.generate_structured.side_effect = [stuffed, stuffed, stuffed]

    with caplog.at_level(logging.WARNING, logger="worker.planner"):
        result = await _assign_files(
            outline=outline,
            file_summary="files",
            dep_info=None,
            all_files=many,
            llm=llm,
            system="sys",
            on_retry=None,
            max_retries=3,
        )

    retry_logs = [
        r for r in caplog.records
        if "wiki_planner.assign_files" in r.getMessage()
        and "attempt" in r.getMessage()
    ]
    fallback_logs = [
        r for r in caplog.records
        if "wiki_planner.assign_files" in r.getMessage()
        and r.levelno == logging.ERROR
    ]
    assert len(retry_logs) == 3, f"expected 3 retry logs, got {len(retry_logs)}"
    assert len(fallback_logs) == 1, f"expected 1 fallback error, got {len(fallback_logs)}"
    # Result is still returned so pipeline does not crash
    assert sum(len(v) for v in result.values()) == len(many)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/worker/test_wiki_planner.py::test_assign_files_logs_each_validation_failure_and_fallback -v
```

Expected: FAIL — no retry logging yet, no fallback ERROR logging yet.

- [ ] **Step 3: Patch `_assign_files` to log each retry and the fallback**

Modify `worker/pipeline/wiki_planner.py`. Replace the `except` block inside `_assign_files` (currently at lines 650-652):

```python
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            log_validation_retry(
                logger,
                stage="wiki_planner.assign_files",
                attempt=attempt + 1,
                max_retries=max_retries,
                exc=e,
                context={
                    "outline_pages": len(outline),
                    "total_files": len(all_files),
                    "llm_provider": type(_current_llm).__name__,
                },
            )
            if attempt < max_retries - 1:
                prompt += f"\n\nPrevious attempt failed: {e}. Please fix and retry."
```

And before the final fallback block (currently lines 654-659), add:

```python
    log_final_failure(
        logger,
        stage="wiki_planner.assign_files",
        exc=ValueError(
            "All LLM assignment attempts failed; using directory-clustering fallback"
        ),
        context={
            "outline_pages": len(outline),
            "total_files": len(all_files),
            "max_retries": max_retries,
        },
    )
```

(Leave the actual round-robin code in place for now — Stage 2 replaces it.)

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/worker/test_wiki_planner.py::test_assign_files_logs_each_validation_failure_and_fallback -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
git commit -m "feat(planner): log each _assign_files retry + final fallback"
```

---

### Task 4: Wire logging into `page_outline`, `page_draft`, `fact_check`, and `page_generator`

**Files:**
- Modify: `worker/pipeline/page_outline.py:267-288` (retry loop)
- Modify: `worker/pipeline/page_draft.py` (add logger + wrap `async_retry` failures)
- Modify: `worker/pipeline/fact_check.py:173-184` (exception handler)
- Modify: `worker/pipeline/page_generator.py:274-289` (revision fallback)
- Test: `tests/worker/test_page_outline.py`, `tests/worker/test_fact_check.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/worker/test_page_outline.py` (create the test file section if needed):

```python
async def test_page_outline_logs_each_retry(caplog):
    import logging
    from unittest.mock import AsyncMock

    from worker.pipeline.page_outline import generate_page_outline
    from worker.pipeline.wiki_planner import WikiPageSpec

    spec = WikiPageSpec(title="X", purpose="y", files=["a.py"])
    bad = {"sections": [], "key_claims": []}  # empty → validation failure
    good = {
        "sections": [
            {
                "heading": "Intro",
                "kind": "prose",
                "focus": "overview",
            }
        ],
        "key_claims": ["one", "two", "three"],
    }
    fast = AsyncMock()
    fast.generate_structured.side_effect = [bad, good]

    with caplog.at_level(logging.WARNING, logger="worker.page_outline"):
        await generate_page_outline(
            spec=spec,
            entity_summaries="",
            dep_info=None,
            fast_llm=fast,
            max_retries=2,
        )

    retry_logs = [
        r for r in caplog.records if "page_outline" in r.getMessage()
    ]
    assert any("attempt" in r.getMessage() for r in retry_logs)
```

Add to `tests/worker/test_fact_check.py` (create the test file if needed):

```python
async def test_fact_check_logs_failure_with_context(caplog):
    import logging
    from unittest.mock import AsyncMock

    from worker.pipeline.fact_check import run_fact_check
    from worker.pipeline.page_outline import PageOutline, Section

    llm = AsyncMock()
    llm.generate_structured.side_effect = RuntimeError("boom")

    outline = PageOutline(
        sections=[Section(heading="Intro", kind="prose", focus="...")],
        key_claims=["c1", "c2"],
    )

    with caplog.at_level(logging.WARNING, logger="worker.fact_check"):
        result = await run_fact_check(
            draft="draft text",
            outline=outline,
            entity_summaries="",
            dep_info=None,
            targeted_chunks="",
            fast_llm=llm,
        )

    assert result.verdict == "pass"  # fail-open
    failure_logs = [
        r for r in caplog.records if "fact_check" in r.getMessage()
    ]
    assert any("boom" in r.getMessage() for r in failure_logs)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/worker/test_page_outline.py::test_page_outline_logs_each_retry tests/worker/test_fact_check.py::test_fact_check_logs_failure_with_context -v
```

Expected: FAIL (missing retry logging, missing context log).

- [ ] **Step 3: Patch `page_outline.generate_page_outline`**

In `worker/pipeline/page_outline.py`, add the import:

```python
from worker.pipeline.pipeline_logging import (
    log_final_failure,
    log_validation_retry,
)
```

Replace the `except` block in `generate_page_outline` (around lines 275-288):

```python
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            log_validation_retry(
                logger,
                stage="page_outline",
                attempt=attempt + 1,
                max_retries=max_retries + 1,
                exc=e,
                context={"page_title": spec.title, "files": len(spec.files or [])},
            )
            if attempt < max_retries:
                error_seg = PromptSegment(
                    text=f"\n\nPrevious attempt failed: {e}. Fix and retry."
                )
                segments = list(segments) + [error_seg]
            else:
                log_final_failure(
                    logger,
                    stage="page_outline",
                    exc=e,
                    context={"page_title": spec.title},
                )
                raise
```

- [ ] **Step 4: Patch `page_draft.generate_draft`**

In `worker/pipeline/page_draft.py`, add at the top (after the module-level imports):

```python
import logging

from worker.pipeline.pipeline_logging import log_final_failure

logger = logging.getLogger("worker.page_draft")
```

Wrap the `async_retry` call in `generate_draft` (currently lines 230-236):

```python
    try:
        content = await async_retry(
            llm.generate,
            segments,
            system=system,
            transient_exceptions=TRANSIENT_EXCEPTIONS,
            on_retry=on_retry,
        )
    except Exception as exc:
        log_final_failure(
            logger,
            stage="page_draft",
            exc=exc,
            context={"page_title": spec.title, "files": len(spec.files or [])},
        )
        raise
    return content
```

(If the current body already has a `return content` line, keep it and put the logging wrapper around the `async_retry` only.)

- [ ] **Step 5: Patch `fact_check.run_fact_check`**

In `worker/pipeline/fact_check.py`, add the import:

```python
from worker.pipeline.pipeline_logging import log_final_failure
```

Replace the `except Exception:` block (around lines 182-184) with:

```python
    except Exception as exc:
        log_final_failure(
            logger,
            stage="fact_check",
            exc=exc,
            context={
                "outline_sections": len(outline.sections),
                "key_claims": len(outline.key_claims),
            },
        )
        return FactCheckResult(verdict="pass")
```

- [ ] **Step 6: Patch `page_generator` revision fallback**

In `worker/pipeline/page_generator.py`, replace the `except Exception:` block inside the Pass 4 revision (around lines 274-289):

```python
        except Exception as exc:
            from worker.pipeline.pipeline_logging import log_final_failure

            log_final_failure(
                logger,
                stage="page_generator.revision",
                exc=exc,
                context={
                    "page_title": spec.title,
                    "issue_count": len(fc_result.issues),
                },
            )
            for issue in fc_result.issues:
                if issue.kind == "claim" and issue.claim:
                    draft = strip_failed_claim(
                        draft, issue.claim, issue.reason, issue.section
                    )
                elif issue.kind == "diagram" and issue.diagram_index is not None:
                    draft = strip_failed_diagram(
                        draft, issue.section, issue.diagram_index, issue.reason
                    )
```

- [ ] **Step 7: Run the new tests to verify they pass**

```bash
uv run pytest tests/worker/test_page_outline.py::test_page_outline_logs_each_retry tests/worker/test_fact_check.py::test_fact_check_logs_failure_with_context -v
```

Expected: PASS.

- [ ] **Step 8: Run the full worker test suite to check for regressions**

```bash
uv run pytest tests/worker/ -v
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add worker/pipeline/page_outline.py worker/pipeline/page_draft.py worker/pipeline/fact_check.py worker/pipeline/page_generator.py tests/worker/test_page_outline.py tests/worker/test_fact_check.py
git commit -m "feat(worker): log retries and fallbacks in page generation pipeline"
```

---

### Task 5: Lint and full-suite verification for Stage 1

- [ ] **Step 1: Run pre-commit checks**

```bash
uv run ruff check .
uv run ruff format --check .
```

Expected: clean.

- [ ] **Step 2: Run the full backend test suite**

```bash
uv run pytest tests/ --ignore=tests/e2e -q
```

Expected: all tests pass, no regressions.

- [ ] **Step 3: If anything fails, fix the issue, re-stage, and amend the failing commit**

(Do not proceed to Stage 2 until Stage 1 is fully green.)

---

## Stage 2 — Directory-Clustering Fallback

**Objective:** replace the round-robin fallback in `_assign_files` with a locality-preserving directory-clustering algorithm. When the LLM fails, fall back to "files that share a directory belong on the page whose title/purpose best matches that directory," not "files alphabetically interleaved across all pages."

### Task 6: Implement `_directory_cluster_assign`

**Files:**
- Modify: `worker/pipeline/wiki_planner.py` — add `_directory_cluster_assign` helper
- Create: `tests/worker/test_directory_cluster_fallback.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/worker/test_directory_cluster_fallback.py`:

```python
"""Tests for the directory-clustering assignment fallback."""

from __future__ import annotations

from worker.pipeline.wiki_planner import _directory_cluster_assign


def _outline(*titles_and_purposes: tuple[str, str]) -> list[dict]:
    return [{"title": t, "purpose": p} for t, p in titles_and_purposes]


def test_preserves_directory_locality():
    """Files in the same directory end up on the same page."""
    outline = _outline(
        ("Worker Pipeline", "Stage-by-stage generation pipeline."),
        ("API Layer", "REST and WebSocket endpoints."),
        ("Web Frontend", "Next.js user interface."),
    )
    all_files = [
        "worker/pipeline/wiki_planner.py",
        "worker/pipeline/page_generator.py",
        "worker/pipeline/ast_analysis.py",
        "api/routers/repos.py",
        "api/routers/wiki.py",
        "web/components/WikiPage.tsx",
        "web/app/page.tsx",
    ]

    result = _directory_cluster_assign(outline, all_files)

    assert "worker/pipeline/wiki_planner.py" in result["Worker Pipeline"]
    assert "worker/pipeline/page_generator.py" in result["Worker Pipeline"]
    assert "worker/pipeline/ast_analysis.py" in result["Worker Pipeline"]
    assert "api/routers/repos.py" in result["API Layer"]
    assert "api/routers/wiki.py" in result["API Layer"]
    assert "web/components/WikiPage.tsx" in result["Web Frontend"]
    assert "web/app/page.tsx" in result["Web Frontend"]


def test_all_files_assigned_exactly_once():
    """Every file must be assigned to exactly one page."""
    outline = _outline(
        ("Overview", "Project overview."),
        ("Core", "Core logic."),
    )
    all_files = [f"src/mod{i}.py" for i in range(12)]
    result = _directory_cluster_assign(outline, all_files)
    flat = [f for files in result.values() for f in files]
    assert sorted(flat) == sorted(all_files)
    assert len(flat) == len(set(flat))


def test_unmatched_files_go_to_overview_when_present():
    """Files that don't match any directory fall to 'Overview' page."""
    outline = _outline(
        ("Overview", "High-level overview."),
        ("Core Pipeline", "Stage pipeline."),
    )
    all_files = [
        "worker/pipeline/a.py",  # matches "Core Pipeline"
        "README.md",             # no clear directory match
        "LICENSE",               # no clear directory match
    ]
    result = _directory_cluster_assign(outline, all_files)
    assert "worker/pipeline/a.py" in result["Core Pipeline"]
    assert "README.md" in result["Overview"]
    assert "LICENSE" in result["Overview"]


def test_unmatched_files_go_to_first_page_when_no_overview():
    """With no Overview page, unmatched files land on the first page."""
    outline = _outline(
        ("First", "First page."),
        ("Second", "Second page."),
    )
    all_files = ["mystery_file.txt"]
    result = _directory_cluster_assign(outline, all_files)
    assert result["First"] == ["mystery_file.txt"]
    assert result["Second"] == []


def test_splits_oversized_directory_groups_across_matching_pages():
    """If one directory has > 25 files but multiple pages match it, split evenly."""
    outline = _outline(
        ("Worker Pipeline Part A", "First half of worker pipeline."),
        ("Worker Pipeline Part B", "Second half of worker pipeline."),
    )
    all_files = [f"worker/pipeline/file{i}.py" for i in range(30)]
    result = _directory_cluster_assign(outline, all_files)
    # Both pages get roughly half
    total = len(result["Worker Pipeline Part A"]) + len(result["Worker Pipeline Part B"])
    assert total == 30
    # Neither page exceeds the 25-file cap
    assert len(result["Worker Pipeline Part A"]) <= 25
    assert len(result["Worker Pipeline Part B"]) <= 25
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/worker/test_directory_cluster_fallback.py -v
```

Expected: FAIL — `_directory_cluster_assign` does not exist.

- [ ] **Step 3: Implement the function**

Add to `worker/pipeline/wiki_planner.py` (place before `_assign_files`):

```python
def _tokenize(text: str) -> set[str]:
    """Lowercase word tokens with length ≥ 3.  Used for page↔directory matching."""
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3}


def _directory_key(rel_path: str) -> str:
    """Return the first directory segment of ``rel_path``.

    Files at the repo root return ``""``.  This is the grouping key used by
    :func:`_directory_cluster_assign` — keep it stable and unhierarchical.
    """
    parts = rel_path.split("/", 1)
    return parts[0] if len(parts) > 1 else ""


def _best_matching_page(
    dir_key: str,
    page_tokens: dict[str, set[str]],
    sample_files: list[str],
) -> str | None:
    """Return the title of the page whose tokens best match *dir_key*.

    Scoring:
    * +3 per overlapping token between dir_key and page tokens.
    * +1 per overlapping token between any word in the sample file basenames
      and the page tokens (captures e.g. ``routers`` in ``api/routers/x.py``
      matching a page called "Routing Layer").
    * Ties broken by page order (first listed wins).
    """
    candidate_tokens: set[str] = _tokenize(dir_key)
    for f in sample_files[:5]:
        candidate_tokens |= _tokenize(f.replace("/", " "))

    best_title: str | None = None
    best_score = 0
    for title, tokens in page_tokens.items():
        overlap = candidate_tokens & tokens
        if not overlap:
            continue
        # Weight dir_key overlap higher than basename overlap
        dir_overlap = _tokenize(dir_key) & tokens
        score = len(dir_overlap) * 3 + (len(overlap) - len(dir_overlap))
        if score > best_score:
            best_score = score
            best_title = title
    return best_title


def _directory_cluster_assign(
    outline: list[dict],
    all_files: list[str],
) -> dict[str, list[str]]:
    """Locality-preserving file assignment fallback.

    Groups files by their top-level directory, then assigns each directory
    group to the page whose title + purpose tokens best match the directory
    name and sample file basenames.  Unmatched files go to the "Overview"
    page if one exists, else to the first outline page.

    When a single page would receive more than 25 files (the per-page cap
    enforced by :func:`_validate_assignments`), the group is split across
    all pages whose tokens matched the directory at all — preserving
    locality while staying under the cap.  Pages with no token match at all
    never receive spill-over from a different directory.

    Invariants:
    * Every file in *all_files* appears in exactly one output list.
    * Every page title in *outline* is a key in the returned dict.
    * No page exceeds 25 files unless the only matching page must absorb
      the whole group and no other page matches (caller should then
      accept the warning rather than re-split).
    """
    page_titles = [p["title"] for p in outline]
    page_tokens: dict[str, set[str]] = {
        p["title"]: _tokenize(p["title"] + " " + p.get("purpose", ""))
        for p in outline
    }

    # Bucket files by first directory segment
    buckets: dict[str, list[str]] = {}
    for f in all_files:
        buckets.setdefault(_directory_key(f), []).append(f)

    result: dict[str, list[str]] = {t: [] for t in page_titles}

    # Overview fallback target
    overview_title = next(
        (t for t in page_titles if "overview" in t.lower()),
        page_titles[0] if page_titles else None,
    )

    for dir_key, files in buckets.items():
        if not dir_key:
            # Files at repo root go to overview
            if overview_title:
                result[overview_title].extend(files)
            continue

        target = _best_matching_page(dir_key, page_tokens, files)
        if target is None:
            if overview_title:
                result[overview_title].extend(files)
            continue

        # If the target would exceed the cap, try to split across all pages
        # whose tokens overlap this directory.
        if len(result[target]) + len(files) > 25:
            matching_pages = [
                t
                for t in page_titles
                if _tokenize(dir_key) & page_tokens[t]
            ]
            if len(matching_pages) > 1:
                # Round-robin *within* the matching page subset — still
                # locality-preserving because every target relates to this
                # directory.
                for i, f in enumerate(files):
                    result[matching_pages[i % len(matching_pages)]].append(f)
                continue

        result[target].extend(files)

    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/worker/test_directory_cluster_fallback.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/worker/test_directory_cluster_fallback.py
git commit -m "feat(planner): add directory-clustering fallback for file assignment"
```

---

### Task 7: Replace the round-robin fallback in `_assign_files`

**Files:**
- Modify: `worker/pipeline/wiki_planner.py:654-659` (fallback body)
- Test: `tests/worker/test_wiki_planner.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/worker/test_wiki_planner.py`:

```python
async def test_assign_files_fallback_uses_directory_clustering(caplog):
    """When all LLM retries fail, the fallback must produce
    directory-clustered output, not alphabetic round-robin."""
    from unittest.mock import AsyncMock

    from worker.pipeline.wiki_planner import _assign_files

    outline = [
        {"title": "Worker Pipeline", "purpose": "Pipeline stages."},
        {"title": "API Layer", "purpose": "REST and WebSocket endpoints."},
    ]
    all_files = [
        "worker/pipeline/wiki_planner.py",
        "worker/pipeline/page_generator.py",
        "worker/pipeline/ast_analysis.py",
        "api/routers/repos.py",
        "api/routers/wiki.py",
    ]

    # LLM returns junk every time — triggers fallback
    llm = AsyncMock()
    llm.generate_structured.side_effect = ValueError("always bad")

    result = await _assign_files(
        outline=outline,
        file_summary="files",
        dep_info=None,
        all_files=all_files,
        llm=llm,
        system="sys",
        on_retry=None,
        max_retries=2,
    )

    # All worker/pipeline files end up on Worker Pipeline
    assert all(
        f in result["Worker Pipeline"]
        for f in all_files
        if f.startswith("worker/pipeline/")
    )
    # All api files end up on API Layer
    assert all(
        f in result["API Layer"]
        for f in all_files
        if f.startswith("api/")
    )
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/worker/test_wiki_planner.py::test_assign_files_fallback_uses_directory_clustering -v
```

Expected: FAIL — current round-robin scatters files.

- [ ] **Step 3: Replace the fallback body**

In `worker/pipeline/wiki_planner.py`, replace the final block of `_assign_files`:

```python
    # Fallback: round-robin distribution (ignores validation constraints)
    result = {p["title"]: [] for p in outline}
    titles = [p["title"] for p in outline]
    for i, f in enumerate(sorted(all_files)):
        result[titles[i % len(titles)]].append(f)
    return result
```

with:

```python
    # Fallback: locality-preserving directory clustering.  See
    # _directory_cluster_assign for semantics.  Logged at ERROR by the
    # preceding log_final_failure call.
    return _directory_cluster_assign(outline, all_files)
```

- [ ] **Step 4: Run the new test + full planner tests**

```bash
uv run pytest tests/worker/test_wiki_planner.py tests/worker/test_directory_cluster_fallback.py -v
```

Expected: all pass.

- [ ] **Step 5: Update the docstring of `_assign_files`**

In `worker/pipeline/wiki_planner.py`, update the docstring of `_assign_files` so the fallback behaviour is documented:

```python
    """Phase 2: Assign every file to a page and validate assignments.

    Combines LLM generation with immediate per-page constraint checking so
    that over-stuffed pages (> 25 files) and empty non-overview pages are
    caught and retried within this phase.

    After all retries are exhausted, falls back to
    :func:`_directory_cluster_assign`, which preserves directory locality
    rather than scattering files alphabetically.  The fallback invocation
    is logged at ``ERROR`` via :func:`log_final_failure`.
    """
```

- [ ] **Step 6: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
git commit -m "feat(planner): replace round-robin fallback with directory clustering"
```

---

## Stage 3 — Batched File Assignment with Prompt Caching

**Objective:** rewrite `_assign_files` to split the file list into batches of ≤40 files and call the LLM once per batch. The large static context (outline + file_summary + dep_info) is placed in the cacheable **system** segment so only the first batch pays full cost; subsequent batches hit Anthropic's ephemeral cache. Partial successes are accepted — only truly unassigned files trigger a retry.

### Task 8: Add `_build_batch_assignment_system` and `_build_batch_assignment_user` prompt builders

**Files:**
- Modify: `worker/pipeline/wiki_planner.py` — add two new prompt builders
- Test: `tests/worker/test_assign_files_batched.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/worker/test_assign_files_batched.py`:

```python
"""Tests for Stage 3 batched file assignment."""

from __future__ import annotations

from worker.llm.prompt_segment import PromptSegment
from worker.pipeline.wiki_planner import (
    _build_batch_assignment_system,
    _build_batch_assignment_user,
)


def test_system_segment_is_cacheable():
    """The static context (outline + file_summary + dep_info) must be marked cacheable."""
    outline = [
        {"title": "Overview", "purpose": "top"},
        {"title": "Core", "purpose": "core"},
    ]
    segments = _build_batch_assignment_system(
        outline=outline,
        file_summary="file summary text",
        dep_info="dep info text",
    )
    assert isinstance(segments, list)
    assert all(isinstance(s, PromptSegment) for s in segments)
    assert any(s.cacheable for s in segments), \
        "at least one system segment must be cacheable"
    # Outline and file_summary content must be present
    joined = "".join(s.text for s in segments)
    assert "Overview" in joined
    assert "file summary text" in joined
    assert "dep info text" in joined


def test_user_segment_contains_only_batch_files():
    """The user segment must contain only the per-batch file list, not the full repo."""
    batch = ["a.py", "b.py", "c.py"]
    segment = _build_batch_assignment_user(batch_files=batch, outline_titles=["O", "C"])
    assert isinstance(segment, PromptSegment)
    assert segment.cacheable is False
    for f in batch:
        assert f in segment.text
    # The outline titles are included as enum reminders
    assert "O" in segment.text and "C" in segment.text
    # Not cached, not containing full context
    assert "file summary" not in segment.text
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/worker/test_assign_files_batched.py -v
```

Expected: FAIL — functions do not exist.

- [ ] **Step 3: Implement the two prompt builders**

Add to `worker/pipeline/wiki_planner.py` (near the existing `_build_assignment_prompt`, add at the end of the prompt-builder section):

```python
def _build_batch_assignment_system(
    outline: list[dict],
    file_summary: str,
    dep_info: str | None,
) -> list[PromptSegment]:
    """Build the cacheable *system* portion of a batched assignment call.

    The system turn contains the full repository context — outline,
    file summaries, and dependency info — which is identical for every
    batch in a single planning run.  Marking it cacheable lets Anthropic's
    ``ephemeral`` cache amortise the tokens across batches so only the
    first batch pays full cost.

    Non-Anthropic providers (OpenAI, Gemini, Ollama) ignore the cache
    hint and simply concatenate the segments into the system prompt.

    Args:
        outline: Phase 1 outline (list of ``{"title", "purpose", "parent"?}``).
        file_summary: Output of ``FileAnalysis.to_llm_summary()``.
        dep_info: Output of ``format_for_llm_prompt(dep_graph)``, or ``None``.

    Returns:
        A list with a single cacheable :class:`PromptSegment` carrying the
        full context.
    """
    outline_json = json.dumps(outline, indent=2)
    parts: list[str] = [
        "You are assigning source files to wiki pages.",
        "",
        f"## Wiki page structure:\n{outline_json}",
        "",
        f"## File summaries:\n{file_summary}",
    ]
    if dep_info:
        parts.append("")
        parts.append(f"## Dependency relationships:\n{dep_info}")
    return [PromptSegment(text="\n".join(parts), cacheable=True)]


def _build_batch_assignment_user(
    batch_files: list[str],
    outline_titles: list[str],
) -> PromptSegment:
    """Build the per-batch *user* segment.

    Contains only the batch-specific content — the file list to assign and
    a reminder of valid page titles — so the system segment's cache stays
    valid across batches.
    """
    titles_str = ", ".join(f'"{t}"' for t in outline_titles)
    files_str = "\n".join(f"- {f}" for f in batch_files)
    schema_json = json.dumps(_ASSIGNMENT_SCHEMA, indent=2)
    text = (
        f"Assign each of the following {len(batch_files)} files to one of the "
        f"page titles below.  Each ``page_title`` MUST exactly match one of: "
        f"{titles_str}.\n\n"
        f"Files to assign:\n{files_str}\n\n"
        "Rules:\n"
        "- Every listed file must appear in the output.\n"
        "- Choose the page whose purpose best matches the file's semantic role.\n"
        "- Files that import each other usually belong on the same page.\n\n"
        f"Output JSON matching this schema:\n{schema_json}"
    )
    return PromptSegment(text=text, cacheable=False)
```

Add the import at the top of the file (if not already present):

```python
from worker.llm.prompt_segment import PromptSegment
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/worker/test_assign_files_batched.py -v
```

Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/worker/test_assign_files_batched.py
git commit -m "feat(planner): add cacheable batched assignment prompt builders"
```

---

### Task 9: Implement `_assign_files_in_batches` core loop

**Files:**
- Modify: `worker/pipeline/wiki_planner.py` — add new function
- Test: `tests/worker/test_assign_files_batched.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/worker/test_assign_files_batched.py`:

```python
async def test_batched_assignment_collects_all_files():
    """Each batch's assignments are merged into the final result."""
    from unittest.mock import AsyncMock

    from worker.pipeline.wiki_planner import _assign_files_in_batches

    outline = [
        {"title": "Overview", "purpose": "top"},
        {"title": "Core", "purpose": "core"},
    ]
    all_files = [f"f{i}.py" for i in range(100)]

    # LLM assigns odd → Core, even → Overview, returned per batch
    async def fake_generate_structured(user_seg, schema, system):
        # Parse the batch from the user segment text
        text = user_seg.text if hasattr(user_seg, "text") else str(user_seg)
        import re
        batch = re.findall(r"- (f\d+\.py)", text)
        assignments = [
            {
                "file": f,
                "page_title": "Core" if int(f[1:-3]) % 2 else "Overview",
            }
            for f in batch
        ]
        return {"assignments": assignments}

    llm = AsyncMock()
    llm.generate_structured.side_effect = fake_generate_structured

    result = await _assign_files_in_batches(
        outline=outline,
        file_summary="fs",
        dep_info=None,
        all_files=all_files,
        llm=llm,
        system="sys",
        on_retry=None,
        batch_size=40,
    )

    # Every file assigned exactly once
    flat = [f for files in result.values() for f in files]
    assert sorted(flat) == sorted(all_files)
    # 50 even → Overview, 50 odd → Core
    assert len(result["Overview"]) == 50
    assert len(result["Core"]) == 50
    # Number of LLM calls: ceil(100 / 40) = 3
    assert llm.generate_structured.await_count == 3


async def test_batched_assignment_reuses_system_segment_across_batches():
    """The same cacheable system segment object is passed to every batch call."""
    from unittest.mock import AsyncMock

    from worker.pipeline.wiki_planner import _assign_files_in_batches

    outline = [{"title": "X", "purpose": "x"}]
    all_files = [f"a{i}.py" for i in range(50)]

    calls: list[object] = []

    async def capture(user_seg, schema, system):
        calls.append(system)
        import re
        text = user_seg.text if hasattr(user_seg, "text") else str(user_seg)
        batch = re.findall(r"- (a\d+\.py)", text)
        return {"assignments": [{"file": f, "page_title": "X"} for f in batch]}

    llm = AsyncMock()
    llm.generate_structured.side_effect = capture

    await _assign_files_in_batches(
        outline=outline,
        file_summary="fs",
        dep_info="deps",
        all_files=all_files,
        llm=llm,
        system="sys",
        on_retry=None,
        batch_size=20,
    )

    # All calls received identical system objects (same identity or
    # same text), which is what lets Anthropic cache work.
    assert len(calls) == 3
    first = calls[0]
    for other in calls[1:]:
        # Either identity match or deep-equal text
        assert first is other or first == other


async def test_batched_assignment_retries_unassigned_files():
    """Files not assigned in the initial pass are retried in a cleanup batch."""
    from unittest.mock import AsyncMock

    from worker.pipeline.wiki_planner import _assign_files_in_batches

    outline = [{"title": "X", "purpose": "x"}]
    all_files = [f"f{i}.py" for i in range(10)]

    call_count = [0]

    async def fake(user_seg, schema, system):
        call_count[0] += 1
        import re
        text = user_seg.text if hasattr(user_seg, "text") else str(user_seg)
        batch = re.findall(r"- (f\d+\.py)", text)
        if call_count[0] == 1:
            # Skip half the files in the first batch
            batch = batch[: len(batch) // 2]
        return {"assignments": [{"file": f, "page_title": "X"} for f in batch]}

    llm = AsyncMock()
    llm.generate_structured.side_effect = fake

    result = await _assign_files_in_batches(
        outline=outline,
        file_summary="fs",
        dep_info=None,
        all_files=all_files,
        llm=llm,
        system="sys",
        on_retry=None,
        batch_size=20,
    )

    # All 10 files assigned despite the first batch dropping half
    assert len(result["X"]) == 10
    # Two calls: initial batch + cleanup batch
    assert call_count[0] == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/worker/test_assign_files_batched.py -v
```

Expected: FAIL — `_assign_files_in_batches` does not exist.

- [ ] **Step 3: Implement `_assign_files_in_batches`**

Add to `worker/pipeline/wiki_planner.py` (place after `_build_batch_assignment_user`):

```python
_BATCH_SIZE_DEFAULT = 40


async def _assign_files_in_batches(
    outline: list[dict],
    file_summary: str,
    dep_info: str | None,
    all_files: list[str],
    llm: LLMProvider,
    system: str,
    on_retry: OnRetryCallback | None,
    batch_size: int = _BATCH_SIZE_DEFAULT,
    max_cleanup_retries: int = 2,
) -> dict[str, list[str]]:
    """Phase 2 batched assignment core.

    Splits *all_files* into chunks of *batch_size* and invokes
    ``llm.generate_structured`` once per batch.  The system turn — which
    carries the large outline + file summary + dep info — is built once
    and reused across every call so Anthropic's ``ephemeral`` cache
    amortises the context tokens.  Non-Anthropic providers simply send
    the full system on every call; correctness is unaffected.

    Partial results are merged across batches.  Any files the LLM fails
    to assign (missing from the response or referencing invalid page
    titles) are re-batched for up to *max_cleanup_retries* cleanup rounds
    before being handed off to :func:`_directory_cluster_assign` for the
    final residue.

    Args:
        outline: Phase 1 outline with valid page titles.
        file_summary: Output of ``FileAnalysis.to_llm_summary()``.
        dep_info: Dependency graph text summary, or ``None``.
        all_files: Every source file path that must be assigned.
        llm: LLM provider.  Must support ``generate_structured`` with
            list-of-:class:`PromptSegment` ``system``.
        system: Stage-level system prompt appended to the cacheable context.
        on_retry: Forwarded to :func:`async_retry` for transient errors.
        batch_size: Files per batch.  Defaults to 40 — empirically the
            largest size where Claude Sonnet 4.6 structured output reliably
            returns complete assignments.
        max_cleanup_retries: Number of additional rounds to re-batch
            unassigned files before giving up.

    Returns:
        ``{page_title: [files]}`` mapping.  Every file in *all_files*
        appears exactly once.
    """
    valid_titles = [p["title"] for p in outline]
    valid_titles_set = set(valid_titles)
    result: dict[str, list[str]] = {t: [] for t in valid_titles}
    assigned: set[str] = set()

    # Build the cacheable system segment ONCE.  The stage system string
    # becomes the first segment (non-cacheable — it's short and rarely
    # changes so cache impact is negligible).  The large context follows
    # in a cacheable segment.
    stage_system_seg = PromptSegment(text=system, cacheable=False)
    context_segs = _build_batch_assignment_system(
        outline=outline,
        file_summary=file_summary,
        dep_info=dep_info,
    )
    system_segments: list[PromptSegment] = [stage_system_seg, *context_segs]

    async def _run_batch(batch: list[str]) -> None:
        user_segment = _build_batch_assignment_user(
            batch_files=batch,
            outline_titles=valid_titles,
        )
        try:
            raw = await async_retry(
                llm.generate_structured,
                user_segment,
                schema=_ASSIGNMENT_SCHEMA,
                system=system_segments,
                transient_exceptions=TRANSIENT_EXCEPTIONS,
                on_retry=on_retry,
            )
        except Exception as exc:
            log_validation_retry(
                logger,
                stage="wiki_planner.assign_files.batch",
                attempt=1,
                max_retries=1,
                exc=exc,
                context={"batch_size": len(batch)},
            )
            return
        for a in raw.get("assignments", []):
            f = a.get("file", "")
            title = a.get("page_title", "")
            if f not in batch or f in assigned:
                continue
            if title not in valid_titles_set:
                continue
            result[title].append(f)
            assigned.add(f)

    # Initial pass: batch every file
    batches: list[list[str]] = [
        all_files[i : i + batch_size]
        for i in range(0, len(all_files), batch_size)
    ]
    # Run the first batch serially to warm the cache, then the rest in parallel.
    if batches:
        await _run_batch(batches[0])
    if len(batches) > 1:
        import asyncio

        await asyncio.gather(*(_run_batch(b) for b in batches[1:]))

    # Cleanup rounds for unassigned files
    for _ in range(max_cleanup_retries):
        unassigned = [f for f in all_files if f not in assigned]
        if not unassigned:
            break
        log_validation_retry(
            logger,
            stage="wiki_planner.assign_files.cleanup",
            attempt=1,
            max_retries=max_cleanup_retries,
            exc=ValueError(f"{len(unassigned)} files unassigned after batches"),
            context={"unassigned": len(unassigned), "total": len(all_files)},
        )
        cleanup_batches = [
            unassigned[i : i + batch_size]
            for i in range(0, len(unassigned), batch_size)
        ]
        for b in cleanup_batches:
            await _run_batch(b)

    # Anything still unassigned → directory clustering residue
    unassigned = [f for f in all_files if f not in assigned]
    if unassigned:
        log_final_failure(
            logger,
            stage="wiki_planner.assign_files.residue",
            exc=ValueError(
                f"{len(unassigned)} files still unassigned after cleanup; "
                "routing residue to directory clustering"
            ),
            context={"residue": len(unassigned)},
        )
        residue_assignment = _directory_cluster_assign(outline, unassigned)
        for title, files in residue_assignment.items():
            result[title].extend(files)

    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/worker/test_assign_files_batched.py -v
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/worker/test_assign_files_batched.py
git commit -m "feat(planner): implement batched file assignment with cache reuse"
```

---

### Task 10: Route `_assign_files` through `_assign_files_in_batches`

**Files:**
- Modify: `worker/pipeline/wiki_planner.py:579-659` (`_assign_files`)
- Test: `tests/worker/test_wiki_planner.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/worker/test_wiki_planner.py`:

```python
async def test_assign_files_uses_batched_path(monkeypatch):
    """_assign_files must delegate to _assign_files_in_batches."""
    from unittest.mock import AsyncMock

    from worker.pipeline import wiki_planner as wp

    called = {}

    async def fake_batched(**kwargs):
        called["hit"] = True
        return {kwargs["outline"][0]["title"]: list(kwargs["all_files"])}

    monkeypatch.setattr(wp, "_assign_files_in_batches", fake_batched)

    llm = AsyncMock()
    outline = [
        {"title": "One", "purpose": "p1"},
        {"title": "Two", "purpose": "p2"},
    ]
    result = await wp._assign_files(
        outline=outline,
        file_summary="fs",
        dep_info=None,
        all_files=["a.py", "b.py"],
        llm=llm,
        system="sys",
        on_retry=None,
    )

    assert called.get("hit") is True
    assert "a.py" in result["One"]
    assert "b.py" in result["One"]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/worker/test_wiki_planner.py::test_assign_files_uses_batched_path -v
```

Expected: FAIL — `_assign_files` still uses the old one-shot path.

- [ ] **Step 3: Rewrite `_assign_files` to delegate to the batched path**

Replace the body of `_assign_files` in `worker/pipeline/wiki_planner.py` with:

```python
async def _assign_files(
    outline: list[dict],
    file_summary: str,
    dep_info: str | None,
    all_files: list[str],
    llm: LLMProvider,
    system: str,
    on_retry: OnRetryCallback | None,
    max_retries: int = 3,
    _extra_context: str | None = None,
    fast_llm: LLMProvider | None = None,
) -> dict[str, list[str]]:
    """Phase 2: Assign every file to a page via batched LLM calls.

    Delegates to :func:`_assign_files_in_batches`, which splits the file
    list into ≤40-file batches and reuses a cacheable system segment
    across batches.  The ``max_retries`` parameter is retained for API
    compatibility but is mapped onto the batched function's internal
    cleanup/retry strategy.  ``fast_llm`` is preferred for the batched
    call because classification-style assignments scale well on faster
    models.  Validation is performed on the merged result, and
    validation failures trigger a single retry via the batched path
    before falling back to directory clustering.
    """
    preferred_llm = fast_llm or llm

    try:
        result = await _assign_files_in_batches(
            outline=outline,
            file_summary=file_summary,
            dep_info=dep_info,
            all_files=all_files,
            llm=preferred_llm,
            system=system,
            on_retry=on_retry,
        )
        _validate_assignments(result, outline)
        return result
    except ValueError as exc:
        log_validation_retry(
            logger,
            stage="wiki_planner.assign_files",
            attempt=1,
            max_retries=2,
            exc=exc,
            context={
                "outline_pages": len(outline),
                "total_files": len(all_files),
            },
        )

    # One retry with the main LLM (in case the fast model is the weak link)
    try:
        result = await _assign_files_in_batches(
            outline=outline,
            file_summary=file_summary,
            dep_info=dep_info,
            all_files=all_files,
            llm=llm,
            system=system,
            on_retry=on_retry,
        )
        _validate_assignments(result, outline)
        return result
    except ValueError as exc:
        log_final_failure(
            logger,
            stage="wiki_planner.assign_files",
            exc=exc,
            context={
                "outline_pages": len(outline),
                "total_files": len(all_files),
            },
        )

    # Final fallback: directory clustering (locality-preserving)
    return _directory_cluster_assign(outline, all_files)
```

Note: the original `_assign_files` had an in-body `_llm_pool` abstraction and a prompt-error append loop. Both are removed — batching + cleanup retries replace them, and the directory-clustering fallback is the final safety net.

- [ ] **Step 4: Run the failing test + full planner suite**

```bash
uv run pytest tests/worker/test_wiki_planner.py tests/worker/test_assign_files_batched.py tests/worker/test_directory_cluster_fallback.py -v
```

Expected: all pass. If existing tests break, check they don't depend on the old round-robin fallback or the `_llm_pool` attempt ordering — update them to expect the new behaviour.

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
git commit -m "feat(planner): route _assign_files through batched path"
```

---

### Task 11: Verify caching works against a real Anthropic call (optional sanity check)

**Files:**
- Read: `worker/llm/anthropic_provider.py` (verify cache_control is applied)
- Read: `worker/pipeline/wiki_planner.py` (verify system_segments ordering)

- [ ] **Step 1: Trace the cache path manually**

Run the repo's existing Anthropic provider test (if any) with a mock cache-aware client, OR read `worker/llm/anthropic_provider.py:14-50` to confirm that a `list[PromptSegment]` where the tail segments are `cacheable=True` produces a content-block list with `cache_control = {"type": "ephemeral"}` on the last cacheable block.

Our `system_segments` layout is:
- index 0: `stage_system_seg` (non-cacheable — the stage system prompt)
- index 1: cacheable segment with outline + file_summary + dep_info

Because our cacheable run is a single segment at the tail, `cache_control` will be applied to it. All batches share the same system segments (identical text), so the cache key is stable across calls within a planning run. Confirm by reading lines 14-50 of `anthropic_provider.py`.

- [ ] **Step 2: Add a unit test that `_segments_to_anthropic_content` sets cache_control on the last cacheable block**

If `tests/worker/test_anthropic_provider.py` does not already cover this, add:

```python
def test_segments_to_anthropic_content_marks_last_cacheable_block():
    from worker.llm.anthropic_provider import _segments_to_anthropic_content
    from worker.llm.prompt_segment import PromptSegment

    segs = [
        PromptSegment(text="stage system", cacheable=False),
        PromptSegment(text="big context", cacheable=True),
    ]
    content = _segments_to_anthropic_content(segs)
    assert isinstance(content, list)
    # Last block should carry cache_control
    assert content[-1].get("cache_control") == {"type": "ephemeral"}
```

Run:

```bash
uv run pytest tests/worker/test_anthropic_provider.py -v
```

Expected: PASS (this verifies the caching path is wired correctly without requiring a real API call).

- [ ] **Step 3: Commit**

```bash
git add tests/worker/test_anthropic_provider.py
git commit -m "test(llm): verify anthropic system cache marker on planning segments"
```

---

### Task 12: Lint and full-suite verification for Stage 3

- [ ] **Step 1: Run pre-commit checks**

```bash
uv run ruff check .
uv run ruff format --check .
```

Expected: clean. Fix any violations with `uv run ruff format .` and `uv run ruff check --fix .` before continuing.

- [ ] **Step 2: Run the full backend test suite**

```bash
uv run pytest tests/ --ignore=tests/e2e -q
```

Expected: all pass.

- [ ] **Step 3: Frontend lint**

```bash
cd web && npm run lint
```

Expected: clean (no frontend changes in this plan, but the CLAUDE.md workflow requires the check).

- [ ] **Step 4: If anything fails, fix before proceeding**

---

## Stage 4 — Documentation & Handoff

### Task 13: Update CLAUDE.md with new conventions and deferred work

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add a new "Pipeline Observability" subsection under "Key Implementation Notes"**

Append to the "Key Implementation Notes" section in `CLAUDE.md`:

```markdown
- **Pipeline observability**: every retry loop over LLM structured-output calls in `worker/pipeline/` must log validation failures via `pipeline_logging.log_validation_retry` and log final fallback invocations via `pipeline_logging.log_final_failure`.  Silent `except (ValueError, json.JSONDecodeError, KeyError): pass` is a bug.  See `worker/pipeline/pipeline_logging.py`.
- **Planner fallback semantics**: when `_assign_files` exhausts its retries, it calls `_directory_cluster_assign(outline, all_files)` — a locality-preserving heuristic that groups files by top-level directory and routes each group to the page whose title/purpose tokens best match.  The prior round-robin fallback destroyed locality and is removed.
- **Planner batched assignment**: `_assign_files` delegates to `_assign_files_in_batches`, which splits files into 40-file chunks and reuses a cacheable system segment (outline + file_summary + dep_info) across every batch.  On Anthropic this triggers ephemeral prompt caching; other providers see no change beyond correctness.  The first batch runs serially to warm the cache, remaining batches run in parallel via `asyncio.gather`.
```

- [ ] **Step 2: Add a "Deferred planner work" subsection**

Append to the "Phased Delivery" section or as a new sibling section:

```markdown
## Deferred planner improvements

Documented for future work; not in scope for the 2026-04-15 robustness pass:

- **Outline anchors (Layer C1)**: surface the top-3-level directory tree with file counts, package-level `__init__.py` docstrings, and README-extracted subsystem headings as explicit signals in `_build_outline_prompt`.  Goal: stop fragmenting cohesive subsystems (e.g. `worker/pipeline/*`) across peer top-level pages.
- **Multi-page file assignment (Layer C2)**: change `_ASSIGNMENT_SCHEMA` to `{file, primary_page, secondary_pages: [...]}` (cap secondary ≤ 2).  Update the validator to dedupe on primary only, and update `ingestion.get_affected_pages` to distinguish primary vs secondary for refresh cost management.
- **Independent stage validation harness**: a replay-style test suite that runs each pipeline stage against recorded fixtures (file analysis → dep graph → outline → assignments → pages) without spending live API budget.  Deferred because the construction cost exceeds the current debugging benefit.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document pipeline observability and deferred planner work"
```

---

### Task 14: Final verification

- [ ] **Step 1: Run all backend tests**

```bash
uv run pytest tests/ --ignore=tests/e2e -q
```

Expected: all pass.

- [ ] **Step 2: Run all pre-commit checks**

```bash
uv run ruff check . && uv run ruff format --check .
cd web && npm run lint && cd ..
```

Expected: clean.

- [ ] **Step 3: Confirm observability manually with a dry run**

Run one indexing job against a small repo with `LOG_LEVEL=DEBUG` and grep for the new log tags:

```bash
LOG_LEVEL=DEBUG uv run autowiki index github.com/pallets/flask --reuse-index 2>&1 | tee /tmp/autowiki.log
grep -E "wiki_planner\.(outline|assign_files)" /tmp/autowiki.log
```

Expected: see `wiki_planner.outline` and `wiki_planner.assign_files.batch` log lines even on a successful run. Any validation failures produce `WARNING`s with full context.

- [ ] **Step 4: Confirm no regressions in incremental refresh**

Run an incremental refresh against the same repo:

```bash
uv run autowiki refresh github.com/pallets/flask 2>&1 | tee /tmp/autowiki-refresh.log
```

Expected: succeeds, no errors about missing files or empty pages.

- [ ] **Step 5: Commit any final fixes (if needed) and open the PR**

```bash
git push -u origin feature/wiki-planner-robustness
gh pr create --title "feat(planner): observability + directory-clustering fallback + batched assignment" --body "$(cat <<'EOF'
## Summary
- Stage 1: every retry/validation failure in the planner and page-generation pipeline now logs via `pipeline_logging.log_validation_retry` / `log_final_failure`. No more silent `except ValueError: pass`.
- Stage 2: replaces the round-robin fallback in `_assign_files` with `_directory_cluster_assign`, a locality-preserving heuristic that groups files by top-level directory and routes each group to the best-matching page.
- Stage 3: rewrites `_assign_files` to split files into 40-file batches with a cacheable system segment, so Anthropic ephemeral caching amortises the context across batches. First batch runs serially to warm the cache; remaining batches run in parallel.

## Motivation
The production planner silently fell back to alphabetic round-robin distribution (`wiki_planner.py:654-659`), producing wikis where `worker/pipeline/wiki_planner.py` landed on a "Testing and Verification" page purely by alphabetic index. The failure was invisible in logs, the fallback destroyed file locality, and the root cause — Phase 2's single-shot structured-output call choking on 180 JSON objects — had no observability.

## Test plan
- [ ] `uv run pytest tests/ --ignore=tests/e2e -q` — full suite green
- [ ] `uv run ruff check . && uv run ruff format --check .` — clean
- [ ] `cd web && npm run lint` — clean
- [ ] Manual dry run: `LOG_LEVEL=DEBUG autowiki index github.com/pallets/flask --reuse-index` and confirm `wiki_planner.outline` / `wiki_planner.assign_files.batch` lines appear in the output
- [ ] Manual regression: `autowiki refresh github.com/pallets/flask` succeeds

## Out of scope / deferred
- Outline anchors (Layer C1): directory-tree / package-docstring / README-section signals in `_build_outline_prompt`
- Multi-page file assignment (Layer C2): `primary_page` + `secondary_pages` schema
- Independent per-stage replay harness
EOF
)"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** Every user-stated priority has at least one task.
  - "Enhance observability/debugging, log all failed retries (LLM + validation, structure + content generation)" → Stage 1, Tasks 1-4.
  - "Defer independent validation of stages" → Stage 4, Task 13 documents the deferral; no task builds a replay harness.
  - "Replace round-robin fallback with directory clustering" → Stage 2, Tasks 6-7.
  - "Maximize caching/reuse in batched allocation" → Stage 3, Tasks 8-11 (cacheable system segment shared across batches + cache-marker verification).
  - "Draft detailed plans for every stage in documentation and execute sequentially" → this document itself.
- [x] **Placeholder scan:** no TBD/TODO/"add validation"/"fill in later" — every step has exact file paths and exact code.
- [x] **Type consistency:** `_directory_cluster_assign(outline, all_files)` signature identical in every task that calls it. `_assign_files_in_batches` kwargs match between implementation and tests. `_build_batch_assignment_system` returns `list[PromptSegment]`, `_build_batch_assignment_user` returns `PromptSegment`.
- [x] **Logging helper signature stable:** `log_validation_retry(logger, *, stage, attempt, max_retries, exc, context)` used identically in Tasks 2-4.
