from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Repository(Base):
    __tablename__ = "repositories"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    stars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    platform: Mapped[str] = mapped_column(String, default="github")
    last_commit: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    wiki_path: Mapped[str | None] = mapped_column(String, nullable=True)
    wiki_structure: Mapped[str | None] = mapped_column(Text, nullable=True)
    jobs: Mapped[list[Job]] = relationship("Job", back_populates="repository")
    pages: Mapped[list[WikiPage]] = relationship(
        "WikiPage", back_populates="repository"
    )


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    status_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    repository: Mapped[Repository] = relationship("Repository", back_populates="jobs")


class WikiPage(Base):
    __tablename__ = "wiki_pages"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_order: Mapped[int] = mapped_column(Integer, default=0)
    parent_slug: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )
    repository: Mapped[Repository] = relationship("Repository", back_populates="pages")


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )
    messages: Mapped[list[ChatMessage]] = relationship(
        "ChatMessage", back_populates="session"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )
    session: Mapped[ChatSession] = relationship(
        "ChatSession", back_populates="messages"
    )
