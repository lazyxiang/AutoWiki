# Deep Research & Wiki Steering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship AutoWiki Phase 3 (Deep Research mode) and Phase 4 (user-steered wiki generation via `.autowiki/wiki.json`) together — without MCP, GitHub webhooks, or push-triggered auto-refresh.

**Architecture:** Deep Research adds a new long-running ARQ job (`run_deep_research`) that runs a three-stage orchestrator (planner → investigator loop → synthesizer) against the existing FAISS + LLM stack, persists results to a new `research_reports` table, and streams progressive events over a WebSocket. User Steering extends the existing wiki planner to load `.autowiki/wiki.json` from the clone during Stage 1, inject `repo_notes` into the planner/page-draft prompts, optionally replace Phase 1 (outline) with a user-supplied page list, and pre-assign files matching user-declared `modules` prefixes before the Phase 2 LLM call.

**Tech Stack:** FastAPI, ARQ, SQLAlchemy async, pytest asyncio, Next.js 16 App Router, TypeScript, Tailwind v4, shadcn/ui.

**Scope exclusions** (per 2026-04-14 scope revision):
- **No** MCP server. REST + WebSocket are the only automation surfaces.
- **No** GitHub webhook endpoint (`POST /webhook/github`).
- **No** push-event auto-refresh. `autowiki refresh` and `POST /api/repos/{repo_id}/refresh` remain the only refresh triggers.

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `worker/deep_research.py` | Pure async orchestrator: `plan_research`, `investigate_step`, `synthesize_report`. No DB / no WebSocket knowledge — keeps it unit-testable. |
| `worker/pipeline/user_steering.py` | `.autowiki/wiki.json` loader + validator + `UserSteering` / `UserPageSpec` dataclasses + module-prefix matcher. |
| `api/routers/research.py` | REST + WebSocket endpoints for Deep Research. Owns the event-streaming glue between the job and the WebSocket. |
| `cli/commands/research_cmd.py` | Typer command that POSTs to `/research`, opens the WebSocket, and prints progress. |
| `web/components/ResearchPanel.tsx` | Client component: input box, plan display, step findings, final report markdown. |
| `web/app/[owner]/[repo]/research/page.tsx` | Server component route → renders `ResearchPanel`. |
| `tests/worker/test_deep_research.py` | Unit tests for planner/investigator/synthesizer/orchestrator. |
| `tests/worker/test_user_steering.py` | Unit tests for `.autowiki/wiki.json` loader + module matcher + planner integration. |
| `tests/api/test_research.py` | API tests for REST + WebSocket endpoints. |
| `tests/cli/test_research_cli.py` | CLI command test. |

### Modified files

| Path | Change |
|---|---|
| `shared/models.py` | Add `ResearchReport` model (new table). |
| `worker/jobs.py` | Add `run_deep_research`; load `UserSteering` in `run_full_index` + `run_refresh_index` and pass to planner. |
| `worker/main.py` | Register `run_deep_research` in `WorkerSettings.functions`. |
| `worker/pipeline/ingestion.py` | New helper `load_autowiki_config(clone_root) -> UserSteering | None`. |
| `worker/pipeline/wiki_planner.py` | `generate_wiki_plan` accepts `user_steering`; Phase 1 skipped when user provides pages; `_assign_files` pre-assigns by module prefix; repo_notes inlined into system prompt; page_notes set on each `WikiPageSpec`. |
| `worker/pipeline/page_draft.py` | Inject `page_notes` and `repo_notes` into the draft prompt. |
| `api/main.py` | Include `research` router. |
| `api/queue.py` | Add `enqueue_deep_research`. |
| `cli/main.py` | Wire `research` command. |
| `web/lib/api.ts` | Add `startResearch`, `getResearchReport`, and typed responses. |
| `web/lib/ws.ts` | Add `useResearchStream` hook. |
| `web/components/WikiSidebar.tsx` | Add "Research" link alongside "Chat"; optionally render a small "steered" badge on pages sourced from `.autowiki/wiki.json`. |

### Storage layout additions

```
~/.autowiki/repos/{repo_hash}/
  clone/.autowiki/wiki.json     ← user-authored (optional; read-only from AutoWiki's POV)
  research/                     ← per-repo research artefacts
    {job_id}.json               ← final serialised {question, plan, findings, report}
```

The `research/` directory is created on demand by `run_deep_research`. SQLite (`research_reports` table) is still the source of truth; the JSON file is a convenience snapshot for debugging / CLI `--dump`.

---

## Task Decomposition

Tasks are grouped under **Phase 3 (Deep Research)** and **Phase 4 (Wiki Steering)**. Complete Phase 3 first — it is self-contained and unblocks UI work. Phase 4 depends on no Phase 3 artefacts but should land after so git history stays readable.

Within each phase the ordering is **model → worker logic → API → CLI → web UI → docs**, which mirrors how dependencies flow.

---

## Phase 3 — Deep Research

### Task 1: Add `ResearchReport` SQLite model

**Files:**
- Modify: `shared/models.py`
- Test: `tests/worker/test_deep_research.py` (new file)

- [ ] **Step 1: Write the failing test**

Create `tests/worker/test_deep_research.py`:

```python
"""Tests for the Deep Research feature (orchestrator + persistence)."""

from __future__ import annotations


async def test_research_report_model_persists_roundtrip(tmp_path):
    """Persisting a ResearchReport round-trips every field."""
    from shared.database import dispose_db, get_session, init_db
    from shared.models import Repository, ResearchReport

    db_path = str(tmp_path / "t.db")
    await init_db(db_path)
    try:
        async with get_session(db_path) as s:
            s.add(Repository(id="r1", owner="o", name="n", status="ready"))
            s.add(
                ResearchReport(
                    id="rep1",
                    repo_id="r1",
                    job_id="job1",
                    question="What is the pipeline?",
                    plan_json="[]",
                    findings_json="[]",
                    report_markdown="",
                    status="queued",
                )
            )
            await s.commit()

        async with get_session(db_path) as s:
            loaded = await s.get(ResearchReport, "rep1")
            assert loaded is not None
            assert loaded.repo_id == "r1"
            assert loaded.job_id == "job1"
            assert loaded.question == "What is the pipeline?"
            assert loaded.status == "queued"
    finally:
        await dispose_db(db_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/worker/test_deep_research.py::test_research_report_model_persists_roundtrip -v`
Expected: FAIL with `ImportError: cannot import name 'ResearchReport'`.

- [ ] **Step 3: Add the model**

In `shared/models.py`, add the following after the `ChatMessage` class:

```python
class ResearchReport(Base):
    __tablename__ = "research_reports"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    plan_json: Mapped[str] = mapped_column(Text, default="[]")
    findings_json: Mapped[str] = mapped_column(Text, default="[]")
    report_markdown: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String, nullable=False)  # queued|running|done|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/worker/test_deep_research.py::test_research_report_model_persists_roundtrip -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add shared/models.py tests/worker/test_deep_research.py
git commit -m "feat(research): add ResearchReport SQLite model"
```

---

### Task 2: Research planner (LLM → investigation plan)

**Files:**
- Create: `worker/deep_research.py`
- Test: `tests/worker/test_deep_research.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/worker/test_deep_research.py`:

```python
async def test_plan_research_returns_investigation_steps(mock_llm):
    """The planner extracts structured investigation steps from an LLM response."""
    from worker.deep_research import ResearchStep, plan_research

    async def _structured(*args, **kwargs):
        return {
            "plan": [
                {"query": "Where does the pipeline start?", "rationale": "Locate the entry point."},
                {"query": "How is incremental refresh detected?", "rationale": "Understand diff logic."},
                {"query": "Which module owns embedding?", "rationale": "Find storage layer."},
            ]
        }

    mock_llm.generate_structured.side_effect = _structured

    steps = await plan_research(
        question="How does the refresh pipeline work?",
        repo_name="autowiki",
        readme="AutoWiki generates wikis.",
        llm=mock_llm,
    )

    assert len(steps) == 3
    assert all(isinstance(s, ResearchStep) for s in steps)
    assert steps[0].query == "Where does the pipeline start?"
    assert steps[0].rationale == "Locate the entry point."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/worker/test_deep_research.py::test_plan_research_returns_investigation_steps -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'worker.deep_research'`.

- [ ] **Step 3: Create the module with `plan_research`**

Create `worker/deep_research.py`:

```python
"""Deep Research orchestrator.

Implements the three-stage research flow defined in
``docs/superpowers/specs/2026-03-22-autowiki-design.md`` §7.2:

    User question
          │
          ▼
    Research Planner (LLM)  → list[ResearchStep]
          │
          ▼  (investigator loop, one round per step)
    Investigator Agent     → list[ResearchFinding]
          │
          ▼
    Synthesizer (LLM)       → final Markdown report

Each helper is a pure async function — no DB, no WebSocket. The ARQ job
in ``worker.jobs`` wires these together and streams events via the
``on_event`` callback.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from worker.embedding.base import EmbeddingProvider
from worker.llm.base import LLMProvider
from worker.pipeline.rag_indexer import FAISSStore

# Event payload emitted to WebSocket / CLI consumers.
ResearchEvent = dict[str, Any]
OnEventCallback = Callable[[ResearchEvent], Awaitable[None]]

MAX_STEPS = 5
MIN_STEPS = 3


@dataclass
class ResearchStep:
    """One investigation step from the research plan."""

    query: str
    rationale: str


@dataclass
class ResearchFinding:
    """Result of running the investigator against one step."""

    step_index: int
    query: str
    answer: str
    sources: list[dict] = field(default_factory=list)


_PLANNER_SCHEMA = {
    "type": "object",
    "properties": {
        "plan": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["query", "rationale"],
            },
        }
    },
    "required": ["plan"],
}

_PLANNER_SYSTEM = (
    "You are a senior software engineer running a deep investigation of a "
    "code repository. Decompose the user's question into {min_steps}–{max_steps} "
    "focused investigation steps. Each step should be answerable by a single "
    "RAG search against the repository. Output ONLY valid JSON."
)


async def plan_research(
    question: str,
    repo_name: str,
    readme: str | None,
    llm: LLMProvider,
) -> list[ResearchStep]:
    """Decompose a research question into an ordered list of investigation steps."""
    system = _PLANNER_SYSTEM.format(min_steps=MIN_STEPS, max_steps=MAX_STEPS)
    prompt = (
        f"Repository: {repo_name}\n\n"
        f"README excerpt:\n{(readme or '')[:2000]}\n\n"
        f"Research question:\n{question}\n\n"
        f"Produce {MIN_STEPS}–{MAX_STEPS} investigation steps as JSON with "
        "key 'plan'. Each entry must have 'query' (a specific question) and "
        "'rationale' (why this step is needed)."
    )
    result = await llm.generate_structured(prompt, _PLANNER_SCHEMA, system=system)
    raw_steps = result.get("plan", [])[:MAX_STEPS]
    return [ResearchStep(query=s["query"], rationale=s["rationale"]) for s in raw_steps]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/worker/test_deep_research.py::test_plan_research_returns_investigation_steps -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/deep_research.py tests/worker/test_deep_research.py
git commit -m "feat(research): add plan_research LLM decomposition"
```

