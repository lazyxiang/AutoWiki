from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    import asyncio

    from shared.database import dispose_db, init_db
    from shared.models import Repository

    db_path = str(tmp_path / "test.db")

    async def _setup():
        from shared.database import get_session

        await init_db(db_path)
        async with get_session(db_path) as s:
            s.add(Repository(id="r1", owner="owner", name="repo", status="ready"))
            await s.commit()

    asyncio.run(_setup())

    with (
        patch("shared.config._config", None),
        patch("api.routers.chat.get_config") as mock_cfg,
    ):
        mock_cfg.return_value.database_path = tmp_path / "test.db"
        mock_cfg.return_value.data_dir = tmp_path
        mock_cfg.return_value.chat.history_window = 10
        from api.main import app

        yield TestClient(app)

    asyncio.run(dispose_db(db_path))


def test_create_chat_session(client):
    resp = client.post("/api/repos/r1/chat")
    assert resp.status_code == 201
    body = resp.json()
    assert "session_id" in body


def test_get_chat_history_empty(client):
    resp = client.post("/api/repos/r1/chat")
    session_id = resp.json()["session_id"]
    resp2 = client.get(f"/api/repos/r1/chat/{session_id}")
    assert resp2.status_code == 200
    assert resp2.json()["messages"] == []


def test_create_chat_session_missing_repo(client):
    resp = client.post("/api/repos/nonexistent/chat")
    assert resp.status_code == 404


def test_ws_chat_disconnect_safe(client):
    """Verify that ws_chat doesn't raise RuntimeError on client disconnect."""
    from unittest.mock import MagicMock

    # We need to mock the dependencies used in ws_chat
    with (
        patch("api.routers.chat.FAISSStore") as mock_store_cls,
        patch("api.routers.chat.make_embedding_provider"),
        patch("api.routers.chat.make_llm_provider"),
        patch("api.routers.chat.generate_chat_response"),
    ):
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store

        # Create a session first
        resp = client.post("/api/repos/r1/chat")
        session_id = resp.json()["session_id"]

        with client.websocket_connect(f"/ws/repos/r1/chat/{session_id}") as ws:
            ws.send_json({"content": "hello"})
            # Closing the client-side triggers WebSocketDisconnect on the server.
            # The fix ensures the server's finally block doesn't crash.
            ws.close()


def test_ws_chat_invalid_session_closes_with_4004(client):
    """WebSocket closes with code 4004 when the session does not exist."""
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/repos/r1/chat/nonexistent") as ws:
            ws.receive_json()
    assert exc_info.value.code == 4004
