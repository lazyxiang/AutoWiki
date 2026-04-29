"""ARQ job support for Fast Report generation.

This module owns the Fast Report background job and its repository-index
retrievers.  ``worker.jobs`` re-exports the public symbols so existing queued
job names and imports remain stable while this file keeps the fast-report
surface separate from full-index and refresh orchestration.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select as sa_select

from shared.config import get_config
from shared.database import get_session, init_db
from shared.fast_report_types import FastReportCitation, FastReportWikiLink
from shared.models import FastReport, FastReportSection, Job, Repository, WikiPage
from worker.fast_report import (
    CodeEvidenceLayer,
    CuratedKnowledgeLayer,
    FastReportQuestionIntent,
    RepositoryStructureLayer,
    generate_fast_report_section,
)
from worker.fast_report.search import retrieve_code_evidence
from worker.llm import make_llm_provider
from worker.pipeline.ingestion import extract_readme

logger = logging.getLogger("worker.task")


async def _update_job(db_path: str, job_id: str, **kwargs) -> None:
    """Update one or more columns on a ``Job`` row in the database."""
    async with get_session(db_path) as s:
        job = await s.get(Job, job_id)
        for k, v in kwargs.items():
            setattr(job, k, v)
        await s.commit()


async def _update_fast_report(db_path: str, report_id: str, **kwargs) -> None:
    async with get_session(db_path) as s:
        report = await s.get(FastReport, report_id)
        if report is None:
            raise RuntimeError(f"FastReport {report_id!r} not found")
        for k, v in kwargs.items():
            setattr(report, k, v)
        await s.commit()


async def _update_fast_report_section(db_path: str, section_id: str, **kwargs) -> None:
    async with get_session(db_path) as s:
        section = await s.get(FastReportSection, section_id)
        if section is None:
            raise RuntimeError(f"FastReportSection {section_id!r} not found")
        for k, v in kwargs.items():
            setattr(section, k, v)
        await s.commit()


def _missing_fast_report_retriever(name: str):
    async def _raiser(*args, **kwargs):
        raise RuntimeError(f"Fast report retriever {name!r} is not configured")

    return _raiser


FastReportRetrieverFactory = Callable[..., Awaitable[dict[str, Callable]]]


async def _load_fast_report_wiki_pages(
    db_path: str, repo_id: str
) -> list[tuple[str, str, str]]:
    async with get_session(db_path) as s:
        result = await s.execute(
            sa_select(WikiPage)
            .where(WikiPage.repo_id == repo_id)
            .order_by(WikiPage.page_order, WikiPage.title)
        )
        return [
            (page.slug, page.title, page.content or "")
            for page in result.scalars().all()
        ]


async def _load_fast_report_index(repo_data_dir: Path) -> dict:
    index_path = repo_data_dir / "ast" / "fast_report_index.json"
    if not index_path.exists():
        return {}
    loop = asyncio.get_running_loop()
    try:
        raw = await loop.run_in_executor(None, index_path.read_text)
        loaded = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning(
            "Ignoring unreadable fast report index at %s: %s",
            index_path,
            exc,
        )
        return {}
    if not isinstance(loaded, dict):
        logger.warning(
            "Ignoring unexpected fast report index payload at %s: %s",
            index_path,
            type(loaded).__name__,
        )
        return {}
    return loaded


class FastReportIndexOutdated(RuntimeError):
    """Raised when fast_report_index.json is missing or below v2."""


def _validate_fast_report_index_version(index: dict) -> None:
    version = index.get("index_version")
    if not isinstance(version, int) or version < 2:
        raise FastReportIndexOutdated(
            "fast_report_index_outdated: Repository index is outdated for fast "
            "reports. Run `autowiki index <repo>` to upgrade."
        )


async def _build_default_fast_report_retrievers(
    *,
    repo_id: str,
    db_path: str,
    cfg,
) -> dict[str, Callable]:
    repo_data_dir = cfg.data_dir / "repos" / repo_id
    clone_root = repo_data_dir / "clone"
    loop = asyncio.get_running_loop()
    readme = await loop.run_in_executor(None, extract_readme, clone_root)
    fast_report_index = await _load_fast_report_index(repo_data_dir)
    _validate_fast_report_index_version(fast_report_index)
    wiki_pages = await _load_fast_report_wiki_pages(db_path, repo_id)
    directory_tree = str(fast_report_index.get("directory_tree") or "").rstrip()
    hub_modules = list(fast_report_index.get("hub_modules") or [])
    readme_headings = list(fast_report_index.get("readme_headings") or [])
    readme_first_paragraph = _readme_first_paragraph(readme)

    async def _repository_structure(
        question: str, intent: FastReportQuestionIntent
    ) -> RepositoryStructureLayer:
        signals: list[str] = []
        if directory_tree:
            signals.append(f"Directory tree:\n{directory_tree}")
        if readme_headings:
            signals.append(f"README headings: {', '.join(readme_headings[:12])}")
        if readme_first_paragraph:
            signals.append(f"README first paragraph: {readme_first_paragraph}")
        if hub_modules:
            hub_lines = "\n".join(
                f"  - {hub.get('path')} - {hub.get('purpose') or ''}".rstrip(" -")
                for hub in hub_modules
                if hub.get("path")
            )
            if hub_lines:
                signals.append(f"Hub modules:\n{hub_lines}")
        if not signals:
            signals.append("Repository structure unavailable.")
        if readme:
            return RepositoryStructureLayer(
                signals=signals,
                citations=[
                    FastReportCitation(
                        id="struct-1",
                        file_path="README.md",
                        start_line=1,
                        end_line=1,
                        label="README",
                        kind="repository_structure",
                    )
                ],
            )
        return RepositoryStructureLayer(signals=signals, citations=[])

    async def _code_evidence(
        question: str, intent: FastReportQuestionIntent
    ) -> CodeEvidenceLayer:
        return await loop.run_in_executor(
            None,
            lambda: retrieve_code_evidence(
                fast_report_index, intent, question, clone_root=clone_root
            ),
        )

    async def _curated(
        question: str, intent: FastReportQuestionIntent
    ) -> CuratedKnowledgeLayer:
        lowered = question.lower()
        ranked_pages = sorted(
            wiki_pages,
            key=lambda page: (
                lowered not in f"{page[1]} {page[2]}".lower(),
                len(page[2]),
                page[1],
            ),
        )[:3]
        return CuratedKnowledgeLayer(
            summaries=[page[2][:400] for page in ranked_pages if page[2]],
            wiki_pages=[
                FastReportWikiLink(
                    slug=slug,
                    title=title,
                    reason="Related generated wiki page",
                )
                for slug, title, _content in ranked_pages
            ],
            diagrams=[],
        )

    return {
        "repository_structure_retriever": _repository_structure,
        "code_evidence_retriever": _code_evidence,
        "curated_knowledge_retriever": _curated,
        "fast_report_index": fast_report_index,
    }


def _readme_first_paragraph(readme: str | None) -> str:
    if not readme:
        return ""
    paragraph_lines: list[str] = []
    for line in readme.splitlines():
        stripped = line.strip()
        if not stripped:
            if paragraph_lines:
                break
            continue
        paragraph_lines.append(stripped)
    return " ".join(paragraph_lines)[:400]


async def run_fast_report(
    ctx: dict,
    repo_id: str,
    job_id: str,
    report_id: str,
    section_id: str,
    question: str,
) -> None:
    """ARQ job: generate and persist one fast report section."""
    cfg = get_config()
    db_path = str(cfg.database_path)
    await init_db(db_path)

    try:
        await _update_job(
            db_path,
            job_id,
            status="running",
            progress=10,
            status_description="Generating fast report...",
        )
        await _update_fast_report(db_path, report_id, status="running")
        await _update_fast_report_section(db_path, section_id, status="running")

        async with get_session(db_path) as s:
            repo = await s.get(Repository, repo_id)
            repo_name = repo.name if repo is not None else repo_id

        llm = make_llm_provider(cfg)
        retrievers = await ctx.get(
            "fast_report_retriever_factory",
            _build_default_fast_report_retrievers,
        )(
            repo_id=repo_id,
            db_path=db_path,
            cfg=cfg,
        )
        result = await generate_fast_report_section(
            question=question,
            repo_name=repo_name,
            llm=llm,
            repository_structure_retriever=retrievers.get(
                "repository_structure_retriever",
                _missing_fast_report_retriever("fast_report_repository_structure"),
            ),
            code_evidence_retriever=retrievers.get(
                "code_evidence_retriever",
                _missing_fast_report_retriever("fast_report_code_evidence"),
            ),
            curated_knowledge_retriever=retrievers.get(
                "curated_knowledge_retriever",
                _missing_fast_report_retriever("fast_report_curated_knowledge"),
            ),
            fast_report_index=retrievers.get("fast_report_index"),
        )

        analysis_trace = {"events": result.analysis_events}
        # Keep the section running for one poll so WebSocket clients can emit
        # analysis_update before section_complete.
        await _update_fast_report_section(
            db_path,
            section_id,
            analysis_trace_json=json.dumps(analysis_trace),
            status="running",
        )
        await asyncio.sleep(0.5)
        await _update_fast_report_section(
            db_path,
            section_id,
            title=result.title,
            summary=result.summary,
            markdown=result.markdown,
            citations_json=json.dumps([asdict(item) for item in result.citations]),
            evidence_blocks_json=json.dumps(
                [asdict(item) for item in result.evidence_blocks]
            ),
            related_wiki_pages_json=json.dumps(
                [asdict(item) for item in result.related_wiki_pages]
            ),
            related_diagrams_json=json.dumps(
                [asdict(item) for item in result.related_diagrams]
            ),
            status="done",
        )
        await _update_fast_report(
            db_path,
            report_id,
            status="done",
            active_section_id=section_id,
        )
        await _update_job(
            db_path,
            job_id,
            status="done",
            progress=100,
            finished_at=datetime.now(UTC),
            status_description="Fast report complete",
        )
    except Exception as e:
        logger.exception("Fast report job failed: %s", e)
        try:
            await _update_fast_report(db_path, report_id, status="failed")
        except Exception:
            logger.exception("Could not mark report failed: %s", report_id)
        try:
            await _update_fast_report_section(db_path, section_id, status="failed")
        except Exception:
            logger.exception("Could not mark section failed: %s", section_id)
        try:
            await _update_job(
                db_path,
                job_id,
                status="failed",
                error=str(e),
                finished_at=datetime.now(UTC),
                status_description=f"Error: {e}",
            )
        except Exception:
            logger.exception("Failed to mark job as failed (job_id=%s)", job_id)
        raise
