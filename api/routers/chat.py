from __future__ import annotations

from fastapi import APIRouter, HTTPException, WebSocket

from shared.database import get_session
from shared.models import ChatMessage, ChatSession, Repository
from shared.config import get_config
from worker.chat import (
    create_chat_session as _create_session,
    get_chat_history,
    save_message,
    generate_chat_response,
)
from worker.pipeline.rag_indexer import FAISSStore
from worker.llm import make_llm_provider
from worker.embedding import make_embedding_provider

router = APIRouter()


@router.post("/api/repos/{repo_id}/chat", status_code=201)
async def create_chat_session(repo_id: str):
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
    cfg = get_config()
    db_path = str(cfg.database_path)
    history = await get_chat_history(session_id, db_path, limit=cfg.chat.history_window * 2)
    return {"session_id": session_id, "messages": history}


@router.websocket("/ws/repos/{repo_id}/chat/{session_id}")
async def ws_chat(websocket: WebSocket, repo_id: str, session_id: str):
    cfg = get_config()
    db_path = str(cfg.database_path)
    data_dir = cfg.data_dir

    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            user_message = data.get("content", "").strip()
            if not user_message:
                continue

            await save_message(session_id, "user", user_message, db_path)

            history = await get_chat_history(
                session_id, db_path, limit=cfg.chat.history_window * 2
            )
            history = history[:-1]  # exclude the user message just inserted

            repo_data_dir = data_dir / "repos" / repo_id
            embedding = make_embedding_provider(cfg)
            store = FAISSStore(
                dimension=embedding.dimension,
                index_path=repo_data_dir / "faiss.index",
                meta_path=repo_data_dir / "faiss.meta.pkl",
            )
            store.load()

            llm = make_llm_provider(cfg)
            response_chunks: list[str] = []
            async for chunk in generate_chat_response(
                user_message, history, store, llm, embedding
            ):
                response_chunks.append(chunk)
                await websocket.send_json({"type": "chunk", "content": chunk})

            full_response = "".join(response_chunks)
            await save_message(session_id, "assistant", full_response, db_path)
            await websocket.send_json({"type": "done"})

    except Exception as e:
        await websocket.send_json({"type": "error", "content": str(e)})
    finally:
        await websocket.close()
