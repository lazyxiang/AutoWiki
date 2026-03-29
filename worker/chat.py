"""Chat session management and RAG-grounded Q&A streaming.

This module provides the building blocks for the conversational Q&A feature:
- Creating and persisting chat sessions in SQLite.
- Loading chat history (oldest-first, with a configurable window limit).
- Saving individual assistant/user messages.
- Streaming LLM responses that are grounded in RAG-retrieved source-code
  chunks and the current conversation history.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import select

from shared.database import get_session
from shared.models import ChatMessage, ChatSession
from worker.embedding.base import EmbeddingProvider
from worker.llm.base import LLMProvider
from worker.pipeline.rag_indexer import FAISSStore

_SYSTEM = """You are a technical documentation assistant for a software repository.
Answer questions precisely using the provided source code context.
Reference specific file names and function names in your answers.
Always cite the source files you draw information from."""


async def create_chat_session(repo_id: str, db_path: str) -> str:
    """Create a new chat session for a repository and persist it to SQLite.

    A UUID is generated client-side so the caller knows the session ID
    without an extra round-trip to the database.

    Args:
        repo_id (str): The repository identifier (primary key in the
            ``repositories`` table) to associate this session with.
        db_path (str): Filesystem path to the SQLite database file.

    Returns:
        str: A newly generated UUID string that uniquely identifies this
            chat session.

    Example:
        >>> session_id = await create_chat_session("abc123", "/data/autowiki.db")
        >>> print(session_id)  # e.g. "550e8400-e29b-41d4-a716-446655440000"
    """
    session_id = str(uuid.uuid4())
    async with get_session(db_path) as s:
        s.add(ChatSession(id=session_id, repo_id=repo_id, created_at=datetime.now(UTC)))
        await s.commit()
    return session_id


async def get_chat_history(
    session_id: str, db_path: str, limit: int = 20
) -> list[dict]:
    """Return up to ``limit`` messages for a session, ordered oldest-first.

    The query fetches the *most recent* ``limit`` messages in descending
    order (to apply the LIMIT efficiently), then reverses the list so
    callers receive messages in chronological order.

    Args:
        session_id (str): UUID of the chat session to retrieve history for.
        db_path (str): Filesystem path to the SQLite database file.
        limit (int): Maximum number of messages to return.  Defaults to 20.
            Older messages beyond this window are discarded so the LLM
            prompt stays within a reasonable token budget.

    Returns:
        list[dict]: List of message dicts, each with keys:
            - ``"role"`` (str): ``"user"`` or ``"assistant"``.
            - ``"content"`` (str): The message text.
            Messages are ordered oldest-first (ascending ``created_at``).

    Example:
        >>> history = await get_chat_history("550e8400...", "/data/autowiki.db")
        >>> for msg in history:
        ...     print(msg["role"], ":", msg["content"][:60])
    """
    async with get_session(db_path) as s:
        result = await s.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        messages = result.scalars().all()
    # Reverse so the list is oldest-first (the query sorted newest-first for LIMIT)
    return [{"role": m.role, "content": m.content} for m in reversed(messages)]


async def save_message(session_id: str, role: str, content: str, db_path: str) -> None:
    """Persist a single chat message to the ``chat_messages`` table.

    Args:
        session_id (str): UUID of the owning chat session.
        role (str): Message author — either ``"user"`` or ``"assistant"``.
        content (str): Full text of the message.
        db_path (str): Filesystem path to the SQLite database file.

    Returns:
        None

    Example:
        >>> await save_message(
        ...     "550e8400...", "user", "What does run_full_index do?",
        ...     "/data/autowiki.db",
        ... )
    """
    async with get_session(db_path) as s:
        s.add(
            ChatMessage(
                id=str(uuid.uuid4()),
                session_id=session_id,
                role=role,
                content=content,
                created_at=datetime.now(UTC),
            )
        )
        await s.commit()


async def generate_chat_response(
    user_message: str,
    history: list[dict],
    store: FAISSStore,
    llm: LLMProvider,
    embedding: EmbeddingProvider,
    top_k: int = 5,
) -> AsyncIterator[str]:
    """Stream an LLM response grounded in RAG-retrieved code chunks and history.

    Context assembly:
        1. The user message is embedded and used to search the FAISS vector
           index for the ``top_k`` most relevant source-code chunks.
        2. Retrieved chunks are formatted as ``File: <path>\\n<text>`` blocks
           separated by horizontal rules.
        3. The conversation history is serialised as ``ROLE: content`` lines.
        4. A single prompt combining history, retrieved context, and the
           user message is sent to the LLM via ``generate_stream``.

    Args:
        user_message (str): The latest message from the user.
        history (list[dict]): Prior conversation turns, each a dict with
            ``"role"`` (str) and ``"content"`` (str) keys, ordered
            oldest-first (as returned by ``get_chat_history``).
        store (FAISSStore): Loaded FAISS vector store for the repository.
        llm (LLMProvider): Configured LLM provider instance used for
            streaming text generation.
        embedding (EmbeddingProvider): Configured embedding provider used
            to vectorise ``user_message`` for the similarity search.
        top_k (int): Number of source-code chunks to retrieve from FAISS.
            Defaults to 5.

    Returns:
        AsyncIterator[str]: An async generator that yields string tokens as
            the LLM produces them.  Callers should consume with
            ``async for chunk in generate_chat_response(...)``.

    Example:
        >>> async for token in generate_chat_response(
        ...     "Explain run_full_index", history, store, llm, embedding
        ... ):
        ...     print(token, end="", flush=True)
    """
    # Embed the user query to find semantically similar source-code chunks
    query_vec = await embedding.embed(user_message)
    chunks = store.search(query_vec, k=top_k)

    # Format retrieved chunks as labelled blocks so the LLM can cite file names
    context = "\n\n---\n\n".join(
        f"File: {c.get('file', 'unknown')}\n{c.get('text', '')}" for c in chunks
    )
    # Flatten conversation history into a plain-text exchange transcript
    history_text = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in history)

    prompt = (
        f"Conversation history:\n{history_text}\n\n"
        f"Relevant source code:\n{context}\n\n"
        f"USER: {user_message}\n\n"
        "Answer based on the source code context. Cite file names where relevant."
    )

    async for chunk in llm.generate_stream(prompt, system=_SYSTEM):
        yield chunk
