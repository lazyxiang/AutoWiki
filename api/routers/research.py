"""REST + WebSocket endpoints for Deep Research."""

from __future__ import annotations

import asyncio
import json as _json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import select

from api.queue import enqueue_deep_research as _enqueue_deep_research
from shared.config import get_config
from shared.database import get_session
from shared.models import Job, Repository, ResearchReport

logger = logging.getLogger(__name__)
router = APIRouter()


class StartResearchRequest(BaseModel):
    question: str


@router.post("/api/repos/{repo_id}/research", status_code=202)
async def start_research(repo_id: str, req: StartResearchRequest) -> dict:
    if not req.question.strip():
        raise HTTPException(status_code=422, detail="Question is required")

    cfg = get_config()
    db_path = str(cfg.database_path)

    async with get_session(db_path) as s:
        repo = await s.get(Repository, repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="Repository not found")
        if repo.status != "ready":
            raise HTTPException(
                status_code=409, detail="Repository is not ready for research"
            )

        job_id = str(uuid.uuid4())
        report_id = str(uuid.uuid4())
        s.add(
            Job(
                id=job_id,
                repo_id=repo_id,
                type="research",
                status="queued",
                progress=0,
                created_at=datetime.now(UTC),
            )
        )
        s.add(
            ResearchReport(
                id=report_id,
                repo_id=repo_id,
                job_id=job_id,
                question=req.question.strip(),
                status="queued",
                created_at=datetime.now(UTC),
            )
        )
        await s.commit()

    try:
        await _enqueue_deep_research(
            repo_id=repo_id,
            job_id=job_id,
            report_id=report_id,
            question=req.question.strip(),
        )
    except Exception as exc:
        async with get_session(db_path) as s:
            job = await s.get(Job, job_id)
            report = await s.get(ResearchReport, report_id)
            if job is not None:
                job.status = "failed"
                job.error = "Failed to enqueue research job"
            if report is not None:
                report.status = "failed"
                report.error = "Failed to enqueue research job"
            await s.commit()
        raise HTTPException(
            status_code=503, detail="Research queue unavailable"
        ) from exc

    return {"job_id": job_id, "report_id": report_id, "status": "queued"}


@router.get("/api/repos/{repo_id}/research/{job_id}")
async def get_research(repo_id: str, job_id: str) -> dict:
    cfg = get_config()
    db_path = str(cfg.database_path)
    async with get_session(db_path) as s:
        result = await s.execute(
            select(ResearchReport).where(
                ResearchReport.repo_id == repo_id,
                ResearchReport.job_id == job_id,
            )
        )
        report = result.scalar_one_or_none()
        if report is None:
            raise HTTPException(status_code=404, detail="Report not found")
        return {
            "id": report.id,
            "repo_id": report.repo_id,
            "job_id": report.job_id,
            "question": report.question,
            "plan": _json.loads(report.plan_json or "[]"),
            "findings": _json.loads(report.findings_json or "[]"),
            "report": report.report_markdown,
            "status": report.status,
            "error": report.error,
        }


_POLL_INTERVAL = 0.25  # seconds


@router.websocket("/ws/repos/{repo_id}/research/{job_id}")
async def ws_research(websocket: WebSocket, repo_id: str, job_id: str):
    """Stream Deep Research progress events over WebSocket.

    Protocol (server → client JSON messages):
        {"type": "plan", "plan": [...]}
        {"type": "step_start", "step_index": i, "query": "..."}
        {"type": "step_finding", "step_index": i, "answer": "...", "sources": [...]}
        {"type": "report", "content": "# markdown"}
        {"type": "done"}
        {"type": "error", "content": "..."}
    """
    cfg = get_config()
    db_path = str(cfg.database_path)

    async with get_session(db_path) as s:
        result = await s.execute(
            select(ResearchReport).where(
                ResearchReport.repo_id == repo_id,
                ResearchReport.job_id == job_id,
            )
        )
        report = result.scalar_one_or_none()
        if report is None:
            await websocket.close(code=4004)
            return

    await websocket.accept()

    sent_plan = False
    sent_finding_indices: set[int] = set()
    sent_report = False
    try:
        while True:
            async with get_session(db_path) as s:
                result = await s.execute(
                    select(ResearchReport).where(
                        ResearchReport.repo_id == repo_id,
                        ResearchReport.job_id == job_id,
                    )
                )
                report = result.scalar_one_or_none()
                if report is None:
                    await websocket.send_json(
                        {"type": "error", "content": "Report vanished"}
                    )
                    break

                plan = _json.loads(report.plan_json or "[]")
                findings = _json.loads(report.findings_json or "[]")

            if plan and not sent_plan:
                await websocket.send_json({"type": "plan", "plan": plan})
                sent_plan = True

            for f in findings:
                idx = f.get("step_index", -1)
                if idx not in sent_finding_indices:
                    await websocket.send_json(
                        {
                            "type": "step_start",
                            "step_index": idx,
                            "query": plan[idx]["query"] if idx < len(plan) else "",
                        }
                    )
                    await websocket.send_json(
                        {
                            "type": "step_finding",
                            "step_index": idx,
                            "answer": f.get("answer", ""),
                            "sources": f.get("sources", []),
                        }
                    )
                    sent_finding_indices.add(idx)

            if report.report_markdown and not sent_report:
                await websocket.send_json(
                    {"type": "report", "content": report.report_markdown}
                )
                sent_report = True

            if report.status == "failed":
                await websocket.send_json(
                    {"type": "error", "content": report.error or "Research failed"}
                )
                break
            if report.status == "done" and sent_report:
                await websocket.send_json({"type": "done"})
                break

            await asyncio.sleep(_POLL_INTERVAL)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Unhandled error in ws_research for job %s", job_id)
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