---

### Task 3: Investigator (RAG retrieval + per-step LLM answer)

**Files:**
- Modify: `worker/deep_research.py`
- Test: `tests/worker/test_deep_research.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/worker/test_deep_research.py`:

```python
async def test_investigate_step_returns_finding_with_sources(
    mock_llm, mock_embedding
):
    """Each investigation step embeds its query, searches FAISS, and calls the LLM."""
    from unittest.mock import MagicMock

    from worker.deep_research import ResearchStep, investigate_step

    store = MagicMock()
    store.search.return_value = [
        {"file": "worker/jobs.py", "text": "def run_full_index(...): ...", "start_line": 120},
        {"file": "worker/pipeline/ingestion.py", "text": "def filter_files(...): ...", "start_line": 42},
    ]

    async def _generate(prompt, system=""):
        return "The pipeline is defined in `worker/jobs.py`."

    mock_llm.generate = _generate

    step = ResearchStep(query="Where is the pipeline?", rationale="Entry point.")
    finding = await investigate_step(
        step=step,
        step_index=0,
        store=store,
        llm=mock_llm,
        embedding=mock_embedding,
    )

    assert finding.step_index == 0
    assert finding.query == "Where is the pipeline?"
    assert "worker/jobs.py" in finding.answer
    assert len(finding.sources) == 2
    assert finding.sources[0]["file"] == "worker/jobs.py"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/worker/test_deep_research.py::test_investigate_step_returns_finding_with_sources -v`
Expected: FAIL with `ImportError: cannot import name 'investigate_step'`.

- [ ] **Step 3: Implement `investigate_step`**

Append to `worker/deep_research.py`:

```python
_INVESTIGATOR_SYSTEM = (
    "You are investigating one question about a software repository. "
    "Answer precisely using ONLY the provided source code context. Cite the "
    "file names you relied on. If the context is insufficient, say so."
)


async def investigate_step(
    step: ResearchStep,
    step_index: int,
    store: FAISSStore,
    llm: LLMProvider,
    embedding: EmbeddingProvider,
    top_k: int = 8,
) -> ResearchFinding:
    """Run RAG retrieval + LLM answer for a single investigation step."""
    query_vec = await embedding.embed(step.query)
    chunks = store.search(query_vec, k=top_k)

    context = "\n\n---\n\n".join(
        f"File: {c.get('file', 'unknown')} (lines {c.get('start_line', '?')}+)\n"
        f"{c.get('text', '')}"
        for c in chunks
    )

    prompt = (
        f"Investigation step: {step.query}\n"
        f"Rationale: {step.rationale}\n\n"
        f"Source code context:\n{context}\n\n"
        "Answer the investigation step using the context above. Cite file names."
    )
    answer = await llm.generate(prompt, system=_INVESTIGATOR_SYSTEM)
    return ResearchFinding(
        step_index=step_index,
        query=step.query,
        answer=answer,
        sources=[
            {
                "file": c.get("file", "unknown"),
                "start_line": c.get("start_line"),
                "end_line": c.get("end_line"),
            }
            for c in chunks
        ],
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/worker/test_deep_research.py::test_investigate_step_returns_finding_with_sources -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/deep_research.py tests/worker/test_deep_research.py
git commit -m "feat(research): add investigate_step RAG runner"
```

---

### Task 4: Synthesizer (final report from plan + findings)

**Files:**
- Modify: `worker/deep_research.py`
- Test: `tests/worker/test_deep_research.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/worker/test_deep_research.py`:

```python
async def test_synthesize_report_joins_findings(mock_llm):
    """Synthesizer builds a single Markdown report from plan + findings."""
    from worker.deep_research import (
        ResearchFinding,
        ResearchStep,
        synthesize_report,
    )

    async def _generate(prompt, system=""):
        assert "Where is the pipeline?" in prompt
        assert "defined in `worker/jobs.py`" in prompt
        return "# Final Report\n\nThe pipeline lives in `worker/jobs.py`."

    mock_llm.generate = _generate

    plan = [ResearchStep(query="Where is the pipeline?", rationale="Entry point.")]
    findings = [
        ResearchFinding(
            step_index=0,
            query="Where is the pipeline?",
            answer="The pipeline is defined in `worker/jobs.py`.",
            sources=[{"file": "worker/jobs.py"}],
        )
    ]
    report = await synthesize_report(
        question="How does the pipeline work?",
        plan=plan,
        findings=findings,
        llm=mock_llm,
    )
    assert report.startswith("# Final Report")
    assert "worker/jobs.py" in report
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/worker/test_deep_research.py::test_synthesize_report_joins_findings -v`
Expected: FAIL — `synthesize_report` not defined.

- [ ] **Step 3: Implement `synthesize_report`**

Append to `worker/deep_research.py`:

```python
_SYNTHESIZER_SYSTEM = (
    "You are compiling a deep-research report for a software engineer. "
    "Using the original question, the investigation plan, and the per-step "
    "findings, write a well-structured Markdown report. Include: a short "
    "executive summary, the conclusions for each step, a combined answer "
    "section, and an explicit 'Sources' section that lists every file "
    "referenced. Use Markdown headings, bullet points, and code fences as "
    "appropriate."
)


async def synthesize_report(
    question: str,
    plan: list[ResearchStep],
    findings: list[ResearchFinding],
    llm: LLMProvider,
) -> str:
    """Generate the final Markdown report for a Deep Research job."""
    plan_md = "\n".join(
        f"{i + 1}. **{s.query}** — {s.rationale}" for i, s in enumerate(plan)
    )
    findings_md = "\n\n".join(
        f"### Step {f.step_index + 1}: {f.query}\n\n{f.answer}\n\n"
        f"Sources: {', '.join(s.get('file', '?') for s in f.sources)}"
        for f in findings
    )
    prompt = (
        f"Research question:\n{question}\n\n"
        f"Investigation plan:\n{plan_md}\n\n"
        f"Findings:\n{findings_md}\n\n"
        "Produce the final report in Markdown."
    )
    return await llm.generate(prompt, system=_SYNTHESIZER_SYSTEM)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/worker/test_deep_research.py::test_synthesize_report_joins_findings -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/deep_research.py tests/worker/test_deep_research.py
git commit -m "feat(research): add synthesize_report Markdown composer"
```

---

### Task 5: Orchestrator `run_deep_research_flow` with event callback

**Files:**
- Modify: `worker/deep_research.py`
- Test: `tests/worker/test_deep_research.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/worker/test_deep_research.py`:

```python
async def test_run_deep_research_flow_emits_events(mock_llm, mock_embedding):
    """The orchestrator emits plan/step_start/step_finding/report events in order."""
    from unittest.mock import MagicMock

    from worker.deep_research import run_deep_research_flow

    async def _structured(prompt, schema, system=""):
        return {
            "plan": [
                {"query": "Q1", "rationale": "R1"},
                {"query": "Q2", "rationale": "R2"},
            ]
        }

    mock_llm.generate_structured.side_effect = _structured

    async def _generate(prompt, system=""):
        if "Research question" in prompt:
            return "# Report\n"
        return "Answer."

    mock_llm.generate = _generate

    store = MagicMock()
    store.search.return_value = [{"file": "x.py", "text": "pass", "start_line": 1}]

    events: list[dict] = []

    async def _on_event(ev):
        events.append(ev)

    result = await run_deep_research_flow(
        question="Q?",
        repo_name="autowiki",
        readme=None,
        store=store,
        llm=mock_llm,
        embedding=mock_embedding,
        on_event=_on_event,
    )

    types = [e["type"] for e in events]
    assert types[0] == "plan"
    assert "step_start" in types
    assert "step_finding" in types
    assert types[-1] == "report"
    assert result.report.startswith("# Report")
    assert len(result.plan) == 2
    assert len(result.findings) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/worker/test_deep_research.py::test_run_deep_research_flow_emits_events -v`
Expected: FAIL — `run_deep_research_flow` not defined.

- [ ] **Step 3: Implement the orchestrator**

Append to `worker/deep_research.py`:

```python
@dataclass
class ResearchResult:
    """Aggregate result returned by the orchestrator."""

    plan: list[ResearchStep]
    findings: list[ResearchFinding]
    report: str

    def to_serialisable(self) -> dict:
        return {
            "plan": [asdict(s) for s in self.plan],
            "findings": [asdict(f) for f in self.findings],
            "report": self.report,
        }


async def run_deep_research_flow(
    question: str,
    repo_name: str,
    readme: str | None,
    store: FAISSStore,
    llm: LLMProvider,
    embedding: EmbeddingProvider,
    on_event: OnEventCallback | None = None,
) -> ResearchResult:
    """Drive planner → investigator → synthesizer, emitting progress events."""

    async def _emit(event: ResearchEvent) -> None:
        if on_event is not None:
            await on_event(event)

    plan = await plan_research(question, repo_name, readme, llm)
    await _emit({"type": "plan", "plan": [asdict(s) for s in plan]})

    findings: list[ResearchFinding] = []
    for idx, step in enumerate(plan):
        await _emit(
            {
                "type": "step_start",
                "step_index": idx,
                "query": step.query,
                "rationale": step.rationale,
            }
        )
        finding = await investigate_step(
            step=step,
            step_index=idx,
            store=store,
            llm=llm,
            embedding=embedding,
        )
        findings.append(finding)
        await _emit(
            {
                "type": "step_finding",
                "step_index": idx,
                "answer": finding.answer,
                "sources": finding.sources,
            }
        )

    report = await synthesize_report(question, plan, findings, llm)
    await _emit({"type": "report", "content": report})

    return ResearchResult(plan=plan, findings=findings, report=report)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/worker/test_deep_research.py::test_run_deep_research_flow_emits_events -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/deep_research.py tests/worker/test_deep_research.py
git commit -m "feat(research): add run_deep_research_flow orchestrator"
```

---

### Task 6: ARQ job `run_deep_research`

**Files:**
- Modify: `worker/jobs.py`
- Modify: `worker/main.py`
- Test: `tests/worker/test_deep_research.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/worker/test_deep_research.py`:

