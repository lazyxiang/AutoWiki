import pytest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch


async def test_create_chat_session(tmp_path):
    from worker.chat import create_chat_session
    from shared.database import init_db, dispose_db, get_session
    from shared.models import ChatSession

    db_path = str(tmp_path / "test.db")
    from shared.models import Repository
    await init_db(db_path)
    async with get_session(db_path) as s:
        s.add(Repository(id="r1", owner="o", name="n", status="ready"))
        await s.commit()

    session_id = await create_chat_session("r1", db_path)
    assert session_id

    try:
        async with get_session(db_path) as s:
            sess = await s.get(ChatSession, session_id)
            assert sess is not None
            assert sess.repo_id == "r1"
    finally:
        await dispose_db(db_path)


async def test_get_chat_history_ordered(tmp_path):
    from worker.chat import create_chat_session, save_message, get_chat_history
    from shared.database import init_db, dispose_db, get_session
    from shared.models import Repository

    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    async with get_session(db_path) as s:
        s.add(Repository(id="r2", owner="o", name="n", status="ready"))
        await s.commit()

    try:
        session_id = await create_chat_session("r2", db_path)
        await save_message(session_id, "user", "hello", db_path)
        await save_message(session_id, "assistant", "hi there", db_path)

        history = await get_chat_history(session_id, db_path, limit=10)
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"
    finally:
        await dispose_db(db_path)


async def test_generate_chat_response_streams(mock_llm, mock_embedding):
    from worker.chat import generate_chat_response
    from unittest.mock import MagicMock

    async def _fake_stream(*args, **kwargs):
        for chunk in ["Hello", " world"]:
            yield chunk

    mock_llm.generate_stream = _fake_stream

    store = MagicMock()
    store.search.return_value = [{"file": "main.py", "text": "def foo(): pass"}]

    chunks = []
    async for chunk in generate_chat_response(
        user_message="What does foo do?",
        history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        store=store,
        llm=mock_llm,
        embedding=mock_embedding,
    ):
        chunks.append(chunk)
    assert "".join(chunks) == "Hello world"
