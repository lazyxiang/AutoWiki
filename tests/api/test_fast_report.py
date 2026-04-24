"""API tests for fast report REST + WebSocket contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient


async def _prep_repo(db_path: str):
    from shared.database import get_session, init_db
    from shared.models import Repository

    await init_db(db_path)
    async with get_session(db_path) as s:
        s.add(
            Repository(
                id="r1",
                owner="o",
                name="n",
                status="ready",
                last_commit="deadbeef",
            )
        )
        await s.commit()


@pytest.fixture
def fast_report_env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("AUTOWIKI_DATA_DIR", str(tmp_path))
    from shared.config import reset_config

    reset_config()
    return db_path


async def test_start_fast_report_returns_ids_and_persists_queued_rows(
    fast_report_env, monkeypatch
):
    """POST creates the queued job/report/section rows and enqueues the worker job."""
    db_path = fast_report_env
    await _prep_repo(db_path)

    calls: list[dict] = []

    async def _fake_enqueue(*args, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("api.routers.fast_report._enqueue_fast_report", _fake_enqueue)

    from api.main import app
    from shared.database import dispose_db, get_session
    from shared.models import FastReport, FastReportSection, Job

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/repos/r1/fast-reports",
                json={"question": "How does indexing work?"},
            )

        assert response.status_code == 202
        body = response.json()
        assert "job_id" in body
        assert "report_id" in body
        assert "section_id" in body
        assert body["status"] == "queued"

        async with get_session(db_path) as s:
            job = await s.get(Job, body["job_id"])
            report = await s.get(FastReport, body["report_id"])
            section = await s.get(FastReportSection, body["section_id"])

            assert job is not None
            assert job.type == "fast_report"
            assert job.status == "queued"

            assert report is not None
            assert report.repo_id == "r1"
            assert report.commit_sha == "deadbeef"
            assert report.status == "queued"
            assert report.active_section_id is None
            now_naive = datetime.now(UTC).replace(tzinfo=None)
            assert report.expires_at > now_naive + timedelta(days=6)

            assert section is not None
            assert section.report_id == report.id
            assert section.query == "How does indexing work?"
            assert section.status == "queued"

        assert calls == [
            {
                "repo_id": "r1",
                "job_id": body["job_id"],
                "report_id": body["report_id"],
                "section_id": body["section_id"],
                "question": "How does indexing work?",
            }
        ]
    finally:
        await dispose_db(db_path)


async def test_get_fast_report_returns_persisted_report_payload(fast_report_env):
    """GET returns report metadata, job linkage, and parsed section payloads."""
    from api.main import app
    from shared.database import dispose_db, get_session, init_db
    from shared.models import FastReport, FastReportSection, Job, Repository

    db_path = fast_report_env
    await init_db(db_path)

    try:
        async with get_session(db_path) as s:
            s.add(Repository(id="r1", owner="o", name="n", status="ready"))
            s.add(Job(id="fr1", repo_id="r1", type="fast_report", status="done"))
            report = FastReport(
                id="fr1",
                repo_id="r1",
                commit_sha="deadbeef",
                status="done",
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            s.add(report)
            s.add(
                FastReportSection(
                    id="sec1",
                    report_id="fr1",
                    query="How does indexing work?",
                    title="Indexing Flow",
                    summary="The job pipeline drives indexing.",
                    markdown="# Indexing Flow",
                    citations_json=json.dumps(
                        [
                            {
                                "id": "code-1",
                                "file_path": "worker/jobs.py",
                                "start_line": 10,
                                "end_line": 20,
                                "label": "worker/jobs.py:10-20",
                                "kind": "code_evidence",
                                "reason": "Entry point",
                                "score": 0.9,
                            }
                        ]
                    ),
                    evidence_blocks_json=json.dumps(
                        [
                            {
                                "citation_id": "code-1",
                                "snippet_start": 10,
                                "snippet_end": 12,
                                "full_start": 8,
                                "full_end": 22,
                                "default_context": 3,
                                "expanded_context": 18,
                                "is_collapsed": True,
                                "code": "async def run_full_index(...):",
                                "symbol_path": "worker.jobs.run_full_index",
                            }
                        ]
                    ),
                    related_wiki_pages_json=json.dumps(
                        [
                            {
                                "slug": "overview",
                                "title": "Overview",
                                "reason": "System map",
                            }
                        ]
                    ),
                    related_diagrams_json=json.dumps(
                        [
                            {
                                "id": "diag-1",
                                "title": "Flow",
                                "type": "flowchart",
                                "source": "graph TD; A-->B;",
                                "caption": "Execution flow",
                                "reason": "Clarifies handoff",
                                "citations": ["code-1"],
                                "placement": "inline",
                            }
                        ]
                    ),
                    status="done",
                )
            )
            await s.flush()
            report.active_section_id = "sec1"
            await s.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/repos/r1/fast-reports/fr1")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == "fr1"
        assert body["repo_id"] == "r1"
        assert body["job_id"] == "fr1"
        assert body["commit_sha"] == "deadbeef"
        assert body["status"] == "done"
        assert body["error"] is None
        assert body["active_section_id"] == "sec1"
        assert body["sections"][0]["id"] == "sec1"
        assert body["sections"][0]["query"] == "How does indexing work?"
        assert body["sections"][0]["citations"][0]["id"] == "code-1"
        assert body["sections"][0]["evidence_blocks"][0]["citation_id"] == "code-1"
        assert body["sections"][0]["related_wiki_pages"][0]["slug"] == "overview"
        assert body["sections"][0]["related_diagrams"][0]["id"] == "diag-1"
    finally:
        await dispose_db(db_path)


async def test_get_fast_report_returns_persisted_failure_detail(fast_report_env):
    """GET surfaces the persisted job failure reason for failed reports."""
    from api.main import app
    from shared.database import dispose_db, get_session, init_db
    from shared.models import FastReport, FastReportSection, Job, Repository

    db_path = fast_report_env
    await init_db(db_path)

    try:
        async with get_session(db_path) as s:
            s.add(Repository(id="r1", owner="o", name="n", status="ready"))
            s.add(
                Job(
                    id="fr-failed",
                    repo_id="r1",
                    type="fast_report",
                    status="failed",
                    error="Embedding index missing for repo snapshot",
                )
            )
            report = FastReport(
                id="fr-failed",
                repo_id="r1",
                commit_sha="deadbeef",
                status="failed",
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            s.add(report)
            s.add(
                FastReportSection(
                    id="sec-failed",
                    report_id="fr-failed",
                    query="Why did generation fail?",
                    title="Pending",
                    summary=None,
                    markdown="",
                    citations_json="[]",
                    evidence_blocks_json="[]",
                    related_wiki_pages_json="[]",
                    related_diagrams_json="[]",
                    status="failed",
                )
            )
            await s.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/repos/r1/fast-reports/fr-failed")

        assert response.status_code == 200
        body = response.json()
        assert body["job_id"] == "fr-failed"
        assert body["status"] == "failed"
        assert body["error"] == "Embedding index missing for repo snapshot"
    finally:
        await dispose_db(db_path)


async def test_get_fast_report_unexpired_reload_returns_persisted_data_without_enqueue(
    fast_report_env, monkeypatch
):
    """Reloading an unexpired report serves DB state only and never re-enqueues work."""
    from api.main import app
    from shared.database import dispose_db, get_session, init_db
    from shared.models import FastReport, FastReportSection, Job, Repository

    db_path = fast_report_env
    await init_db(db_path)
    enqueue_calls: list[dict] = []

    async def _fake_enqueue(*args, **kwargs):
        enqueue_calls.append(kwargs)

    monkeypatch.setattr("api.routers.fast_report._enqueue_fast_report", _fake_enqueue)

    try:
        async with get_session(db_path) as s:
            s.add(Repository(id="r1", owner="o", name="n", status="ready"))
            s.add(Job(id="fr1", repo_id="r1", type="fast_report", status="done"))
            report = FastReport(
                id="fr1",
                repo_id="r1",
                commit_sha="persisted-sha",
                status="done",
                expires_at=datetime.now(UTC) + timedelta(days=2),
            )
            s.add(report)
            s.add(
                FastReportSection(
                    id="sec1",
                    report_id="fr1",
                    query="Persisted question",
                    title="Persisted answer",
                    summary="Persisted summary",
                    markdown="# Persisted answer",
                    citations_json="[]",
                    evidence_blocks_json="[]",
                    related_wiki_pages_json=json.dumps(
                        [{"slug": "overview", "title": "Overview"}]
                    ),
                    related_diagrams_json="[]",
                    status="done",
                )
            )
            await s.flush()
            report.active_section_id = "sec1"
            await s.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/repos/r1/fast-reports/fr1")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == "fr1"
        assert body["repo_id"] == "r1"
        assert body["job_id"] == "fr1"
        assert body["commit_sha"] == "persisted-sha"
        assert body["status"] == "done"
        assert body["error"] is None
        assert body["active_section_id"] == "sec1"
        assert body["sections"] == [
            {
                "id": "sec1",
                "report_id": "fr1",
                "query": "Persisted question",
                "title": "Persisted answer",
                "summary": "Persisted summary",
                "markdown": "# Persisted answer",
                "citations": [],
                "evidence_blocks": [],
                "related_wiki_pages": [{"slug": "overview", "title": "Overview"}],
                "related_diagrams": [],
                "created_at": body["sections"][0]["created_at"],
                "status": "done",
            }
        ]
        assert enqueue_calls == []
    finally:
        await dispose_db(db_path)


async def test_start_fast_report_appends_section_to_existing_unexpired_report(
    fast_report_env, monkeypatch
):
    """POST with report_id reuses the report/job rows and appends a queued section."""
    from api.main import app
    from shared.database import dispose_db, get_session, init_db
    from shared.models import FastReport, FastReportSection, Job, Repository

    db_path = fast_report_env
    await init_db(db_path)
    calls: list[dict] = []

    async def _fake_enqueue(*args, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("api.routers.fast_report._enqueue_fast_report", _fake_enqueue)

    try:
        async with get_session(db_path) as s:
            s.add(
                Repository(
                    id="r1",
                    owner="o",
                    name="n",
                    status="ready",
                    last_commit="new-sha",
                )
            )
            s.add(Job(id="fr1", repo_id="r1", type="fast_report", status="done"))
            s.add(
                FastReport(
                    id="fr1",
                    repo_id="r1",
                    commit_sha="old-sha",
                    status="done",
                    expires_at=datetime.now(UTC) + timedelta(days=3),
                )
            )
            s.add(
                FastReportSection(
                    id="sec-old",
                    report_id="fr1",
                    query="Initial question",
                    title="Initial",
                    summary="Initial summary",
                    markdown="# Initial",
                    citations_json="[]",
                    evidence_blocks_json="[]",
                    related_wiki_pages_json="[]",
                    related_diagrams_json="[]",
                    status="done",
                )
            )
            await s.flush()
            report = await s.get(FastReport, "fr1")
            assert report is not None
            report.active_section_id = "sec-old"
            await s.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/repos/r1/fast-reports",
                json={
                    "question": "What changed in the auth flow?",
                    "report_id": "fr1",
                },
            )

        assert response.status_code == 202
        body = response.json()
        assert body["job_id"] == "fr1"
        assert body["report_id"] == "fr1"
        assert body["status"] == "queued"
        assert body["section_id"] != "sec-old"

        async with get_session(db_path) as s:
            job = await s.get(Job, "fr1")
            report = await s.get(FastReport, "fr1")
            new_section = await s.get(FastReportSection, body["section_id"])
            old_section = await s.get(FastReportSection, "sec-old")

            assert job is not None
            assert job.status == "queued"
            assert job.progress == 0
            assert job.error is None

            assert report is not None
            assert report.status == "queued"
            assert report.commit_sha == "old-sha"
            assert report.active_section_id == "sec-old"
            now_naive = datetime.now(UTC).replace(tzinfo=None)
            assert report.expires_at > now_naive + timedelta(days=6)

            assert old_section is not None
            assert old_section.status == "done"

            assert new_section is not None
            assert new_section.report_id == "fr1"
            assert new_section.query == "What changed in the auth flow?"
            assert new_section.status == "queued"

        assert calls == [
            {
                "repo_id": "r1",
                "job_id": "fr1",
                "report_id": "fr1",
                "section_id": body["section_id"],
                "question": "What changed in the auth flow?",
            }
        ]
    finally:
        await dispose_db(db_path)


async def test_start_fast_report_rejects_append_for_expired_report(fast_report_env):
    """Expired reports cannot be appended to; clients must start a fresh report."""
    from api.main import app
    from shared.database import dispose_db, get_session, init_db
    from shared.models import FastReport, Job, Repository

    db_path = fast_report_env
    await init_db(db_path)

    try:
        async with get_session(db_path) as s:
            s.add(Repository(id="r1", owner="o", name="n", status="ready"))
            s.add(Job(id="fr-expired", repo_id="r1", type="fast_report", status="done"))
            s.add(
                FastReport(
                    id="fr-expired",
                    repo_id="r1",
                    commit_sha="deadbeef",
                    status="done",
                    expires_at=datetime.now(UTC) - timedelta(seconds=1),
                )
            )
            await s.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/repos/r1/fast-reports",
                json={"question": "Follow-up", "report_id": "fr-expired"},
            )

        assert response.status_code == 410
        assert response.json()["detail"] == "Report expired"
    finally:
        await dispose_db(db_path)


async def test_get_fast_report_returns_410_for_expired_report(fast_report_env):
    """Expired reports are not reloadable through the persisted report endpoint."""
    from api.main import app
    from shared.database import dispose_db, get_session, init_db
    from shared.models import FastReport, Job, Repository

    db_path = fast_report_env
    await init_db(db_path)

    try:
        async with get_session(db_path) as s:
            s.add(Repository(id="r1", owner="o", name="n", status="ready"))
            s.add(Job(id="fr-expired", repo_id="r1", type="fast_report", status="done"))
            s.add(
                FastReport(
                    id="fr-expired",
                    repo_id="r1",
                    commit_sha="deadbeef",
                    status="done",
                    expires_at=datetime.now(UTC) - timedelta(seconds=1),
                )
            )
            await s.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/repos/r1/fast-reports/fr-expired")

        assert response.status_code == 410
        assert response.json()["detail"] == "Report expired"
    finally:
        await dispose_db(db_path)


async def test_ws_fast_report_streams_section_and_report_completion(fast_report_env):
    """Completed reports stream section_complete followed by report_complete."""
    from starlette.testclient import TestClient

    from api.main import app
    from shared.database import get_session, init_db
    from shared.models import FastReport, FastReportSection, Job, Repository

    db_path = fast_report_env
    await init_db(db_path)
    async with get_session(db_path) as s:
        s.add(Repository(id="r1", owner="o", name="n", status="ready"))
        s.add(Job(id="fr1", repo_id="r1", type="fast_report", status="done"))
        report = FastReport(
            id="fr1",
            repo_id="r1",
            commit_sha="deadbeef",
            status="done",
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        s.add(report)
        s.add(
            FastReportSection(
                id="sec1",
                report_id="fr1",
                query="How does indexing work?",
                title="Indexing Flow",
                summary="The job pipeline drives indexing.",
                markdown="# Indexing Flow",
                citations_json="[]",
                evidence_blocks_json="[]",
                related_wiki_pages_json="[]",
                related_diagrams_json="[]",
                status="done",
            )
        )
        await s.flush()
        report.active_section_id = "sec1"
        await s.commit()

    with TestClient(app) as client:
        with client.websocket_connect("/ws/repos/r1/fast-reports/fr1") as ws:
            section_msg = ws.receive_json()
            report_msg = ws.receive_json()

    assert section_msg["type"] == "section_complete"
    assert section_msg["section"]["id"] == "sec1"
    assert report_msg == {
        "type": "report_complete",
        "report_id": "fr1",
        "job_id": "fr1",
        "active_section_id": "sec1",
        "status": "done",
    }


async def test_ws_fast_report_emits_persisted_failure_detail(fast_report_env):
    """Failed reports emit the persisted job error instead of a generic message."""
    from starlette.testclient import TestClient

    from api.main import app
    from shared.database import get_session, init_db
    from shared.models import FastReport, FastReportSection, Job, Repository

    db_path = fast_report_env
    await init_db(db_path)
    async with get_session(db_path) as s:
        s.add(Repository(id="r1", owner="o", name="n", status="ready"))
        s.add(
            Job(
                id="fr-failed",
                repo_id="r1",
                type="fast_report",
                status="failed",
                error="Fast report retriever factory was not configured",
            )
        )
        s.add(
            FastReport(
                id="fr-failed",
                repo_id="r1",
                commit_sha="deadbeef",
                status="failed",
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        s.add(
            FastReportSection(
                id="sec-failed",
                report_id="fr-failed",
                query="Why did it fail?",
                title="Pending",
                summary=None,
                markdown="",
                citations_json="[]",
                evidence_blocks_json="[]",
                related_wiki_pages_json="[]",
                related_diagrams_json="[]",
                status="failed",
            )
        )
        await s.commit()

    with TestClient(app) as client:
        with client.websocket_connect("/ws/repos/r1/fast-reports/fr-failed") as ws:
            msg = ws.receive_json()

    assert msg == {
        "type": "error",
        "content": "Fast report retriever factory was not configured",
    }


async def test_ws_fast_report_404_closes_with_4004(fast_report_env):
    """Missing reports are rejected before websocket upgrade with code 4004."""
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from api.main import app
    from shared.database import init_db

    db_path = fast_report_env
    await init_db(db_path)

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/repos/r1/fast-reports/missing") as ws:
                ws.receive_json()

    assert exc_info.value.code == 4004
