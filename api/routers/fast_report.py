"""REST + WebSocket endpoints for fast report generation and retrieval."""

from __future__ import annotations

import asyncio
import json as _json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import select

from api.queue import enqueue_fast_report as _enqueue_fast_report
from shared.config import get_config
from shared.database import get_session
from shared.models import FastReport, FastReportSection, Job, Repository
from worker.jobs import (
    FastReportIndexOutdated,
    _load_fast_report_index,
    _validate_fast_report_index_version,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class StartFastReportRequest(BaseModel):
    question: str
    report_id: str | None = None


async def _get_fast_report_job(session, repo_id: str, report_id: str) -> Job | None:
    job = await session.get(Job, report_id)
    if job is None or job.repo_id != repo_id or job.type != "fast_report":
        return None
    return job


def _section_payload(section: FastReportSection) -> dict:
    return {
        "id": section.id,
        "report_id": section.report_id,
        "query": section.query,
        "title": section.title,
        "summary": section.summary,
        "markdown": section.markdown,
        "citations": _json.loads(section.citations_json or "[]"),
        "evidence_blocks": _json.loads(section.evidence_blocks_json or "[]"),
        "related_wiki_pages": _json.loads(section.related_wiki_pages_json or "[]"),
        "related_diagrams": _json.loads(section.related_diagrams_json or "[]"),
        "analysis_trace": _json.loads(section.analysis_trace_json or "{}"),
        "created_at": section.created_at.isoformat(),
        "status": section.status,
    }


def _is_expired(report: FastReport) -> bool:
    expires_at = report.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)


def _expired_or_mismatched(report: FastReport, repo: Repository | None) -> bool:
    if _is_expired(report):
        return True
    return bool(
        repo is not None
        and repo.last_commit
        and report.commit_sha
        and report.commit_sha != repo.last_commit
    )


def _outdated_index_detail() -> dict[str, str]:
    return {
        "error": "fast_report_index_outdated",
        "message": (
            "Repository index is outdated for fast reports. "
            "Run `autowiki index <repo>` to upgrade."
        ),
        "actionable_command": "autowiki index <repo>",
    }


async def _ensure_fast_report_index_v2(repo_id: str) -> None:
    cfg = get_config()
    try:
        fast_report_index = await _load_fast_report_index(
            cfg.data_dir / "repos" / repo_id
        )
        _validate_fast_report_index_version(fast_report_index)
    except FastReportIndexOutdated as exc:
        raise HTTPException(
            status_code=409,
            detail=_outdated_index_detail(),
        ) from exc


@router.post("/api/repos/{repo_id}/fast-reports", status_code=202)
async def start_fast_report(repo_id: str, req: StartFastReportRequest) -> dict:
    if not req.question.strip():
        raise HTTPException(status_code=422, detail="Question is required")

    cfg = get_config()
    db_path = str(cfg.database_path)
    report_id = req.report_id

    async with get_session(db_path) as s:
        repo = await s.get(Repository, repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="Repository not found")
        if repo.status != "ready":
            raise HTTPException(
                status_code=409, detail="Repository is not ready for fast reports"
            )

        section_id = str(uuid.uuid4())
        if report_id:
            job_id = report_id
            report = await s.get(FastReport, report_id)
            if report is None or report.repo_id != repo_id:
                raise HTTPException(status_code=404, detail="Report not found")
            if _expired_or_mismatched(report, repo):
                raise HTTPException(status_code=410, detail="Report expired")
            job = await s.get(Job, job_id)
            if job is None or job.repo_id != repo_id or job.type != "fast_report":
                raise HTTPException(status_code=404, detail="Report job not found")
            await _ensure_fast_report_index_v2(repo_id)

            job.status = "queued"
            job.progress = 0
            job.error = None
            job.status_description = None
            job.finished_at = None
            report.status = "queued"
            report.expires_at = datetime.now(UTC) + timedelta(days=7)
        else:
            job_id = str(uuid.uuid4())
            report_id = job_id
            await _ensure_fast_report_index_v2(repo_id)
            s.add(
                Job(
                    id=job_id,
                    repo_id=repo_id,
                    type="fast_report",
                    status="queued",
                    progress=0,
                    created_at=datetime.now(UTC),
                )
            )
            s.add(
                FastReport(
                    id=report_id,
                    repo_id=repo_id,
                    commit_sha=repo.last_commit or "",
                    status="queued",
                    expires_at=datetime.now(UTC) + timedelta(days=7),
                )
            )
        s.add(
            FastReportSection(
                id=section_id,
                report_id=report_id,
                query=req.question.strip(),
                title="Pending",
                summary=None,
                markdown="",
                citations_json="[]",
                evidence_blocks_json="[]",
                related_wiki_pages_json="[]",
                related_diagrams_json="[]",
                status="queued",
            )
        )
        await s.commit()

    try:
        await _enqueue_fast_report(
            repo_id=repo_id,
            job_id=job_id,
            report_id=report_id,
            section_id=section_id,
            question=req.question.strip(),
        )
    except Exception as exc:
        async with get_session(db_path) as s:
            job = await s.get(Job, job_id)
            report = await s.get(FastReport, report_id)
            section = await s.get(FastReportSection, section_id)
            if job is not None:
                job.status = "failed"
                job.error = "Failed to enqueue fast report job"
            if report is not None:
                report.status = "failed"
            if section is not None:
                section.status = "failed"
            await s.commit()
        raise HTTPException(
            status_code=503, detail="Fast report queue unavailable"
        ) from exc

    return {
        "job_id": job_id,
        "report_id": report_id,
        "section_id": section_id,
        "status": "queued",
    }


