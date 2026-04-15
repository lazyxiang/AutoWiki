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
    from shared.database import dispose_db, get_session
    from shared.models import ResearchReport

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

            assert calls and calls[0]["question"] == "How does refresh work?"
    finally:
        await dispose_db(db_path)


async def test_get_research_returns_persisted_report(research_env):
    """GET returns the plan/findings/report as persisted in SQLite."""
    import json as _json

    from api.main import app
    from shared.database import dispose_db, get_session, init_db
    from shared.models import Job, Repository, ResearchReport

    db_path = research_env
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


async def test_ws_research_404_closes_with_4004(research_env):
    """WebSocket closes with code 4004 when the report does not exist."""
    from api.main import app
    from shared.database import init_db

    db_path = research_env
    await init_db(db_path)

    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/repos/r1/research/nonexistent") as ws:
                ws.receive_json()
    assert exc_info.value.code == 4004