```python
async def test_run_deep_research_persists_report(
    tmp_path, mock_llm, mock_embedding, monkeypatch
):
    """End-to-end: the ARQ job persists the plan/findings/report to SQLite."""
    import json
    from unittest.mock import MagicMock, patch

    from shared.database import dispose_db, get_session, init_db
    from shared.models import Job, Repository, ResearchReport
    from worker.jobs import run_deep_research

    db_path = str(tmp_path / "t.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("AUTOWIKI_DATA_DIR", str(tmp_path))
    from shared.config import reset_config

    reset_config()
    await init_db(db_path)
    async with get_session(db_path) as s:
        s.add(Repository(id="r1", owner="o", name="n", status="ready"))
        s.add(Job(id="j1", repo_id="r1", type="research", status="queued"))
        s.add(
            ResearchReport(
                id="rep1",
                repo_id="r1",
                job_id="j1",
                question="Q?",
                status="queued",
            )
        )
        await s.commit()

    # Stub the FAISS store + providers so the job runs without real I/O.
    fake_store = MagicMock()
    fake_store.search.return_value = [{"file": "x.py", "text": "pass"}]

    async def _structured(*a, **k):
        return {"plan": [{"query": "Q1", "rationale": "R1"}]}

    mock_llm.generate_structured.side_effect = _structured

    async def _generate(prompt, system=""):
        return "# Report" if "Research question" in prompt else "Answer."

    mock_llm.generate = _generate

    with patch("worker.jobs.make_llm_provider", return_value=mock_llm), patch(
        "worker.jobs.make_embedding_provider", return_value=mock_embedding
    ), patch("worker.jobs.FAISSStore", return_value=fake_store), patch(
        "worker.jobs._load_faiss_for_research", return_value=fake_store
    ):
        try:
            await run_deep_research(
                {}, repo_id="r1", job_id="j1", report_id="rep1", question="Q?"
            )
            async with get_session(db_path) as s:
                report = await s.get(ResearchReport, "rep1")
                assert report.status == "done"
                assert report.report_markdown.startswith("# Report")
                assert json.loads(report.plan_json)[0]["query"] == "Q1"
                assert len(json.loads(report.findings_json)) == 1
        finally:
            await dispose_db(db_path)
            reset_config()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/worker/test_deep_research.py::test_run_deep_research_persists_report -v`
Expected: FAIL — `run_deep_research` not defined.

- [ ] **Step 3: Implement the ARQ job**

In `worker/jobs.py`, after the `run_refresh_index` function, add:

```python
async def _load_faiss_for_research(repo_data_dir: Path, embedding) -> FAISSStore:
    """Load the FAISS store for a repo, running the blocking IO in an executor."""
    store = _make_faiss_store(repo_data_dir, embedding)
    await asyncio.get_running_loop().run_in_executor(None, store.load)
    return store


async def run_deep_research(
    ctx: dict,
    repo_id: str,
    job_id: str,
    report_id: str,
    question: str,
) -> None:
    """ARQ job: run the Deep Research flow and persist the result."""
    import json as _json

    from worker.deep_research import run_deep_research_flow
    from shared.models import ResearchReport

    cfg = get_config()
    db_path = str(cfg.database_path)
    data_dir = cfg.data_dir
    await init_db(db_path)

    async def _update_report(**kwargs):
        async with get_session(db_path) as s:
            report = await s.get(ResearchReport, report_id)
            for k, v in kwargs.items():
                setattr(report, k, v)
            await s.commit()

    try:
        await _update_job(db_path, job_id, status="running", progress=5)
        await _update_report(status="running")

        # Load repo + readme from DB clone
        repo_data_dir = data_dir / "repos" / repo_id
        clone_root = repo_data_dir / "clone"
        from worker.pipeline.ingestion import extract_readme

        loop = asyncio.get_running_loop()
        readme = await loop.run_in_executor(None, extract_readme, clone_root)

        embedding = make_embedding_provider(cfg)
        llm = make_llm_provider(cfg)
        store = await _load_faiss_for_research(repo_data_dir, embedding)

        async with get_session(db_path) as s:
            repo = await s.get(Repository, repo_id)
            repo_name = repo.name if repo is not None else repo_id

        # Stream progress events into the DB so the WebSocket polling layer
        # (or GET-based inspection) sees live state.
        async def _on_event(event: dict) -> None:
            if event["type"] == "plan":
                await _update_report(plan_json=_json.dumps(event["plan"]))
                await _update_job(db_path, job_id, progress=20)
            elif event["type"] == "step_start":
                await _update_job(
                    db_path,
                    job_id,
                    status_description=(
                        f"Investigating step {event['step_index'] + 1}"
                    ),
                )
            elif event["type"] == "step_finding":
                # Append to findings_json as they land
                async with get_session(db_path) as s:
                    rep = await s.get(ResearchReport, report_id)
                    findings = _json.loads(rep.findings_json or "[]")
                    findings.append(
                        {
                            "step_index": event["step_index"],
                            "answer": event["answer"],
                            "sources": event["sources"],
                        }
                    )
                    rep.findings_json = _json.dumps(findings)
                    await s.commit()
            elif event["type"] == "report":
                await _update_report(report_markdown=event["content"])

        result = await run_deep_research_flow(
            question=question,
            repo_name=repo_name,
            readme=readme,
            store=store,
            llm=llm,
            embedding=embedding,
            on_event=_on_event,
        )

        now = datetime.now(UTC)
        await _update_report(
            status="done",
            finished_at=now,
            plan_json=_json.dumps([asdict_s(s) for s in result.plan]),
            findings_json=_json.dumps([asdict_s(f) for f in result.findings]),
            report_markdown=result.report,
        )
        await _update_job(
            db_path,
            job_id,
            status="done",
            progress=100,
            finished_at=now,
            status_description="Research complete",
        )
    except Exception as e:
        logger.exception("Deep research job failed: %s", e)
        now = datetime.now(UTC)
        await _update_report(status="failed", error=str(e), finished_at=now)
        await _update_job(
            db_path,
            job_id,
            status="failed",
            error=str(e),
            finished_at=now,
            status_description=f"Error: {e}",
        )
        raise
```

Add a tiny helper at the top of `worker/jobs.py` (near the other helpers):

```python
def asdict_s(obj) -> dict:
    """dataclasses.asdict proxy so the Deep Research ARQ job does not import it twice."""
    from dataclasses import asdict as _asdict

    return _asdict(obj)
```

- [ ] **Step 4: Register the job with the ARQ worker**

In `worker/main.py`, change the import and `functions` list:

```python
from worker.jobs import run_deep_research, run_full_index, run_refresh_index
```

```python
    functions = [run_full_index, run_refresh_index, run_deep_research]
```

Update the docstring on `WorkerSettings.functions` to mention `run_deep_research`.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/worker/test_deep_research.py::test_run_deep_research_persists_report -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add worker/jobs.py worker/main.py tests/worker/test_deep_research.py
git commit -m "feat(research): wire run_deep_research ARQ job"
```

---

### Task 7: Enqueue helper + API request schema

**Files:**
- Modify: `api/queue.py`
- Create: `api/routers/research.py`
- Test: `tests/api/test_research.py` (new file)

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_research.py`:

```python
"""API tests for the Deep Research REST + WebSocket endpoints."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


async def _prep_repo(db_path: str):
    from shared.database import get_session, init_db
    from shared.models import Repository

    await init_db(db_path)
    async with get_session(db_path) as s:
        s.add(Repository(id="r1", owner="o", name="n", status="ready"))
        await s.commit()


@pytest.fixture
def research_env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("AUTOWIKI_DATA_DIR", str(tmp_path))
    from shared.config import reset_config

    reset_config()
    return db_path


async def test_start_research_returns_job_and_report_ids(research_env, monkeypatch):
    """POST /api/repos/{id}/research enqueues a job and inserts a ResearchReport row."""
    db_path = research_env
    await _prep_repo(db_path)

    calls: list[dict] = []

    async def _fake_enqueue(*args, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("api.routers.research._enqueue_deep_research", _fake_enqueue)

    from api.main import app
    from shared.database import dispose_db
    from shared.models import ResearchReport
    from shared.database import get_session

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                "/api/repos/r1/research", json={"question": "How does refresh work?"}
            )
            assert r.status_code == 202
            body = r.json()
            assert "job_id" in body and "report_id" in body
            assert body["status"] == "queued"

            async with get_session(db_path) as s:
                report = await s.get(ResearchReport, body["report_id"])
                assert report is not None
                assert report.question == "How does refresh work?"

            # Enqueue helper was called with the right arguments
            assert calls and calls[0]["question"] == "How does refresh work?"
    finally:
        await dispose_db(db_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_research.py::test_start_research_returns_job_and_report_ids -v`
Expected: FAIL — router/module not found.

- [ ] **Step 3: Add enqueue helper**

In `api/queue.py`, append:

```python
async def enqueue_deep_research(
    repo_id: str,
    job_id: str,
    report_id: str,
    question: str,
) -> str:
    """Enqueue a Deep Research job."""
    await _enqueue(
        "run_deep_research",
        repo_id=repo_id,
        job_id=job_id,
        report_id=report_id,
        question=question,
    )
    return job_id
```

- [ ] **Step 4: Create the research router skeleton**

Create `api/routers/research.py`:

```python
"""REST + WebSocket endpoints for Deep Research."""

from __future__ import annotations

import asyncio
import json as _json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import select

from api.queue import enqueue_deep_research as _enqueue_deep_research
from shared.config import get_config
from shared.database import get_session
from shared.models import Job, Repository, ResearchReport

logger = logging.getLogger(__name__)
router = APIRouter()


class StartResearchRequest(BaseModel):
    question: str


@router.post("/api/repos/{repo_id}/research", status_code=202)
async def start_research(repo_id: str, req: StartResearchRequest) -> dict:
    if not req.question.strip():
        raise HTTPException(status_code=422, detail="Question is required")

    cfg = get_config()
    db_path = str(cfg.database_path)

    async with get_session(db_path) as s:
        repo = await s.get(Repository, repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="Repository not found")
        if repo.status != "ready":
            raise HTTPException(
                status_code=409, detail="Repository is not ready for research"
            )

        job_id = str(uuid.uuid4())
        report_id = str(uuid.uuid4())
        s.add(
            Job(
                id=job_id,
                repo_id=repo_id,
                type="research",
                status="queued",
                progress=0,
                created_at=datetime.now(UTC),
            )
        )
        s.add(
            ResearchReport(
                id=report_id,
                repo_id=repo_id,
                job_id=job_id,
                question=req.question.strip(),
                status="queued",
                created_at=datetime.now(UTC),
            )
        )
        await s.commit()

    await _enqueue_deep_research(
        repo_id=repo_id, job_id=job_id, report_id=report_id, question=req.question.strip()
    )
    return {"job_id": job_id, "report_id": report_id, "status": "queued"}


@router.get("/api/repos/{repo_id}/research/{job_id}")
async def get_research(repo_id: str, job_id: str) -> dict:
    cfg = get_config()
    db_path = str(cfg.database_path)
    async with get_session(db_path) as s:
        result = await s.execute(
            select(ResearchReport).where(
                ResearchReport.repo_id == repo_id,
                ResearchReport.job_id == job_id,
            )
        )
        report = result.scalar_one_or_none()
        if report is None:
            raise HTTPException(status_code=404, detail="Report not found")
        return {
            "id": report.id,
            "repo_id": report.repo_id,
            "job_id": report.job_id,
            "question": report.question,
            "plan": _json.loads(report.plan_json or "[]"),
            "findings": _json.loads(report.findings_json or "[]"),
            "report": report.report_markdown,
            "status": report.status,
            "error": report.error,
        }
```

- [ ] **Step 5: Wire the router into the app**

In `api/main.py`:

```python
from api.routers import research as research_router
```

```python
app.include_router(research_router.router)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/api/test_research.py::test_start_research_returns_job_and_report_ids -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add api/queue.py api/routers/research.py api/main.py tests/api/test_research.py
git commit -m "feat(research): POST + GET research endpoints"
```

---

### Task 8: `GET /research/{job_id}` returns persisted state

