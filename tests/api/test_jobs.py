async def test_get_job_not_found(client):
    resp = await client.get("/api/jobs/unknown-job-id")
    assert resp.status_code == 404


async def test_get_job_found(client):
    from shared.config import get_config
    from shared.database import get_session
    from shared.models import Job, Repository

    cfg = get_config()
    async with get_session(str(cfg.database_path)) as s:
        repo = Repository(
            id="r2", owner="a", name="b", status="ready", platform="github"
        )
        job = Job(
            id="job-found", repo_id="r2", type="full_index", status="queued", progress=0
        )
        s.add(repo)
        s.add(job)
        await s.commit()
    resp = await client.get("/api/jobs/job-found")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "job-found"
    assert body["status"] == "queued"
    assert body["progress"] == 0


async def test_ws_job_progress(tmp_path):
    import os

    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["AUTOWIKI_DATA_DIR"] = str(tmp_path)
    from shared.config import reset_config

    reset_config()
    from shared.database import dispose_db, init_db

    await init_db(str(tmp_path / "test.db"))

    from shared.database import get_session
    from shared.models import Job, Repository

    async with get_session(str(tmp_path / "test.db")) as s:
        repo = Repository(
            id="r1", owner="a", name="b", status="ready", platform="github"
        )
        job = Job(id="j1", repo_id="r1", type="full_index", status="done", progress=100)
        s.add(repo)
        s.add(job)
        await s.commit()

    from starlette.testclient import TestClient

    from api.main import app

    with TestClient(app) as tc:
        with tc.websocket_connect("/ws/jobs/j1") as ws:
            data = ws.receive_json()
            assert data["progress"] == 100
            assert data["status"] == "done"

    await dispose_db(str(tmp_path / "test.db"))
    reset_config()
