from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
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
    wiki_language: Mapped[str | None] = mapped_column(String, nullable=True)
    platform: Mapped[str] = mapped_column(String, default="github")
    last_commit: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    default_branch: Mapped[str | None] = mapped_column(String, nullable=True)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    wiki_path: Mapped[str | None] = mapped_column(String, nullable=True)
    wiki_structure: Mapped[str | None] = mapped_column(Text, nullable=True)
    jobs: Mapped[list[Job]] = relationship("Job", back_populates="repository")
    pages: Mapped[list[WikiPage]] = relationship(
        "WikiPage", back_populates="repository"
    )
    fast_reports: Mapped[list[FastReport]] = relationship(
        "FastReport", back_populates="repository"
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


class ResearchReport(Base):
    __tablename__ = "research_reports"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    plan_json: Mapped[str | None] = mapped_column(Text, nullable=True, default="[]")
    findings_json: Mapped[str | None] = mapped_column(Text, nullable=True, default="[]")
    report_markdown: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    # status: queued|running|done|failed
    status: Mapped[str] = mapped_column(String, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    repository: Mapped[Repository] = relationship("Repository")
    job: Mapped[Job] = relationship("Job")


class FastReport(Base):
    __tablename__ = "fast_reports"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    active_section_id: Mapped[str | None] = mapped_column(
        ForeignKey("fast_report_sections.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    repository: Mapped[Repository] = relationship(
        "Repository", back_populates="fast_reports"
    )
    sections: Mapped[list[FastReportSection]] = relationship(
        "FastReportSection",
        back_populates="report",
        foreign_keys="FastReportSection.report_id",
    )
    active_section: Mapped[FastReportSection | None] = relationship(
        "FastReportSection",
        foreign_keys=[active_section_id],
        post_update=True,
    )


class FastReportSection(Base):
    __tablename__ = "fast_report_sections"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    report_id: Mapped[str] = mapped_column(
        ForeignKey("fast_reports.id"), nullable=False
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    citations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    evidence_blocks_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )
    related_wiki_pages_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )
    related_diagrams_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    report: Mapped[FastReport] = relationship(
        "FastReport",
        back_populates="sections",
        foreign_keys=[report_id],
    )


class PlatformToken(Base):
    __tablename__ = "platform_tokens"
    platform: Mapped[str] = mapped_column(String, primary_key=True)
    # PATs are stored locally in SQLite; init_db locks the data dir to 0700
    # and the database file to 0600, and API responses only expose masked values.
    token: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