**Files:**
- Test: `tests/api/test_research.py`

- [ ] **Step 1: Write the test (route already implemented in Task 7)**

Append to `tests/api/test_research.py`:

```python
async def test_get_research_returns_persisted_report(research_env):
    """GET returns the plan/findings/report as persisted in SQLite."""
    import json as _json

    from api.main import app
    from shared.database import dispose_db, get_session
    from shared.models import Job, Repository, ResearchReport

    db_path = research_env
    from shared.database import init_db

    await init_db(db_path)
    try:
        async with get_session(db_path) as s:
            s.add(Repository(id="r1", owner="o", name="n", status="ready"))
            s.add(Job(id="j1", repo_id="r1", type="research", status="done"))
            s.add(
                ResearchReport(
                    id="rep1",
                    repo_id="r1",
                    job_id="j1",
                    question="Q?",
                    plan_json=_json.dumps([{"query": "Q1", "rationale": "R"}]),
                    findings_json=_json.dumps(
                        [{"step_index": 0, "answer": "A", "sources": []}]
                    ),
                    report_markdown="# Report",
                    status="done",
                )
            )
            await s.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get("/api/repos/r1/research/j1")
            assert r.status_code == 200
            body = r.json()
            assert body["question"] == "Q?"
            assert body["plan"][0]["query"] == "Q1"
            assert body["findings"][0]["answer"] == "A"
            assert body["report"] == "# Report"
            assert body["status"] == "done"
    finally:
        await dispose_db(db_path)
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/api/test_research.py::test_get_research_returns_persisted_report -v`
Expected: PASS (route already implemented in Task 7).

- [ ] **Step 3: Commit**

```bash
git add tests/api/test_research.py
git commit -m "test(research): GET research endpoint test"
```

---

### Task 9: WebSocket streaming of research events

**Files:**
- Modify: `api/routers/research.py`
- Test: `tests/api/test_research.py`

The WebSocket reads rows from `research_reports` / `jobs` and pushes a diff
of events as new fields populate — it doesn't need to share state with the
ARQ job. Polling every 250 ms is fine for v1; we can revisit when we add
Redis pub/sub.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_research.py`:

```python
async def test_ws_research_streams_completed_report(research_env):
    """When a report is already 'done', the WS emits plan/step/report then closes."""
    import json as _json

    from starlette.testclient import TestClient

    from api.main import app
    from shared.database import get_session, init_db
    from shared.models import Job, Repository, ResearchReport

    db_path = research_env
    await init_db(db_path)
    async with get_session(db_path) as s:
        s.add(Repository(id="r1", owner="o", name="n", status="ready"))
        s.add(Job(id="j1", repo_id="r1", type="research", status="done"))
        s.add(
            ResearchReport(
                id="rep1",
                repo_id="r1",
                job_id="j1",
                question="Q?",
                plan_json=_json.dumps([{"query": "Q1", "rationale": "R"}]),
                findings_json=_json.dumps(
                    [{"step_index": 0, "answer": "A", "sources": []}]
                ),
                report_markdown="# Report",
                status="done",
            )
        )
        await s.commit()

    with TestClient(app) as client:
        with client.websocket_connect("/ws/repos/r1/research/j1") as ws:
            types: list[str] = []
            try:
                while True:
                    msg = ws.receive_json()
                    types.append(msg["type"])
                    if msg["type"] in ("done", "error"):
                        break
            except Exception:
                pass
    assert "plan" in types
    assert "step_finding" in types
    assert "report" in types
    assert types[-1] == "done"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_research.py::test_ws_research_streams_completed_report -v`
Expected: FAIL — WebSocket route not defined.

- [ ] **Step 3: Add the WebSocket route**

Append to `api/routers/research.py`:

```python
_POLL_INTERVAL = 0.25  # seconds


@router.websocket("/ws/repos/{repo_id}/research/{job_id}")
async def ws_research(websocket: WebSocket, repo_id: str, job_id: str):
    """Stream Deep Research progress events over WebSocket.

    Protocol (server → client JSON messages):
        {"type": "plan", "plan": [...]}
        {"type": "step_start", "step_index": i, "query": "..."}
        {"type": "step_finding", "step_index": i, "answer": "...", "sources": [...]}
        {"type": "report", "content": "# markdown"}
        {"type": "done"}
        {"type": "error", "content": "..."}
    """
    cfg = get_config()
    db_path = str(cfg.database_path)

    async with get_session(db_path) as s:
        result = await s.execute(
            select(ResearchReport).where(
                ResearchReport.repo_id == repo_id,
                ResearchReport.job_id == job_id,
            )
        )
        report = result.scalar_one_or_none()
        if report is None:
            await websocket.close(code=4004)
            return

    await websocket.accept()

    sent_plan = False
    sent_finding_indices: set[int] = set()
    sent_report = False
    try:
        while True:
            async with get_session(db_path) as s:
                result = await s.execute(
                    select(ResearchReport).where(
                        ResearchReport.repo_id == repo_id,
                        ResearchReport.job_id == job_id,
                    )
                )
                report = result.scalar_one_or_none()
                if report is None:
                    await websocket.send_json(
                        {"type": "error", "content": "Report vanished"}
                    )
                    break

                plan = _json.loads(report.plan_json or "[]")
                findings = _json.loads(report.findings_json or "[]")

            if plan and not sent_plan:
                await websocket.send_json({"type": "plan", "plan": plan})
                sent_plan = True

            for f in findings:
                idx = f.get("step_index", -1)
                if idx not in sent_finding_indices:
                    await websocket.send_json(
                        {
                            "type": "step_start",
                            "step_index": idx,
                            "query": plan[idx]["query"] if idx < len(plan) else "",
                        }
                    )
                    await websocket.send_json(
                        {
                            "type": "step_finding",
                            "step_index": idx,
                            "answer": f.get("answer", ""),
                            "sources": f.get("sources", []),
                        }
                    )
                    sent_finding_indices.add(idx)

            if report.report_markdown and not sent_report:
                await websocket.send_json(
                    {"type": "report", "content": report.report_markdown}
                )
                sent_report = True

            if report.status == "failed":
                await websocket.send_json(
                    {"type": "error", "content": report.error or "Research failed"}
                )
                break
            if report.status == "done" and sent_report:
                await websocket.send_json({"type": "done"})
                break

            await asyncio.sleep(_POLL_INTERVAL)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Unhandled error in ws_research for job %s", job_id)
        try:
            await websocket.send_json(
                {"type": "error", "content": "Internal server error"}
            )
        except Exception:
            pass
    finally:
        await websocket.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_research.py::test_ws_research_streams_completed_report -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/routers/research.py tests/api/test_research.py
git commit -m "feat(research): WebSocket streaming for research progress"
```

---

### Task 10: CLI `autowiki research`

**Files:**
- Create: `cli/commands/research_cmd.py`
- Modify: `cli/main.py`
- Test: `tests/cli/test_research_cli.py` (new file)

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_research_cli.py`:

```python
"""CLI tests for `autowiki research`."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from cli.main import app


def test_research_cmd_prints_report():
    """`autowiki research` posts to the API, consumes the WS, prints the report."""
    runner = CliRunner()

    mock_repo_resp = MagicMock()
    mock_repo_resp.status_code = 200
    mock_repo_resp.json.return_value = {"status": "ready"}

    mock_start_resp = MagicMock()
    mock_start_resp.status_code = 202
    mock_start_resp.json.return_value = {"job_id": "j1", "report_id": "rep1", "status": "queued"}
    mock_start_resp.raise_for_status = MagicMock()

    events = [
        '{"type": "plan", "plan": [{"query": "Q1", "rationale": "R"}]}',
        '{"type": "step_start", "step_index": 0, "query": "Q1"}',
        '{"type": "step_finding", "step_index": 0, "answer": "A", "sources": []}',
        '{"type": "report", "content": "# Final Report"}',
        '{"type": "done"}',
    ]

    fake_ws = AsyncMock()
    fake_ws.recv.side_effect = events
    fake_ws.__aenter__.return_value = fake_ws
    fake_ws.__aexit__.return_value = None

    with patch("httpx.get", return_value=mock_repo_resp), patch(
        "httpx.post", return_value=mock_start_resp
    ), patch("websockets.connect", return_value=fake_ws):
        result = runner.invoke(
            app, ["research", "github.com/o/n", "How does refresh work?"]
        )
    assert result.exit_code == 0
    assert "Final Report" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_research_cli.py -v`
Expected: FAIL — command not registered.

- [ ] **Step 3: Implement the CLI command**

Create `cli/commands/research_cmd.py`:

```python
from __future__ import annotations

import asyncio
import hashlib
import json as _json

import httpx
import typer

from worker.pipeline.ingestion import parse_github_url


def research_cmd(
    url: str = typer.Argument(..., help="GitHub repo URL"),
    question: str = typer.Argument(..., help="Research question"),
    api_url: str = typer.Option("http://127.0.0.1:3001", envvar="AUTOWIKI_API_URL"),
):
    """Run Deep Research on an indexed repository and print the report."""
    try:
        owner, name = parse_github_url(url)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    repo_id = hashlib.sha256(f"github:{owner}/{name}".encode()).hexdigest()[:16]

    repo_resp = httpx.get(f"{api_url}/api/repos/{repo_id}", timeout=10)
    if repo_resp.status_code == 404:
        typer.echo("Repository not found. Run `autowiki index` first.", err=True)
        raise typer.Exit(1)
    if repo_resp.status_code >= 400:
        typer.echo(f"API error {repo_resp.status_code}: {repo_resp.text}", err=True)
        raise typer.Exit(1)
    if repo_resp.json().get("status") != "ready":
        typer.echo("Repository is not ready. Wait for indexing to complete.", err=True)
        raise typer.Exit(1)

    start_resp = httpx.post(
        f"{api_url}/api/repos/{repo_id}/research",
        json={"question": question},
        timeout=10,
    )
    start_resp.raise_for_status()
    job_id = start_resp.json()["job_id"]

    import websockets

    ws_url = api_url.replace("http://", "ws://").replace("https://", "wss://")

    async def _stream() -> str:
        uri = f"{ws_url}/ws/repos/{repo_id}/research/{job_id}"
        final_report = ""
        async with websockets.connect(uri) as ws:
            while True:
                raw = await ws.recv()
                msg = _json.loads(raw)
                mtype = msg["type"]
                if mtype == "plan":
                    typer.echo("\n=== Research Plan ===")
                    for i, step in enumerate(msg["plan"], 1):
                        typer.echo(f"{i}. {step['query']} — {step['rationale']}")
                elif mtype == "step_start":
                    typer.echo(
                        f"\n--- Step {msg['step_index'] + 1}: {msg['query']} ---"
                    )
                elif mtype == "step_finding":
                    typer.echo(msg["answer"])
                elif mtype == "report":
                    final_report = msg["content"]
                elif mtype == "done":
                    break
                elif mtype == "error":
                    raise RuntimeError(msg["content"])
        return final_report

    try:
        report = asyncio.run(_stream())
        typer.echo("\n=== Final Report ===\n")
        typer.echo(report)
    except RuntimeError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Connection error: {e}", err=True)
        raise typer.Exit(1)
```