@router.get("/api/repos/{repo_id}/fast-reports/{report_id}")
async def get_fast_report(repo_id: str, report_id: str) -> dict:
    cfg = get_config()
    db_path = str(cfg.database_path)
    async with get_session(db_path) as s:
        report = await s.get(FastReport, report_id)
        if report is None or report.repo_id != repo_id:
            raise HTTPException(status_code=404, detail="Report not found")
        repo = await s.get(Repository, report.repo_id)
        if _expired_or_mismatched(report, repo):
            raise HTTPException(status_code=410, detail="Report expired")
        job = await _get_fast_report_job(s, repo_id, report_id)

        result = await s.execute(
            select(FastReportSection)
            .where(FastReportSection.report_id == report_id)
            .order_by(FastReportSection.created_at.asc())
        )
        sections = result.scalars().all()

        return {
            "id": report.id,
            "repo_id": report.repo_id,
            "job_id": job.id if job is not None else None,
            "commit_sha": report.commit_sha,
            "status": report.status,
            "error": job.error if job is not None else None,
            "active_section_id": report.active_section_id,
            "created_at": report.created_at.isoformat(),
            "expires_at": report.expires_at.isoformat(),
            "sections": [_section_payload(section) for section in sections],
        }


_POLL_INTERVAL = 0.25


@router.websocket("/ws/repos/{repo_id}/fast-reports/{report_id}")
async def ws_fast_report(websocket: WebSocket, repo_id: str, report_id: str):
    cfg = get_config()
    db_path = str(cfg.database_path)

    async with get_session(db_path) as s:
        report = await s.get(FastReport, report_id)
        if report is None or report.repo_id != repo_id:
            await websocket.close(code=4004)
            return
        repo = await s.get(Repository, report.repo_id)
        if _expired_or_mismatched(report, repo):
            await websocket.close(code=4008)
            return

    await websocket.accept()

    sent_sections: set[str] = set()
    sent_analysis_updates: dict[str, str] = {}
    sent_report_complete = False
    try:
        while True:
            async with get_session(db_path) as s:
                report = await s.get(FastReport, report_id)
                if report is None or report.repo_id != repo_id:
                    await websocket.send_json(
                        {"type": "error", "content": "Report vanished"}
                    )
                    break
                job = await _get_fast_report_job(s, repo_id, report_id)

                result = await s.execute(
                    select(FastReportSection)
                    .where(FastReportSection.report_id == report_id)
                    .order_by(FastReportSection.created_at.asc())
                )
                sections = result.scalars().all()

            for section in sections:
                if section.status == "running":
                    raw_trace = section.analysis_trace_json or "{}"
                    trace = _json.loads(raw_trace)
                    if trace and sent_analysis_updates.get(section.id) != raw_trace:
                        await websocket.send_json(
                            {
                                "type": "analysis_update",
                                "section_id": section.id,
                                "analysis_trace": trace,
                            }
                        )
                        sent_analysis_updates[section.id] = raw_trace
                elif section.status == "done" and section.id not in sent_sections:
                    await websocket.send_json(
                        {
                            "type": "section_complete",
                            "section": _section_payload(section),
                        }
                    )
                    sent_sections.add(section.id)

            if report.status == "failed":
                error_content = (
                    job.error if job is not None and job.error else "Fast report failed"
                )
                if isinstance(error_content, str) and (
                    "fast_report_index_outdated" in error_content
                ):
                    error_content = _outdated_index_detail()
                await websocket.send_json(
                    {
                        "type": "error",
                        "content": error_content,
                    }
                )
                break

            if report.status == "done" and not sent_report_complete:
                await websocket.send_json(
                    {
                        "type": "report_complete",
                        "report_id": report.id,
                        "job_id": job.id if job is not None else None,
                        "active_section_id": report.active_section_id,
                        "status": report.status,
                    }
                )
                sent_report_complete = True
                break

            await asyncio.sleep(_POLL_INTERVAL)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Unhandled error in ws_fast_report for report %s", report_id)
        try:
            await websocket.send_json(
                {"type": "error", "content": "Internal server error"}
            )
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
