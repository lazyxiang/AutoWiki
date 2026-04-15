"""Tests for the Deep Research feature (orchestrator + persistence)."""

from __future__ import annotations


async def test_research_report_model_persists_roundtrip(tmp_path):
    """Persisting a ResearchReport round-trips every field."""
    from shared.database import dispose_db, get_session, init_db
    from shared.models import Job, Repository, ResearchReport

    db_path = str(tmp_path / "t.db")
    await init_db(db_path)
    try:
        async with get_session(db_path) as s:
            s.add(Repository(id="r1", owner="o", name="n", status="ready"))
            s.add(
                Job(
                    id="job1",
                    repo_id="r1",
                    type="index",
                    status="pending",
                )
            )
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
            assert loaded.plan_json == "[]"
            assert loaded.findings_json == "[]"
            assert loaded.report_markdown == ""
            assert loaded.error is None
            assert loaded.finished_at is None
            assert loaded.created_at is not None
    finally:
        await dispose_db(db_path)


async def test_plan_research_returns_investigation_steps(mock_llm):
    """The planner extracts structured investigation steps from an LLM response."""
    from worker.deep_research import ResearchStep, plan_research

    async def _structured(*args, **kwargs):
        return {
            "plan": [
                {
                    "query": "Where does the pipeline start?",
                    "rationale": "Locate the entry point.",
                },
                {
                    "query": "How is incremental refresh detected?",
                    "rationale": "Understand diff logic.",
                },
                {
                    "query": "Which module owns embedding?",
                    "rationale": "Find storage layer.",
                },
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


async def test_investigate_step_returns_finding_with_sources(mock_llm, mock_embedding):
    """Each investigation step embeds its query, searches FAISS, and calls the LLM."""
    from unittest.mock import MagicMock

    from worker.deep_research import ResearchStep, investigate_step

    store = MagicMock()
    store.search.return_value = [
        {
            "file": "worker/jobs.py",
            "text": "def run_full_index(...): ...",
            "start_line": 120,
        },
        {
            "file": "worker/pipeline/ingestion.py",
            "text": "def filter_files(...): ...",
            "start_line": 42,
        },
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
    assert "start_line" in finding.sources[0]
    assert "end_line" in finding.sources[0]


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
