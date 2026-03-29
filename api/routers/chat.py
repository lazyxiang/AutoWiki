"""REST and WebSocket endpoints for the Q&A chat interface.

Provides three endpoints:

* ``POST /api/repos/{repo_id}/chat`` — create a new chat session.
* ``GET  /api/repos/{repo_id}/chat/{session_id}`` — retrieve message history.
* ``WS   /ws/repos/{repo_id}/chat/{session_id}`` — stream LLM responses.

The WebSocket endpoint uses RAG (Retrieval-Augmented Generation) to ground
responses in the repository's indexed content.  The FAISS vector store is
loaded once per WebSocket connection and reused across multiple messages within
the same session.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from shared.config import get_config
from shared.database import get_session
from shared.models import ChatSession, Repository
from worker.chat import create_chat_session as _create_session
from worker.chat import generate_chat_response, get_chat_history, save_message
from worker.embedding import make_embedding_provider
from worker.llm import make_llm_provider
from worker.pipeline.rag_indexer import FAISSStore

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/repos/{repo_id}/chat", status_code=201)
async def create_chat_session(repo_id: str):
    """Create a new chat session for a repository.

    Verifies that the repository exists, then delegates to
    :func:`worker.chat.create_chat_session` which inserts a new
    ``ChatSession`` row into SQLite and returns its UUID.

    Args:
        repo_id (str): The 16-character hex repository identifier, injected
            from the URL path.

    Returns:
        dict: A JSON object:

        .. code-block:: json

            {"session_id": "7f3e9a1b-4c2d-4e5f-8a6b-1c2d3e4f5a6b"}

    Raises:
        HTTPException: 404 if no repository with the given ``repo_id`` exists.

    Example:
        .. code-block:: http

            POST /api/repos/a1b2c3d4e5f6a7b8/chat HTTP/1.1

        Response (201 Created):

        .. code-block:: json

            {"session_id": "7f3e9a1b-4c2d-4e5f-8a6b-1c2d3e4f5a6b"}
    """
    cfg = get_config()
    db_path = str(cfg.database_path)
    async with get_session(db_path) as s:
        repo = await s.get(Repository, repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="Repository not found")
    session_id = await _create_session(repo_id, db_path)
    return {"session_id": session_id}


@router.get("/api/repos/{repo_id}/chat/{session_id}")
async def get_session_history(repo_id: str, session_id: str):
    """Retrieve the message history for an existing chat session.

    Validates that the session belongs to the specified repository (guards
    against cross-repository access), then returns messages up to
    ``cfg.chat.history_window * 2`` entries (one entry per role per turn).

    Args:
        repo_id (str): The 16-character hex repository identifier.
        session_id (str): UUID of the chat session, as returned by
            :func:`create_chat_session`.

    Returns:
        dict: A JSON object:

        .. code-block:: json

            {
                "session_id": "7f3e9a1b-4c2d-4e5f-8a6b-1c2d3e4f5a6b",
                "messages": [
                    {"role": "user", "content": "What does this repo do?"},
                    {"role": "assistant", "content": "This repo ..."}
                ]
            }

        ``role`` is either ``"user"`` or ``"assistant"``.  Messages are ordered
        oldest-first.

    Raises:
        HTTPException: 404 if the session does not exist or does not belong to
            the given ``repo_id``.

    Example:
        .. code-block:: http

            GET /api/repos/a1b2c3/chat/7f3e9a1b HTTP/1.1

        Response (200 OK):

        .. code-block:: json

            {"session_id": "7f3e9a1b", "messages": []}
    """
    cfg = get_config()
    db_path = str(cfg.database_path)
    async with get_session(db_path) as s:
        session = await s.get(ChatSession, session_id)
        # Guard against cross-repo access: session_id is globally unique but
        # must still belong to the repo_id in the URL.
        if session is None or session.repo_id != repo_id:
            raise HTTPException(status_code=404, detail="Session not found")
    history = await get_chat_history(
        session_id, db_path, limit=cfg.chat.history_window * 2
    )
    return {"session_id": session_id, "messages": history}


@router.websocket("/ws/repos/{repo_id}/chat/{session_id}")
async def ws_chat(websocket: WebSocket, repo_id: str, session_id: str):
    """Stream LLM chat responses over a WebSocket connection.

    **Protocol**:

    1. The server validates the session *before* accepting the WebSocket.  If
       the session is invalid the connection is closed with code ``4004``
       without an upgrade.

    2. After a successful upgrade, the server loads the FAISS vector store for
       the repository once (outside the message loop) so subsequent messages
       in the same session share the loaded index.

    3. For each message the client sends::

           Client → Server:  {"content": "What does the ingestion module do?"}

       The server persists the user message, performs RAG retrieval, streams
       the LLM response as a sequence of chunk frames, and closes the turn with
       a done frame::

           Server → Client:  {"type": "chunk", "content": "The ingestion ..."}
           Server → Client:  {"type": "chunk", "content": "module handles ..."}
           Server → Client:  {"type": "done"}

    4. If an unhandled error occurs the server sends an error frame before
       closing::

           Server → Client:  {"type": "error", "content": "Internal server error"}

    5. A normal client disconnect (``WebSocketDisconnect``) is handled
       silently.  The ``finally`` block always closes the WebSocket.

    Args:
        websocket (WebSocket): The FastAPI WebSocket connection object.
        repo_id (str): The 16-character hex repository identifier.
        session_id (str): UUID of the chat session.
    """
    cfg = get_config()
    db_path = str(cfg.database_path)
    data_dir = cfg.data_dir

    # Validate session exists before accepting
    async with get_session(db_path) as s:
        session = await s.get(ChatSession, session_id)
        if session is None or session.repo_id != repo_id:
            # Reject before upgrade — close with an application-level error
            # code so the client can distinguish auth failures from network
            # errors.
            await websocket.close(code=4004)
            return

    await websocket.accept()

    try:
        # Load store once outside the message loop
        repo_data_dir = data_dir / "repos" / repo_id
        embedding = make_embedding_provider(cfg)
        store = FAISSStore(
            dimension=embedding.dimension,
            index_path=repo_data_dir / "faiss.index",
            meta_path=repo_data_dir / "faiss.meta.pkl",
        )
        # FAISS I/O is synchronous; run it in the default thread pool executor
        # to avoid blocking the asyncio event loop.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, store.load)
        llm = make_llm_provider(cfg)

        while True:
            data = await websocket.receive_json()
            user_message = data.get("content", "").strip()
            # Ignore empty or whitespace-only messages rather than sending them
            # to the LLM.
            if not user_message:
                continue

            await save_message(session_id, "user", user_message, db_path)

            history = await get_chat_history(
                session_id, db_path, limit=cfg.chat.history_window * 2
            )
            history = history[:-1]  # exclude the user message just inserted

            response_chunks: list[str] = []
            async for chunk in generate_chat_response(
                user_message, history, store, llm, embedding
            ):
                response_chunks.append(chunk)
                await websocket.send_json({"type": "chunk", "content": chunk})

            # Persist the complete assistant response as a single DB row.
            full_response = "".join(response_chunks)
            await save_message(session_id, "assistant", full_response, db_path)
            # Signal to the client that the turn is complete so it can re-enable
            # the input and display the finalised message.
            await websocket.send_json({"type": "done"})

    except WebSocketDisconnect:
        pass  # Client disconnected normally
    except Exception:
        logger.exception("Unhandled error in ws_chat for session %s", session_id)
        try:
            await websocket.send_json(
                {"type": "error", "content": "Internal server error"}
            )
        except Exception:
            pass  # Socket may already be closed
    finally:
        await websocket.close()