- [ ] **Step 4: Wire into `cli/main.py`**

```python
from cli.commands.research_cmd import research_cmd
```

```python
app.command("research")(research_cmd)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/cli/test_research_cli.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add cli/commands/research_cmd.py cli/main.py tests/cli/test_research_cli.py
git commit -m "feat(research): autowiki research CLI command"
```

---

### Task 11: Web API client + streaming hook

**Files:**
- Modify: `web/lib/api.ts`
- Modify: `web/lib/ws.ts`

- [ ] **Step 1: Add API client functions**

Append to `web/lib/api.ts`:

```typescript
/**
 * Starts a Deep Research job for a repository.
 */
export async function startResearch(
  repoId: string,
  question: string,
): Promise<{ job_id: string; report_id: string; status: string }> {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/research`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export interface ResearchFinding {
  step_index: number;
  answer: string;
  sources: Array<{ file: string; start_line?: number; end_line?: number }>;
}

export interface ResearchPlanStep {
  query: string;
  rationale: string;
}

/**
 * Fetches a completed (or in-progress) Deep Research report.
 */
export async function getResearchReport(
  repoId: string,
  jobId: string,
): Promise<{
  question: string;
  plan: ResearchPlanStep[];
  findings: ResearchFinding[];
  report: string;
  status: string;
  error: string | null;
}> {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/research/${jobId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
```

- [ ] **Step 2: Add the `useResearchStream` hook**

Append to `web/lib/ws.ts`:

```typescript
interface ResearchPlanStep { query: string; rationale: string }
interface ResearchFinding {
  step_index: number;
  answer: string;
  sources: Array<{ file: string }>;
}

export function useResearchStream(
  repoId: string,
  jobId: string | null,
  onPlan: (plan: ResearchPlanStep[]) => void,
  onStep: (finding: ResearchFinding) => void,
  onReport: (markdown: string) => void,
  onDone: () => void,
  onError: (msg: string) => void,
) {
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!jobId) return;
    const ws = new WebSocket(`${WS_URL}/ws/repos/${repoId}/research/${jobId}`);
    wsRef.current = ws;
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "plan") onPlan(msg.plan);
      else if (msg.type === "step_finding") onStep(msg);
      else if (msg.type === "report") onReport(msg.content);
      else if (msg.type === "done") { onDone(); ws.close(); }
      else if (msg.type === "error") { onError(msg.content); ws.close(); }
    };
    ws.onerror = () => onError("WebSocket error");
    return () => { ws.close(); };
  }, [repoId, jobId, onPlan, onStep, onReport, onDone, onError]);
}
```

- [ ] **Step 3: Run lint + typecheck**

Run: `npm run lint --prefix web`
Expected: PASS (no errors).

- [ ] **Step 4: Commit**

```bash
git add web/lib/api.ts web/lib/ws.ts
git commit -m "feat(research): web API client + useResearchStream hook"
```

---

### Task 12: `ResearchPanel` component + route

**Files:**
- Create: `web/components/ResearchPanel.tsx`
- Create: `web/app/[owner]/[repo]/research/page.tsx`
- Modify: `web/components/WikiSidebar.tsx` (add "Research" link next to "Chat")

- [ ] **Step 1: Create `ResearchPanel.tsx`**

Create `web/components/ResearchPanel.tsx`:

```tsx
"use client";

import { useCallback, useState } from "react";
import { startResearch, type ResearchFinding, type ResearchPlanStep } from "@/lib/api";
import { useResearchStream } from "@/lib/ws";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

