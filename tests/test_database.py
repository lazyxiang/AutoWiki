import pytest

from shared.database import dispose_db, get_session, init_db
from shared.models import Job, Repository, WikiPage


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(str(db_path))
    return db_path


async def test_create_repository(db):
    async with get_session(str(db)) as session:
        repo = Repository(
            id="abc123",
            owner="testowner",
            name="testrepo",
            platform="github",
            status="pending",
        )
        session.add(repo)
        await session.commit()

    async with get_session(str(db)) as session:
        result = await session.get(Repository, "abc123")
        assert result.owner == "testowner"
        assert result.status == "pending"


async def test_create_job(db):
    async with get_session(str(db)) as session:
        repo = Repository(id="r1", owner="o", name="n", status="pending")
        job = Job(id="j1", repo_id="r1", type="full_index", status="queued", progress=0)
        session.add(repo)
        session.add(job)
        await session.commit()

    async with get_session(str(db)) as session:
        result = await session.get(Job, "j1")
        assert result.status == "queued"
        assert result.progress == 0


async def test_create_wiki_page(db):
    import uuid

    async with get_session(str(db)) as session:
        repo = Repository(id="r2", owner="o2", name="n2", status="pending")
        page = WikiPage(
            id=str(uuid.uuid4()),
            repo_id="r2",
            slug="overview",
            title="Overview",
            content="# Overview\n\nHello world.",
            page_order=0,
            parent_slug=None,
        )
        session.add(repo)
        session.add(page)
        await session.commit()

    async with get_session(str(db)) as session:
        from sqlalchemy import select

        result = await session.execute(select(WikiPage).where(WikiPage.repo_id == "r2"))
        page = result.scalar_one()
        assert page.slug == "overview"
        assert page.parent_slug is None
        assert page.page_order == 0


async def test_fast_report_persists_commit_sha_and_expiry(db):
    import json
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from shared.models import FastReport, FastReportSection

    expires_at = datetime.now(UTC) + timedelta(days=7)

    async with get_session(str(db)) as session:
        session.add(
            Repository(
                id="repo-fast",
                owner="owner",
                name="repo",
                platform="github",
                status="ready",
            )
        )
        session.add(
            FastReport(
                id="report-1",
                repo_id="repo-fast",
                commit_sha="deadbeef",
                expires_at=expires_at,
                status="done",
            )
        )
        session.add(
            FastReportSection(
                id="section-1",
                report_id="report-1",
                query="How does indexing work?",
                title="Indexing Flow",
                summary="Short summary",
                markdown="## Overview",
                citations_json="[]",
                evidence_blocks_json=json.dumps(
                    [{"citation_id": "cite-1", "snippet_start": 10}]
                ),
                related_wiki_pages_json="[]",
                related_diagrams_json="[]",
                status="done",
            )
        )
        await session.flush()
        report = await session.get(FastReport, "report-1")
        report.active_section_id = "section-1"
        await session.commit()

    async with get_session(str(db)) as session:
        report = await session.get(FastReport, "report-1")
        assert report is not None
        assert report.commit_sha == "deadbeef"
        assert report.expires_at == expires_at.replace(tzinfo=None)
        assert report.active_section_id == "section-1"
        result = await session.execute(
            select(FastReportSection).where(FastReportSection.report_id == "report-1")
        )
        section = result.scalar_one()
        assert section.query == "How does indexing work?"
        assert section.report_id == "report-1"
        assert json.loads(section.evidence_blocks_json)[0]["citation_id"] == "cite-1"


async def test_fast_report_active_section_requires_existing_section(db):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.exc import IntegrityError

    from shared.models import FastReport

    expires_at = datetime.now(UTC) + timedelta(days=7)

    async with get_session(str(db)) as session:
        session.add(
            Repository(
                id="repo-fast-fk",
                owner="owner",
                name="repo",
                platform="github",
                status="ready",
            )
        )
        session.add(
            FastReport(
                id="report-fk",
                repo_id="repo-fast-fk",
                commit_sha="cafebabe",
                expires_at=expires_at,
                status="done",
                active_section_id="missing-section",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_fast_report_active_section_fk_requires_existing_section(db):
    # active_section_id references fast_report_sections.id; setting it to a
    # non-existent section UUID must raise. Cross-report integrity is enforced
    # by application code (worker always creates sections with the same report_id).
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.exc import IntegrityError

    from shared.models import FastReport

    expires_at = datetime.now(UTC) + timedelta(days=7)

    async with get_session(str(db)) as session:
        session.add(
            Repository(
                id="repo-fast-same-report",
                owner="owner",
                name="repo",
                platform="github",
                status="ready",
            )
        )
        session.add(
            FastReport(
                id="report-a",
                repo_id="repo-fast-same-report",
                commit_sha="aaa111",
                expires_at=expires_at,
                status="done",
            )
        )
        await session.flush()

        report_a = await session.get(FastReport, "report-a")
        report_a.active_section_id = "nonexistent-section-uuid"

        with pytest.raises(IntegrityError):
            await session.commit()


async def test_platform_token_crud(tmp_path):
    from datetime import UTC, datetime

    from shared.database import get_session, init_db
    from shared.models import PlatformToken

    db = str(tmp_path / "t.db")
    await init_db(db)
    try:
        now = datetime.now(UTC)

        async with get_session(db) as s:
            s.add(
                PlatformToken(
                    platform="github",
                    token="ghp_test",
                    created_at=now,
                    updated_at=now,
                )
            )
            await s.commit()

        async with get_session(db) as s:
            row = await s.get(PlatformToken, "github")
            assert row is not None
            assert row.token == "ghp_test"
    finally:
        await dispose_db(db)


async def test_repository_has_is_private(tmp_path):
    from shared.database import get_session, init_db
    from shared.models import Repository

    db = str(tmp_path / "t2.db")
    await init_db(db)
    try:
        async with get_session(db) as s:
            s.add(
                Repository(
                    id="abc123",
                    owner="owner",
                    name="repo",
                    status="pending",
                    platform="github",
                    is_private=True,
                )
            )
            await s.commit()

        async with get_session(db) as s:
            repo = await s.get(Repository, "abc123")
            assert repo.is_private is True
    finally:
        await dispose_db(db)


async def test_init_db_applies_private_file_permissions(tmp_path):
    from shared.database import init_db

    db_path = tmp_path / "private" / "autowiki.db"
    db = str(db_path)
    await init_db(db)
    try:
        assert oct(db_path.parent.stat().st_mode & 0o777) == "0o700"
        assert oct(db_path.stat().st_mode & 0o777) == "0o600"
    finally:
        await dispose_db(db)


async def test_init_db_does_not_chmod_cwd_for_relative_database(tmp_path, monkeypatch):
    from pathlib import Path

    from shared.database import init_db

    monkeypatch.chdir(tmp_path)
    original_chmod = Path.chmod

    def forbid_cwd_chmod(self: Path, mode: int):
        if self == Path("."):
            raise AssertionError("init_db must not chmod the current directory")
        return original_chmod(self, mode)

    monkeypatch.setattr(Path, "chmod", forbid_cwd_chmod)

    db = "autowiki.db"
    await init_db(db)
    try:
        assert Path(db).exists()
    finally:
        await dispose_db(db)
