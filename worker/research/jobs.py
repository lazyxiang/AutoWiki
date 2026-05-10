"""ARQ job support for Deep Research."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from shared.config import get_config
from shared.database import get_session, init_db
from shared.models import Job, Repository, ResearchReport
from worker.embedding import make_embedding_provider
from worker.embedding.faiss_store import FAISSStore
from worker.llm import make_llm_provider
from worker.pipeline.ingestion import extract_readme
from worker.research.service import run_deep_research_flow

logger = logging.getLogger("worker.task")


async def _update_job(db_path: str, job_id: str, **kwargs) -> None:
    """Update one or more columns on a ``Job`` row in the database."""
    async with get_session(db_path) as s:
        job = await s.get(Job, job_id)
        for k, v in kwargs.items():
            setattr(job, k, v)
        await s.commit()


def _make_faiss_store(repo_data_dir: Path, embedding) -> FAISSStore:
    return FAISSStore(
        dimension=embedding.dimension,
        index_path=repo_data_dir / "faiss.index",
        meta_path=repo_data_dir / "faiss.meta.pkl",
    )


async def _load_faiss_for_research(repo_data_dir: Path, embedding) -> FAISSStore:
    """Load the FAISS store for a repo, running the blocking IO in an executor."""
    store = _make_faiss_store(repo_data_dir, embedding)
    await asyncio.get_running_loop().run_in_executor(None, store.load)
    return store


async def run_deep_research(
    ctx: dict,
    repo_id: str,
    job_id: str,
    report_id: str,
    question: str,
) -> None:
    """ARQ job: run the Deep Research flow and persist the result."""
    cfg = get_config()
    db_path = str(cfg.database_path)
    data_dir = cfg.data_dir
    await init_db(db_path)

    async def _update_report(**kwargs):
        async with get_session(db_path) as s:
            report = await s.get(ResearchReport, report_id)
            if report is None:
                raise RuntimeError(f"ResearchReport {report_id!r} not found")
            for k, v in kwargs.items():
                setattr(report, k, v)
            await s.commit()

    try:
        await _update_job(db_path, job_id, status="running", progress=5)
        await _update_report(status="running")

        repo_data_dir = data_dir / "repos" / repo_id
        clone_root = repo_data_dir / "clone"

        loop = asyncio.get_running_loop()
        readme = await loop.run_in_executor(None, extract_readme, clone_root)

        embedding = make_embedding_provider(cfg)
        llm = make_llm_provider(cfg)
        store = await _load_faiss_for_research(repo_data_dir, embedding)

        async with get_session(db_path) as s:
            repo = await s.get(Repository, repo_id)
            repo_name = repo.name if repo is not None else repo_id

        async def _on_event(event: dict) -> None:
            if event["type"] == "plan":
                await _update_report(plan_json=json.dumps(event["plan"]))
                await _update_job(db_path, job_id, progress=20)
            elif event["type"] == "step_start":
                await _update_job(
                    db_path,
                    job_id,
                    status_description=(
                        f"Investigating step {event['step_index'] + 1}"
                    ),
                )
            elif event["type"] == "step_finding":
                async with get_session(db_path) as s:
                    rep = await s.get(ResearchReport, report_id)
                    findings = json.loads(rep.findings_json or "[]")
                    findings.append(
                        {
                            "step_index": event["step_index"],
                            "answer": event["answer"],
                            "sources": event["sources"],
                        }
                    )
                    rep.findings_json = json.dumps(findings)
                    await s.commit()
            elif event["type"] == "report":
                await _update_report(report_markdown=event["content"])

        result = await run_deep_research_flow(
            question=question,
            repo_name=repo_name,
            readme=readme,
            store=store,
            llm=llm,
            embedding=embedding,
            on_event=_on_event,
        )

        now = datetime.now(UTC)
        await _update_report(
            status="done",
            finished_at=now,
            plan_json=json.dumps([asdict(s) for s in result.plan]),
            findings_json=json.dumps([asdict(f) for f in result.findings]),
            report_markdown=result.report,
        )
        await _update_job(
            db_path,
            job_id,
            status="done",
            progress=100,
            finished_at=now,
            status_description="Research complete",
        )
    except Exception as e:
        logger.exception("Deep research job failed: %s", e)
        now = datetime.now(UTC)
        await _update_report(status="failed", error=str(e), finished_at=now)
        await _update_job(
            db_path,
            job_id,
            status="failed",
            error=str(e),
            finished_at=now,
            status_description=f"Error: {e}",
        )
        raise