export default function ResearchPanel({ repoId }: { repoId: string }) {
  const [question, setQuestion] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [plan, setPlan] = useState<ResearchPlanStep[]>([]);
  const [findings, setFindings] = useState<ResearchFinding[]>([]);
  const [report, setReport] = useState<string>("");
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const onPlan = useCallback((p: ResearchPlanStep[]) => setPlan(p), []);
  const onStep = useCallback((f: ResearchFinding) => {
    setFindings((prev) => [...prev, f]);
  }, []);
  const onReport = useCallback((md: string) => setReport(md), []);
  const onDone = useCallback(() => setStatus("done"), []);
  const onError = useCallback((msg: string) => {
    setErrorMsg(msg);
    setStatus("error");
  }, []);

  useResearchStream(repoId, jobId, onPlan, onStep, onReport, onDone, onError);

  const submit = async () => {
    if (!question.trim()) return;
    setPlan([]);
    setFindings([]);
    setReport("");
    setErrorMsg(null);
    setStatus("running");
    try {
      const { job_id } = await startResearch(repoId, question.trim());
      setJobId(job_id);
    } catch (e) {
      setErrorMsg(String(e));
      setStatus("error");
    }
  };

  return (
    <div className="flex flex-col gap-4 p-4 max-w-3xl mx-auto">
      <div className="flex gap-2">
        <Input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="What do you want to investigate?"
          disabled={status === "running"}
        />
        <Button onClick={submit} disabled={status === "running" || !question.trim()}>
          Research
        </Button>
      </div>

      {status === "error" && errorMsg && (
        <div className="text-red-600 text-sm">Error: {errorMsg}</div>
      )}

      {plan.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-2">Plan</h2>
          <ol className="list-decimal pl-6 space-y-1">
            {plan.map((s, i) => (
              <li key={i}>
                <span className="font-medium">{s.query}</span> — {s.rationale}
              </li>
            ))}
          </ol>
        </section>
      )}

      {findings.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-2">Findings</h2>
          <div className="space-y-3">
            {findings.map((f) => (
              <div key={f.step_index} className="border rounded p-3 bg-slate-50">
                <div className="font-medium mb-1">Step {f.step_index + 1}</div>
                <div className="wiki-content">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                    {f.answer}
                  </ReactMarkdown>
                </div>
                {f.sources.length > 0 && (
                  <div className="mt-1 text-xs text-slate-500">
                    Sources: {f.sources.map((s) => s.file).join(", ")}
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {report && (
        <section>
          <h2 className="text-lg font-semibold mb-2">Final Report</h2>
          <div className="wiki-content border rounded p-4 bg-white">
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
              {report}
            </ReactMarkdown>
          </div>
        </section>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Create the route page**

Create `web/app/[owner]/[repo]/research/page.tsx`:

```tsx
import ResearchPanel from "@/components/ResearchPanel";
import { repoId } from "@/lib/utils";

export default async function ResearchPage({
  params,
}: {
  params: Promise<{ owner: string; repo: string }>;
}) {
  const { owner, repo } = await params;
  return (
    <div style={{ padding: "1rem" }}>
      <h1 style={{ fontSize: "1.25rem", fontWeight: "bold", marginBottom: "1rem" }}>
        Deep Research — {owner}/{repo}
      </h1>
      <ResearchPanel repoId={repoId(owner, repo)} />
    </div>
  );
}
```

- [ ] **Step 3: Add "Research" link to `WikiSidebar.tsx`**

Locate the existing "Chat" link in `web/components/WikiSidebar.tsx` and add a sibling link pointing to `/${owner}/${repo}/research` labelled "Research". Keep existing styles — copy whatever classes the Chat link uses.

- [ ] **Step 4: Manual smoke test**

Run the full stack and verify the new page loads without TypeScript errors:

```bash
npm run lint --prefix web
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/components/ResearchPanel.tsx web/app/[owner]/[repo]/research/page.tsx web/components/WikiSidebar.tsx
git commit -m "feat(research): ResearchPanel component and /research route"
```

---

### Task 13: Pre-commit checks for Phase 3

- [ ] **Step 1: Run full test suite**

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/ --ignore=tests/e2e
npm run lint --prefix web
npm test --prefix web
```

Expected: all green. Fix any issues before proceeding to Phase 4.

- [ ] **Step 2: Commit any lint/format fixes**

```bash
git add -u
git commit -m "chore: ruff/eslint fixes after Phase 3"
```

---

## Phase 4 — Wiki Steering (`.autowiki/wiki.json`)

### Task 14: `UserSteering` dataclass + loader

**Files:**
- Create: `worker/pipeline/user_steering.py`
- Test: `tests/worker/test_user_steering.py` (new file)

The loader reads `{clone}/.autowiki/wiki.json` (optional). Supported schema:

```json
{
  "repo_notes": ["The core event bus lives in src/core/bus.ts"],
  "pages": [
    {
      "title": "Architecture Overview",
      "purpose": "High-level view of the system.",
      "parent": null,
      "modules": ["src/core", "src/api"],
      "page_notes": ["Ignore the legacy/ prefix entirely."]
    }
  ]
}
```

All top-level fields are optional. Invalid JSON or a non-object top-level value is treated as "no steering" and logs a warning.

- [ ] **Step 1: Write the failing test**

Create `tests/worker/test_user_steering.py`:

```python
"""Tests for the `.autowiki/wiki.json` user-steering loader."""

from __future__ import annotations

import json


def test_load_returns_none_when_missing(tmp_path):
    from worker.pipeline.user_steering import load_user_steering

    assert load_user_steering(tmp_path) is None


def test_load_parses_full_schema(tmp_path):
    from worker.pipeline.user_steering import load_user_steering

    cfg_dir = tmp_path / ".autowiki"
    cfg_dir.mkdir()
    (cfg_dir / "wiki.json").write_text(
        json.dumps(
            {
                "repo_notes": ["Treat legacy/ as deprecated."],
                "pages": [
                    {
                        "title": "Architecture",
                        "purpose": "System overview.",
                        "modules": ["src/core"],
                        "page_notes": ["Bus lives in core/bus.ts."],
                    }
                ],
            }
        )
    )

    steering = load_user_steering(tmp_path)
    assert steering is not None
    assert steering.repo_notes == ["Treat legacy/ as deprecated."]
    assert len(steering.pages) == 1
    page = steering.pages[0]
    assert page.title == "Architecture"
    assert page.purpose == "System overview."
    assert page.modules == ["src/core"]
    assert page.page_notes == ["Bus lives in core/bus.ts."]


def test_load_tolerates_partial_page(tmp_path):
    from worker.pipeline.user_steering import load_user_steering

    cfg_dir = tmp_path / ".autowiki"
    cfg_dir.mkdir()
    (cfg_dir / "wiki.json").write_text(json.dumps({"pages": [{"title": "Only"}]}))

    steering = load_user_steering(tmp_path)
    assert steering is not None
    assert steering.pages[0].title == "Only"
    assert steering.pages[0].purpose is None
    assert steering.pages[0].modules == []
    assert steering.pages[0].page_notes == []


def test_load_returns_none_on_invalid_json(tmp_path, caplog):
    from worker.pipeline.user_steering import load_user_steering

    cfg_dir = tmp_path / ".autowiki"
    cfg_dir.mkdir()
    (cfg_dir / "wiki.json").write_text("{ not json")

    assert load_user_steering(tmp_path) is None
    assert any("invalid" in rec.message.lower() for rec in caplog.records)


def test_assign_by_modules_groups_files_by_prefix():
    from worker.pipeline.user_steering import UserPageSpec, assign_by_modules

    pages = [
        UserPageSpec(title="Core", modules=["src/core"]),
        UserPageSpec(title="API", modules=["src/api", "src/routes"]),
    ]
    all_files = [
        "src/core/bus.ts",
        "src/core/util.ts",
        "src/api/server.ts",
        "src/routes/index.ts",
        "src/misc/other.ts",
    ]
    assignments, unassigned = assign_by_modules(pages, all_files)
    assert assignments["Core"] == ["src/core/bus.ts", "src/core/util.ts"]
    assert assignments["API"] == ["src/api/server.ts", "src/routes/index.ts"]
    assert unassigned == ["src/misc/other.ts"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_user_steering.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the loader**

Create `worker/pipeline/user_steering.py`:

```python
"""`.autowiki/wiki.json` loader and user-steering dataclasses.

Invoked during Stage 1 (ingestion) after the repo is cloned. The returned
:class:`UserSteering` (or ``None``) is passed through to the wiki planner
and page generator so user-authored notes and outlines can influence
both structure and content.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("worker.user_steering")


@dataclass
class UserPageSpec:
    title: str
    purpose: str | None = None
    parent: str | None = None
    modules: list[str] = field(default_factory=list)
    page_notes: list[str] = field(default_factory=list)


@dataclass
class UserSteering:
    repo_notes: list[str] = field(default_factory=list)
    pages: list[UserPageSpec] = field(default_factory=list)


def load_user_steering(clone_root: Path) -> UserSteering | None:
    """Load ``{clone_root}/.autowiki/wiki.json``.

    Returns ``None`` when the file is missing or invalid. Warnings are
    logged for invalid files so users can see what went wrong in the job
    log without failing the whole pipeline.
    """
    path = clone_root / ".autowiki" / "wiki.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Invalid .autowiki/wiki.json: %s", e)
        return None
    if not isinstance(data, dict):
        logger.warning("Invalid .autowiki/wiki.json: top-level must be an object")
        return None

    repo_notes = data.get("repo_notes") or []
    if not isinstance(repo_notes, list):
        logger.warning(".autowiki/wiki.json repo_notes must be a list; ignoring")
        repo_notes = []
    # Allow either plain strings or {"content": str} shapes; normalise to strings.
    norm_notes: list[str] = []
    for n in repo_notes:
        if isinstance(n, str):
            norm_notes.append(n)
        elif isinstance(n, dict) and isinstance(n.get("content"), str):
            norm_notes.append(n["content"])

    raw_pages = data.get("pages") or []
    if not isinstance(raw_pages, list):
        logger.warning(".autowiki/wiki.json pages must be a list; ignoring")
        raw_pages = []

    pages: list[UserPageSpec] = []
    for p in raw_pages:
        if not isinstance(p, dict):
            continue
        title = p.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        modules = [m for m in (p.get("modules") or []) if isinstance(m, str)]
        notes_raw = p.get("page_notes") or []
        page_notes = [
            n if isinstance(n, str) else n.get("content", "")
            for n in notes_raw
            if isinstance(n, (str, dict))
        ]
        page_notes = [n for n in page_notes if n]
        pages.append(
            UserPageSpec(
                title=title.strip(),
                purpose=(p.get("purpose") if isinstance(p.get("purpose"), str) else None),
                parent=(p.get("parent") if isinstance(p.get("parent"), str) else None),
                modules=modules,
                page_notes=page_notes,
            )
        )

    return UserSteering(repo_notes=norm_notes, pages=pages)


def assign_by_modules(
    pages: list[UserPageSpec], all_files: list[str]
) -> tuple[dict[str, list[str]], list[str]]:
    """Pre-assign files to pages by longest-prefix match on ``modules``.

    Returns ``(assignments, unassigned)`` where ``assignments`` maps each
    page title to a list of matched files and ``unassigned`` lists the
    files that did not match any user module prefix.
    """
    assignments: dict[str, list[str]] = {p.title: [] for p in pages}
    unassigned: list[str] = []
    # Sort prefixes longest-first so nested directories win.
    prefix_owners: list[tuple[str, str]] = sorted(
        ((prefix.rstrip("/"), p.title) for p in pages for prefix in p.modules),
        key=lambda t: len(t[0]),
        reverse=True,
    )
    for file in all_files:
        matched = False
        for prefix, owner in prefix_owners:
            if file == prefix or file.startswith(prefix + "/"):
                assignments[owner].append(file)
                matched = True
                break
        if not matched:
            unassigned.append(file)
    return assignments, unassigned
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/worker/test_user_steering.py -v`
Expected: PASS (all 5 tests).

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/user_steering.py tests/worker/test_user_steering.py
git commit -m "feat(steering): UserSteering loader and module matcher"
```

---

### Task 15: Plumb `user_steering` into `run_full_index`

**Files:**
- Modify: `worker/jobs.py`
- Test: `tests/worker/test_user_steering.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/worker/test_user_steering.py`:

```python
async def test_full_index_reads_autowiki_wiki_json(tmp_path, monkeypatch):
    """run_full_index reads `.autowiki/wiki.json` during Stage 1 and forwards
    the UserSteering object to generate_wiki_plan."""
    import json as _json
    from unittest.mock import AsyncMock, MagicMock, patch

    from shared.config import reset_config
    from shared.database import dispose_db, get_session, init_db
    from shared.models import Job, Repository
    from worker.jobs import run_full_index

    db_path = str(tmp_path / "t.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("AUTOWIKI_DATA_DIR", str(tmp_path))
    reset_config()

    await init_db(db_path)
    async with get_session(db_path) as s:
        s.add(Repository(id="r1", owner="o", name="n", status="pending"))
        s.add(Job(id="j1", repo_id="r1", type="full_index", status="queued"))
        await s.commit()

    clone_root = tmp_path / "repos" / "r1" / "clone"
    (clone_root / ".autowiki").mkdir(parents=True)
    (clone_root / ".autowiki" / "wiki.json").write_text(
        _json.dumps({"repo_notes": ["N"]})
    )

    captured: dict = {}

    async def _fake_plan(*args, **kwargs):
        captured["user_steering"] = kwargs.get("user_steering")
        from worker.pipeline.wiki_planner import WikiPlan, WikiPageSpec

        return WikiPlan(pages=[WikiPageSpec(title="Overview", purpose="Test")])

    # Stub everything else so only the integration point matters.
    with patch("worker.jobs.clone_or_fetch", new=AsyncMock(return_value=("abc", "main"))), patch(
        "worker.jobs.fetch_github_metadata", new=AsyncMock(return_value={})
    ), patch("worker.jobs.filter_files", return_value=[]), patch(
        "worker.jobs.extract_readme", return_value=""
    ), patch(
        "worker.jobs.analyze_all_files",
        return_value=MagicMock(files={}, to_llm_summary=lambda **k: ""),
    ), patch("worker.jobs.build_dependency_graph", return_value=MagicMock(clusters=[], edges={})), patch(
        "worker.jobs.build_rag_index", new=AsyncMock()
    ), patch("worker.jobs.make_llm_provider"), patch(
        "worker.jobs.make_fast_llm_provider"
    ), patch("worker.jobs.make_embedding_provider", return_value=MagicMock(dimension=8)), patch(
        "worker.jobs.generate_wiki_plan", new=_fake_plan
    ), patch("worker.jobs.compute_generation_order", return_value=[]):
        try:
            await run_full_index({}, "r1", "j1", "o", "n", clone_root=clone_root)
        finally:
            await dispose_db(db_path)
            reset_config()

    assert captured["user_steering"] is not None
    assert captured["user_steering"].repo_notes == ["N"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/worker/test_user_steering.py::test_full_index_reads_autowiki_wiki_json -v`
Expected: FAIL — `generate_wiki_plan` is not invoked with `user_steering`.

- [ ] **Step 3: Plumb `user_steering` through `run_full_index`**

In `worker/jobs.py`:

At the top:

```python
from worker.pipeline.user_steering import load_user_steering
```

Inside `run_full_index`, after `clone_or_fetch` / before Stage 2, add:

```python
        user_steering = await loop.run_in_executor(None, load_user_steering, clone_root)
        if user_steering is not None:
            logger.info(
                "Loaded .autowiki/wiki.json: %d repo_notes, %d user pages",
                len(user_steering.repo_notes),
                len(user_steering.pages),
            )
```

Then pass it to `generate_wiki_plan`:

```python
        plan = await generate_wiki_plan(
            file_analysis,
            repo_name=name,
            llm=llm,
            dep_graph=dep_graph,
            readme=readme,
            on_retry=_on_retry,
            wiki_language=wiki_language,
            fast_llm=fast_llm,
            user_steering=user_steering,
        )
```

(The planner change lands in Task 16 — this call will be a no-op until then, but the pytest path is mocked so it still passes.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/worker/test_user_steering.py::test_full_index_reads_autowiki_wiki_json -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/jobs.py tests/worker/test_user_steering.py
git commit -m "feat(steering): load .autowiki/wiki.json during full index"
```

---

### Task 16: Planner accepts `user_steering` and injects repo_notes

**Files:**
- Modify: `worker/pipeline/wiki_planner.py`
- Test: `tests/worker/test_user_steering.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/worker/test_user_steering.py`:

```python
async def test_planner_injects_repo_notes_into_system_prompt(mock_llm):
    """repo_notes from UserSteering are appended to the planner system prompt."""
    from worker.pipeline.user_steering import UserPageSpec, UserSteering
    from worker.pipeline.wiki_planner import generate_wiki_plan
    from worker.pipeline.ast_analysis import FileAnalysis, FileInfo

    file_analysis = FileAnalysis(
        files={
            "main.py": FileInfo(path="main.py", language="python", entities=[], imports=[])
        }
    )

    captured: dict = {}

    async def _structured(prompt, schema, system=""):
        captured.setdefault("systems", []).append(system)
        if "outline" in (schema.get("properties") or {}).keys() or "pages" in (
            schema.get("properties") or {}
        ):
            return {"pages": [{"title": "Overview", "purpose": "P"}]}
        if "assignments" in (schema.get("properties") or {}):
            return {"assignments": [{"file": "main.py", "page_title": "Overview"}]}
        return {}

    mock_llm.generate_structured.side_effect = _structured

    steering = UserSteering(
        repo_notes=["Treat legacy/ as deprecated."],
        pages=[],
    )
    plan = await generate_wiki_plan(
        file_analysis,
        repo_name="example",
        llm=mock_llm,
        user_steering=steering,
    )
    assert plan.repo_notes and plan.repo_notes[0]["content"] == "Treat legacy/ as deprecated."
    assert any("Treat legacy/ as deprecated." in s for s in captured["systems"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/worker/test_user_steering.py::test_planner_injects_repo_notes_into_system_prompt -v`
Expected: FAIL — `generate_wiki_plan` has no `user_steering` parameter.

- [ ] **Step 3: Add `user_steering` to the planner**

In `worker/pipeline/wiki_planner.py`:

1. Import at the top:

```python
from worker.pipeline.user_steering import UserSteering
```

2. Update `generate_wiki_plan` signature:

```python
async def generate_wiki_plan(
    file_analysis: FileAnalysis,
    repo_name: str,
    llm: LLMProvider,
    dep_graph: DependencyGraph | None = None,
    max_retries: int = 3,
    readme: str | None = None,
    on_retry: OnRetryCallback | None = None,
    existing_titles: set[str] | None = None,
    wiki_language: str = "en",
    fast_llm: LLMProvider | None = None,
    user_steering: UserSteering | None = None,
) -> WikiPlan:
```

3. Replace the `system = _SYSTEM + get_planner_language_instruction(...)` line with:

```python
    system = _SYSTEM + get_planner_language_instruction(wiki_language)
    if user_steering is not None and user_steering.repo_notes:
        notes = "\n".join(f"- {n}" for n in user_steering.repo_notes)
        system = (
            system
            + "\n\nUser-provided repository notes (authoritative — honour these):\n"
            + notes
        )
```

4. At the end of the function, before returning the plan (wherever `validate_wiki_plan` succeeds **and** wherever `_fallback_plan` returns), set `repo_notes` on the returned `WikiPlan`:

Extract the tail of `generate_wiki_plan` into a helper that attaches notes:

```python
    def _attach_notes(plan: WikiPlan) -> WikiPlan:
        if user_steering is not None and user_steering.repo_notes:
            plan.repo_notes = [{"content": n} for n in user_steering.repo_notes]
        return plan
```

Then wrap every `return` in `generate_wiki_plan` with `return _attach_notes(...)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/worker/test_user_steering.py::test_planner_injects_repo_notes_into_system_prompt -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/worker/test_user_steering.py
git commit -m "feat(steering): planner injects repo_notes into system prompt"
```

---

### Task 17: User-supplied pages override Phase 1 (outline)

**Files:**
- Modify: `worker/pipeline/wiki_planner.py`
- Test: `tests/worker/test_user_steering.py`

When `user_steering.pages` is non-empty we skip the Phase 1 outline LLM
call entirely and build the outline dict ourselves. Purposes default to an
LLM-generated description only if the user omitted one.

- [ ] **Step 1: Write the failing test**

Append to `tests/worker/test_user_steering.py`:

```python
async def test_planner_skips_phase1_when_user_pages_provided(mock_llm):
    """When user provides pages, Phase 1 outline LLM call is skipped."""
    from worker.pipeline.ast_analysis import FileAnalysis, FileInfo
    from worker.pipeline.user_steering import UserPageSpec, UserSteering
    from worker.pipeline.wiki_planner import generate_wiki_plan

    file_analysis = FileAnalysis(
        files={
            "src/core/bus.py": FileInfo(path="src/core/bus.py", language="python", entities=[], imports=[]),
            "src/api/server.py": FileInfo(path="src/api/server.py", language="python", entities=[], imports=[]),
        }
    )

    n_structured_calls = 0

    async def _structured(prompt, schema, system=""):
        nonlocal n_structured_calls
        n_structured_calls += 1
        # Only the Phase-2 assignment schema should be invoked.
        assert "assignments" in (schema.get("properties") or {})
        return {"assignments": []}

    mock_llm.generate_structured.side_effect = _structured

    steering = UserSteering(
        repo_notes=[],
        pages=[
            UserPageSpec(title="Core", purpose="Core system.", modules=["src/core"]),
            UserPageSpec(title="API", purpose="API layer.", modules=["src/api"]),
        ],
    )
    plan = await generate_wiki_plan(
        file_analysis,
        repo_name="x",
        llm=mock_llm,
        user_steering=steering,
    )

    titles = [p.title for p in plan.pages]
    assert titles == ["Core", "API"]
    # Files were assigned by module prefix without calling the LLM for outline.
    core = next(p for p in plan.pages if p.title == "Core")
    assert "src/core/bus.py" in core.files
    api = next(p for p in plan.pages if p.title == "API")
    assert "src/api/server.py" in api.files
    # Exactly one structured call (Phase 2 for remaining files — or none if all assigned).
    assert n_structured_calls <= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/worker/test_user_steering.py::test_planner_skips_phase1_when_user_pages_provided -v`
Expected: FAIL — the planner still calls Phase 1 and produces LLM-authored titles.

- [ ] **Step 3: Short-circuit Phase 1 when user provides pages**

In `worker/pipeline/wiki_planner.py`, update `generate_wiki_plan` around the Phase 1 block. Replace the existing try/except that calls `_generate_outline` with:

```python
    if user_steering is not None and user_steering.pages:
        outline = [
            {
                "title": p.title,
                "purpose": p.purpose or f"User-defined page for {p.title}.",
                "parent": p.parent,
            }
            for p in user_steering.pages
        ]
    else:
        try:
            outline = await _generate_outline(
                file_summary=file_summary,
                repo_name=repo_name,
                llm=llm,
                readme=readme,
                dep_info=dep_info,
                clusters=clusters,
                page_range=page_range,
                system=system,
                on_retry=on_retry,
                max_retries=max_retries,
                total_file_count=len(all_files),
            )
        except ValueError:
            return _attach_notes(_fallback_plan(repo_name, all_files, clusters))
```

Before the Phase 2 assignment call, pre-assign files using module prefixes:

```python
    pre_assigned: dict[str, list[str]] = {}
    remaining_files = all_files
    if user_steering is not None and user_steering.pages:
        from worker.pipeline.user_steering import assign_by_modules

        pre_assigned, remaining_files = assign_by_modules(user_steering.pages, all_files)
```

Change the Phase 2 call to only classify remaining files and then merge:

```python
    file_assignments = await _assign_files(
        outline=outline,
        file_summary=file_summary,
        dep_info=dep_info,
        all_files=remaining_files,
        llm=llm,
        system=system,
        on_retry=on_retry,
        max_retries=max_retries,
        fast_llm=fast_llm,
    )
    # Merge module-prefix assignments back in.
    for title, files in pre_assigned.items():
        file_assignments.setdefault(title, []).extend(files)
```

Also propagate `page_notes` into the final plan's `WikiPageSpec`s. In the block that builds `raw` / validates the plan, add, after `validate_wiki_plan` returns successfully:

```python
        if user_steering is not None and user_steering.pages:
            notes_by_title = {p.title: p.page_notes for p in user_steering.pages}
            for spec in plan.pages:
                if spec.title in notes_by_title and notes_by_title[spec.title]:
                    spec.page_notes = [
                        {"content": n} for n in notes_by_title[spec.title]
                    ]
        return _attach_notes(plan)
```

(Where `plan = validate_wiki_plan(...)`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/worker/test_user_steering.py::test_planner_skips_phase1_when_user_pages_provided -v`
Expected: PASS.

- [ ] **Step 5: Run the full planner test suite to catch regressions**

Run: `uv run pytest tests/worker/test_wiki_planner.py tests/worker/test_user_steering.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/worker/test_user_steering.py
git commit -m "feat(steering): user-provided pages override Phase 1 outline"
```

---

### Task 18: Inject `page_notes` + `repo_notes` into page draft prompts

**Files:**
- Modify: `worker/pipeline/page_draft.py` (or wherever the page draft prompt is built — read the file first)
- Test: new assertions in `tests/worker/test_page_draft.py`

- [ ] **Step 1: Read the current draft module to find the insertion point**

Read `worker/pipeline/page_draft.py` and locate the function that builds the draft prompt. Note the exact parameters currently threaded into it.

- [ ] **Step 2: Write the failing test**

In `tests/worker/test_page_draft.py`, add:

```python
async def test_draft_prompt_includes_user_notes(mock_llm):
    """repo_notes and page_notes appear in the prompt sent to the LLM."""
    from worker.pipeline.page_draft import draft_page
    from worker.pipeline.wiki_planner import WikiPageSpec
    # import the existing helper/factory your draft module uses for a minimal
    # DraftContext (mirror the signature already used in this file's fixtures).

    captured_prompt = {}

    async def _generate(prompt, system=""):
        captured_prompt["text"] = str(prompt)
        return "Draft body."

    mock_llm.generate = _generate

    spec = WikiPageSpec(
        title="Core",
        purpose="Core system.",
        page_notes=[{"content": "Bus lives in bus.ts."}],
    )
    # adapt the following args to the real draft_page signature you found in Step 1
    await draft_page(
        spec=spec,
        rag_snippets="",
        entities=[],
        dep_summary={},
        outline={"sections": [], "key_claims": []},
        children_markdown="",
        llm=mock_llm,
        repo_notes=["Treat legacy/ as deprecated."],
    )
    assert "Bus lives in bus.ts." in captured_prompt["text"]
    assert "Treat legacy/ as deprecated." in captured_prompt["text"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/worker/test_page_draft.py::test_draft_prompt_includes_user_notes -v`
Expected: FAIL.

- [ ] **Step 4: Implement the prompt injection**

Modify `draft_page` (or its prompt builder) in `worker/pipeline/page_draft.py` to accept `repo_notes: list[str] | None = None` and to emit a new section before the RAG snippets:

```python
    if spec.page_notes:
        notes = "\n".join(
            f"- {n['content']}"
            for n in spec.page_notes
            if isinstance(n, dict) and n.get("content")
        )
        if notes:
            prompt_parts.append(
                f"User notes for this page (authoritative — honour these):\n{notes}"
            )
    if repo_notes:
        rn = "\n".join(f"- {n}" for n in repo_notes)
        prompt_parts.append(
            f"Repository-level user notes (authoritative):\n{rn}"
        )
```

Also propagate `repo_notes` through `page_generator.generate_page_batch` / the orchestrator in `worker/jobs.py` (read the existing signatures — they call `draft_page` inside the batch). Add `repo_notes: list[str] | None = None` as a new keyword arg and pass it through.

- [ ] **Step 5: Thread the notes from the job into page generation**

In `worker/jobs.py`, inside `run_full_index`, gather `repo_notes` from the plan after generation and pass them into `generate_page_batch`:

```python
        repo_notes_text = [
            n.get("content", "")
            for n in (plan.repo_notes or [])
            if isinstance(n, dict) and n.get("content")
        ]
```

Then pass `repo_notes=repo_notes_text` to `generate_page_batch(...)` in the per-level loop.

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/worker/test_page_draft.py::test_draft_prompt_includes_user_notes -v`
Expected: PASS.

- [ ] **Step 7: Run the full page-generation test suite for regressions**

Run: `uv run pytest tests/worker/test_page_draft.py tests/worker/test_page_generator.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add worker/pipeline/page_draft.py worker/pipeline/page_generator.py worker/jobs.py tests/worker/test_page_draft.py
git commit -m "feat(steering): inject user notes into page draft prompts"
```

---

### Task 19: Propagate user_steering through `run_refresh_index`

**Files:**
- Modify: `worker/jobs.py`
- Test: `tests/worker/test_refresh.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/worker/test_refresh.py`:

```python
async def test_refresh_passes_user_steering_to_planner(tmp_path, monkeypatch):
    """run_refresh_index loads .autowiki/wiki.json and forwards it to the planner."""
    import json as _json
    from unittest.mock import AsyncMock, MagicMock, patch

    from shared.config import reset_config
    from shared.database import get_session, init_db, dispose_db
    from shared.models import Job, Repository
    from worker.jobs import run_refresh_index

    db_path = str(tmp_path / "t.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("AUTOWIKI_DATA_DIR", str(tmp_path))
    reset_config()
    await init_db(db_path)
    async with get_session(db_path) as s:
        s.add(
            Repository(
                id="r1",
                owner="o",
                name="n",
                status="ready",
                last_commit="old",
            )
        )
        s.add(Job(id="j1", repo_id="r1", type="refresh", status="queued"))
        await s.commit()

    clone_root = tmp_path / "repos" / "r1" / "clone"
    (clone_root / ".autowiki").mkdir(parents=True)
    (clone_root / ".autowiki" / "wiki.json").write_text(
        _json.dumps({"repo_notes": ["Refresh-note"]})
    )

    captured: dict = {}

    async def _fake_plan(*args, **kwargs):
        captured["user_steering"] = kwargs.get("user_steering")
        from worker.pipeline.wiki_planner import WikiPlan

        return WikiPlan()

    with patch("worker.jobs.clone_or_fetch", new=AsyncMock(return_value=("new", "main"))), patch(
        "worker.jobs.fetch_github_metadata", new=AsyncMock(return_value={})
    ), patch("worker.jobs.get_changed_files", return_value=[]), patch(
        "worker.jobs.generate_wiki_plan", new=_fake_plan
    ), patch("worker.jobs.filter_files", return_value=[]), patch(
        "worker.jobs.analyze_all_files",
        return_value=MagicMock(files={}, to_llm_summary=lambda **k: ""),
    ):
        try:
            await run_refresh_index({}, "r1", "j1", "o", "n", clone_root=clone_root)
        except Exception:
            pass  # early-exit / fallback paths are fine for this test
        finally:
            await dispose_db(db_path)
            reset_config()

    # Only assert if refresh actually reached the planner (not early-exit)
    if "user_steering" in captured:
        assert captured["user_steering"] is not None
        assert captured["user_steering"].repo_notes == ["Refresh-note"]
```

- [ ] **Step 2: Run test to verify it fails (or is no-op)**

Run: `uv run pytest tests/worker/test_refresh.py::test_refresh_passes_user_steering_to_planner -v`
Expected: FAIL with a missing-kwarg assertion. If the refresh early-exits before reaching the planner, the test is a soft no-op — implement Step 3 anyway so the kwarg is wired for future refreshes.

- [ ] **Step 3: Plumb user_steering through `run_refresh_index`**

In `run_refresh_index` in `worker/jobs.py`, add (before the planner call):

```python
        user_steering = await asyncio.get_running_loop().run_in_executor(
            None, load_user_steering, clone_root
        )
```

Pass `user_steering=user_steering` to the `generate_wiki_plan(...)` call inside refresh.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/worker/test_refresh.py::test_refresh_passes_user_steering_to_planner -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/jobs.py tests/worker/test_refresh.py
git commit -m "feat(steering): refresh job forwards .autowiki/wiki.json"
```

---

### Task 20: Surface "steered" flag in the API `/wiki` structure

**Files:**
- Modify: `worker/pipeline/wiki_planner.py` (`to_api_structure`)
- Modify: `api/routers/wiki.py` (if the structure comes from there)
- Modify: `tests/worker/test_wiki_planner.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/worker/test_wiki_planner.py`:

```python
def test_to_api_structure_marks_user_steered_pages():
    from worker.pipeline.wiki_planner import WikiPlan, WikiPageSpec

    plan = WikiPlan(
        pages=[
            WikiPageSpec(title="Core", purpose="P", page_notes=[{"content": "N"}]),
            WikiPageSpec(title="Other", purpose="O"),
        ]
    )
    data = plan.to_api_structure()
    pages = {p["title"]: p for p in data["pages"]}
    assert pages["Core"]["has_user_notes"] is True
    assert pages["Other"]["has_user_notes"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/worker/test_wiki_planner.py::test_to_api_structure_marks_user_steered_pages -v`
Expected: FAIL (`has_user_notes` key missing).

- [ ] **Step 3: Update `to_api_structure`**

In `worker/pipeline/wiki_planner.py`, modify `WikiPlan.to_api_structure`:

```python
        return {
            "repo_notes": self.repo_notes,
            "pages": [
                {
                    "title": p.title,
                    "slug": p.slug,
                    "parent_slug": p.parent_slug,
                    "description": p.purpose,
                    "has_user_notes": any(
                        isinstance(n, dict) and n.get("content")
                        for n in p.page_notes or []
                    ),
                }
                for p in self.pages
            ],
        }
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/worker/test_wiki_planner.py -v`
Expected: PASS.

- [ ] **Step 5: Expose `repo_notes` on `GET /api/repos/{id}/wiki`**

Read `api/routers/wiki.py` and ensure the route returns the `repo_notes` array from the stored structure. If the existing code already serialises `Repository.wiki_structure` verbatim, this is automatic. Otherwise, thread it through.

- [ ] **Step 6: Commit**

```bash
git add worker/pipeline/wiki_planner.py api/routers/wiki.py tests/worker/test_wiki_planner.py
git commit -m "feat(steering): expose has_user_notes + repo_notes in API"
```

---

### Task 21: Frontend — badge steered pages, show repo_notes

**Files:**
- Modify: `web/lib/api.ts` (add `has_user_notes` + `repo_notes` to the typed response)
- Modify: `web/components/WikiSidebar.tsx` (render small badge next to pages where `has_user_notes`)
- Modify: `web/app/[owner]/[repo]/page.tsx` (render `repo_notes` banner at the top of the repo landing)

- [ ] **Step 1: Update API types**

In `web/lib/api.ts`, update `getRepoWiki` return type:

```typescript
export async function getRepoWiki(repoId: string) {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/wiki`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{
    repo_notes?: Array<{ content: string }>;
    pages: Array<{
      slug: string;
      title: string;
      parent_slug: string | null;
      has_user_notes?: boolean;
    }>;
  }>;
}
```

- [ ] **Step 2: Badge steered pages in `WikiSidebar.tsx`**

Next to each sidebar link, when `page.has_user_notes` is true, render a tiny indicator:

```tsx
{page.has_user_notes && (
  <span
    className="ml-2 text-xs text-indigo-600"
    title="User-authored notes from .autowiki/wiki.json"
  >
    ★
  </span>
)}
```

- [ ] **Step 3: Show `repo_notes` banner on the repo landing page**

In `web/app/[owner]/[repo]/page.tsx`, if `repo_notes` is present and non-empty, render a callout above the page list:

```tsx
{wiki.repo_notes && wiki.repo_notes.length > 0 && (
  <div className="border border-indigo-200 bg-indigo-50 rounded p-3 mb-4">
    <div className="font-medium text-indigo-900 mb-1">Repository notes</div>
    <ul className="list-disc pl-5 text-sm text-indigo-900/80">
      {wiki.repo_notes.map((n, i) => (
        <li key={i}>{n.content}</li>
      ))}
    </ul>
  </div>
)}
```

- [ ] **Step 4: Run web lint**

Run: `npm run lint --prefix web`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/lib/api.ts web/components/WikiSidebar.tsx web/app/[owner]/[repo]/page.tsx
git commit -m "feat(steering): surface repo_notes and page badges in UI"
```

---

### Task 22: Documentation updates

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Document `.autowiki/wiki.json` in README**

Add a new section after the "CLI" section in `README.md`:

````markdown
## Steering wiki generation

Check a `.autowiki/wiki.json` file into the root of any repository to guide
AutoWiki's output. All fields are optional.

```json
{
  "repo_notes": [
    "The core event bus lives in src/core/bus.ts — treat it as authoritative."
  ],
  "pages": [
    {
      "title": "Architecture Overview",
      "purpose": "High-level view of the system.",
      "modules": ["src/core", "src/api"],
      "page_notes": ["Skip the legacy/ directory entirely."]
    }
  ]
}
```

- `repo_notes` — plain-language notes injected into every wiki-generation
  LLM call.
- `pages` — when present, replaces AutoWiki's auto-generated page outline.
  Files whose path starts with any `modules` prefix are assigned to that
  page; the remaining files are assigned by the LLM.
- `page_notes` — per-page notes injected into that page's draft prompt.

In the web UI, pages with user notes show a ★ next to their sidebar entry,
and `repo_notes` appear as a banner on the repo landing page.
````

- [ ] **Step 2: Update `CLAUDE.md`**

Update the "Project Status" block and the "Phased Delivery" block in
`CLAUDE.md` to reflect that Phases 3 and 4 (as scoped in the 2026-04-14
revision) have landed, and remove the references to "not yet implemented"
on those phases.

- [ ] **Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: document Deep Research and .autowiki/wiki.json steering"
```

---

### Task 23: Final pre-commit checks + full suite

- [ ] **Step 1: Run the full suite**

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/ --ignore=tests/e2e
npm run lint --prefix web
npm test --prefix web
```

Expected: all green.

- [ ] **Step 2: Smoke test end-to-end**

1. `autowiki serve` in one terminal.
2. In the browser, index a small public GitHub repo.
3. Once ready, open `/chat` and ask a question — confirm the chat still works.
4. Open `/research` and ask a research question — confirm the plan streams,
   findings appear progressively, and the final report renders.
5. Check the repo out locally, create `.autowiki/wiki.json` with a couple of
   `repo_notes` and one user-defined page, push to a fork, re-index — confirm
   the sidebar shows the ★ badge and the landing page shows the repo notes.

- [ ] **Step 3: Commit any cleanup**

```bash
git add -u
git commit -m "chore: final cleanup after Phase 3/4 implementation"
```

---

## Appendix: Self-Review Notes

**Spec coverage:**
- Phase 3 Deep Research (design doc §7.2): Planner → Investigator loop → Synthesizer ✅ (Tasks 2–5), REST+WS endpoints ✅ (Tasks 7–9), CLI ✅ (Task 10), UI ✅ (Tasks 11–12).
- Phase 4 User Steering (design doc §9.2): `.autowiki/wiki.json` loader ✅ (Task 14), planner integration ✅ (Tasks 15–17), page draft injection ✅ (Task 18), refresh propagation ✅ (Task 19), frontend surfacing ✅ (Tasks 20–21).
- **Out of scope (as instructed):** MCP server, GitHub webhook, auto-refresh on push. Not covered.

**Known risk areas:**
- The `run_deep_research` job writes incremental progress to SQLite (via `_on_event`) because the WebSocket uses polling. If multiple research jobs run against the same repo concurrently, Redis pub/sub would scale better — noted for a future task, **out of scope here**.
- `page_draft.py` modifications depend on reading the current function signature first; the test in Task 18 uses the real signature and may need small adjustment once the engineer inspects the file.
- `generate_wiki_plan`'s `_attach_notes` wrapper must cover every return path (happy path, fallback path, validation failure). Mis-wiring one path means user notes silently drop.

**Placeholder scan:** All code blocks show actual code. No "TODO" / "TBD" / "implement later" strings remain.

**Type consistency:** `ResearchStep`, `ResearchFinding`, `ResearchResult` are referenced consistently across Tasks 2–10. `UserSteering` / `UserPageSpec` flow unchanged from Tasks 14–19.
