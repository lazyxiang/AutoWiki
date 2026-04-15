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
