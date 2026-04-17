# PR #22 Review Issues Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve all CodeRabbit review issues for PR #22, covering safety, performance (async I/O), and correctness (secondary file handling).

**Architecture:** Surgical fixes across CLI, Worker, and Pipeline components to ensure architectural consistency with the Layer C2 (multi-page assignment) and Layer C1 (anchors) designs.

**Tech Stack:** Python 3.12, asyncio, Typer, ruff (linting).

---

## Tasks

### Task 1: Fix `page_generator` Pass-4 Revision Context

**Files:**
- Modify: `worker/pipeline/page_generator.py`

- [ ] **Step 1: Update Pass-4 to include secondary context**

Modify `worker/pipeline/page_generator.py` around line 283:

```python
    # ── Pass 4: Targeted revision (main model, conditional) ──
    if fc_result.verdict == "fail" and fc_result.issues:
        context_segments = build_draft_prompt(
            spec=spec,
            outline=outline,
            context_chunks=context_chunks,
            repo_name=repo_name,
            dep_info=dep_info,
            entity_details=entity_details,
            child_contents=child_contents,
            repo_notes=repo_notes,
            secondary_block=secondary_block or None, # Add this
        )
```

- [ ] **Step 2: Update `build_draft_prompt` signature if needed**

Verify if `build_draft_prompt` in `worker/pipeline/page_generator.py` (or wherever it's defined, likely same file) accepts `secondary_block`. If not, add it.

- [ ] **Step 3: Commit**

```bash
git add worker/pipeline/page_generator.py
git commit -m "fix(page_generator): carry secondary_block through to Pass-4 revision"
```

### Task 2: Fix `worker/jobs.py` Secondary File Handling and Async I/O

**Files:**
- Modify: `worker/jobs.py`

- [ ] **Step 1: Restore `secondary_files` in `old_plan` hydration**

Around line 833 in `worker/jobs.py`:

```python
            pages=[
                WikiPageSpec(
                    title=p["title"],
                    purpose=p.get("purpose", ""),
                    parent=p.get("parent"),
                    files=p.get("files", []),
                    secondary_files=p.get("secondary_files", []), # Add this
                    # Merge saved page_notes back into the spec; default to empty note
                    page_notes=saved_page_notes.get(p["title"], [{"content": ""}]),
                )
                for p in plan_data.get("pages", [])
            ],
```

- [ ] **Step 2: Update `affected_files_set` to include secondary files**

Around line 1013 in `worker/jobs.py`:

```python
        # Stage 5: Re-plan for affected pages
        # ...
        affected_files_set = {
            f
            for p in old_plan.pages
            if p.title in affected_page_titles
            for f in [*(p.files or []), *(p.secondary_files or [])] # Include secondary_files
        }
```

- [ ] **Step 3: Move `stale_path` operations off the event loop**

Around line 850 in `worker/jobs.py`:

```python
        stale_path = ast_dir / "stale_secondary.json"
        prior_stale: set[str] = set()
        
        def _read_stale():
            if not stale_path.exists():
                return set()
            try:
                data = set(json.loads(stale_path.read_text()))
                stale_path.unlink()
                return data
            except (OSError, json.JSONDecodeError):
                return set()

        prior_stale = await loop.run_in_executor(None, _read_stale)
```

- [ ] **Step 4: Commit**

```bash
git add worker/jobs.py
git commit -m "fix(jobs): restore secondary_files and offload stale_path I/O to executor"
```

### Task 3: Refactor `validate-plan` CLI Loader

**Files:**
- Modify: `cli/commands/validate_plan.py`

- [ ] **Step 1: Update `_load_plan` to avoid pre-filling defaults**

```python
def _load_plan(plan_path: Path) -> tuple[dict, WikiPlan]:
    data = json.loads(plan_path.read_text())
    # No pre-filling WikiPageSpec here, just return raw data first or 
    # use a minimal constructor that doesn't mask errors.
    pages = [
        WikiPageSpec(
            title=p.get("title", ""), # Don't provide fallback if it should fail validation
            purpose=p.get("purpose", ""),
            parent=p.get("parent"),
            files=p.get("files", []),
            secondary_files=p.get("secondary_files", []),
        )
        for p in data.get("pages", [])
    ]
    return data, WikiPlan(repo_notes=data.get("repo_notes", []), pages=pages)
```

Actually, CodeRabbit suggested running `validate_wiki_plan` on raw payload first.

- [ ] **Step 2: Commit**

```bash
git add cli/commands/validate_plan.py
git commit -m "refactor(cli): validate raw JSON in validate-plan before normalization"
```

### Task 4: Enhance `FixtureRecorder` with Async I/O and Error Handling

**Files:**
- Modify: `worker/pipeline/fixture_recorder.py`

- [ ] **Step 1: Make `FixtureRecorder` methods async and use `run_in_executor`**

- [ ] **Step 2: Add try/except in `_write` to catch and log errors**

- [ ] **Step 3: Update `wiki_planner.py` to await recorder calls**

- [ ] **Step 4: Commit**

```bash
git add worker/pipeline/fixture_recorder.py worker/pipeline/wiki_planner.py
git commit -m "fix(recorder): offload I/O to executor and add error handling"
```

### Task 5: Secure and Offload `outline_anchors` I/O

**Files:**
- Modify: `worker/pipeline/outline_anchors.py`

- [ ] **Step 1: Add path traversal protection in `extract_package_docstrings`**

- [ ] **Step 2: Offload `path.read_text` to `run_in_executor`**

- [ ] **Step 3: Commit**

```bash
git add worker/pipeline/outline_anchors.py
git commit -m "fix(anchors): add path protection and offload I/O to executor"
```

### Task 6: Refactor `wiki_planner` Logging and Exceptions

**Files:**
- Modify: `worker/pipeline/wiki_planner.py`

- [ ] **Step 1: Use bare `raise` in `except WikiPlannerError`**

- [ ] **Step 2: Fix long line in `_assign_files` ValueError**

- [ ] **Step 3: Refactor long `logger.warning` in `generate_wiki_plan`**

- [ ] **Step 4: Commit**

```bash
git add worker/pipeline/wiki_planner.py
git commit -m "style(planner): refactor long lines and use idiomatic raise"
```

### Task 7: Update Docker Compose Env Interpolation

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Use `${VAR}` for all bare environment variables**

- [ ] **Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "fix(infra): use Compose interpolation for environment variables"
```

### Task 8: Update Ingestion Test

**Files:**
- Modify: `tests/worker/test_ingestion.py`

- [ ] **Step 1: Update `test_get_affected_pages_primary_wins_over_secondary` to verify disjoint sets**

- [ ] **Step 2: Commit**

```bash
git add tests/worker/test_ingestion.py
git commit -m "test(ingestion): verify primary/secondary affected pages are disjoint"
```

### Task 9: Final Verification

- [ ] **Step 1: Run all tests**

```bash
uv run pytest tests/ --ignore=tests/e2e
```

- [ ] **Step 2: Run lint**

```bash
uv run ruff check .
```
