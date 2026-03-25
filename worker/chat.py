from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

from sqlalchemy import select

from shared.database import get_session
from shared.models import ChatSession, ChatMessage
from worker.llm.base import LLMProvider
from worker.embedding.base import EmbeddingProvider
from worker.pipeline.rag_indexer import FAISSStore

_SYSTEM = """You are a technical documentation assistant for a software repository.
Answer questions precisely using the provided source code context.
Reference specific file names and function names in your answers.
Always cite the source files you draw information from."""


async def create_chat_session(repo_id: str, db_path: str) -> str:
    session_id = str(uuid.uuid4())
    async with get_session(db_path) as s:
        s.add(ChatSession(id=session_id, repo_id=repo_id,
                          created_at=datetime.now(timezone.utc)))
        await s.commit()
    return session_id


async def get_chat_history(session_id: str, db_path: str, limit: int = 20) -> list[dict]:
    """Return up to `limit` messages for a session, oldest first."""
    async with get_session(db_path) as s:
        result = await s.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        messages = result.scalars().all()
    return [{"role": m.role, "content": m.content} for m in reversed(messages)]


async def save_message(session_id: str, role: str, content: str, db_path: str) -> None:
    async with get_session(db_path) as s:
        s.add(ChatMessage(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role=role,
            content=content,
            created_at=datetime.now(timezone.utc),
        ))
        await s.commit()


async def generate_chat_response(
    user_message: str,
    history: list[dict],
    store: FAISSStore,
    llm: LLMProvider,
    embedding: EmbeddingProvider,
    top_k: int = 5,
) -> AsyncIterator[str]:
    """Stream an LLM response grounded in RAG-retrieved code chunks and conversation history."""
    query_vec = await embedding.embed(user_message)
    chunks = store.search(query_vec, k=top_k)

    context = "\n\n---\n\n".join(
        f"File: {c.get('file', 'unknown')}\n{c.get('text', '')}"
        for c in chunks
    )
    history_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in history
    )

    prompt = (
        f"Conversation history:\n{history_text}\n\n"
        f"Relevant source code:\n{context}\n\n"
        f"USER: {user_message}\n\n"
        "Answer based on the source code context. Cite file names where relevant."
    )

    async for chunk in llm.generate_stream(prompt, system=_SYSTEM):
        yield chunk
