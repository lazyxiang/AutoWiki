from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from api.queue import enqueue_full_index
from shared.config import get_config
from shared.database import get_session
from shared.models import Job, Repository
from worker.pipeline.ingestion import parse_github_url

router = APIRouter(prefix="/api/repos")


class IndexRequest(BaseModel):
    url: str
    force: bool = False


@router.post("", status_code=202)
async def submit_repo(req: IndexRequest):
    try:
        owner, name = parse_github_url(req.url)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid GitHub URL")

    cfg = get_config()
    db_path = str(cfg.database_path)
    repo_id = hashlib.sha256(f"github:{owner}/{name}".encode()).hexdigest()[:16]
    job_id = str(uuid.uuid4())

    async with get_session(db_path) as s:
        in_flight = await s.scalar(
            select(Job.id)
            .where(
                Job.repo_id == repo_id,
                Job.type == "full_index",
                Job.status.in_(("queued", "running")),
            )
            .limit(1)
        )
        if in_flight is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "A full index job is already queued or running for this repository"
                ),
            )
        existing = await s.get(Repository, repo_id)
        if existing is None:
            repo = Repository(
                id=repo_id, owner=owner, name=name, status="pending", platform="github"
            )
            s.add(repo)
        job = Job(
            id=job_id,
            repo_id=repo_id,
            type="full_index",
            status="queued",
            progress=0,
            status_description="Queued for processing...",
        )
        s.add(job)
        await s.commit()

    await enqueue_full_index(repo_id, job_id, owner, name, force=req.force)
    return {"repo_id": repo_id, "job_id": job_id, "status": "queued"}


@router.get("")
async def list_repos():
    cfg = get_config()
    async with get_session(str(cfg.database_path)) as s:
        result = await s.execute(select(Repository))
        repos = result.scalars().all()
    return {
        "repos": [
            {"id": r.id, "owner": r.owner, "name": r.name, "status": r.status}
            for r in repos
        ]
    }


@router.get("/{repo_id}")
async def get_repo(repo_id: str):
    cfg = get_config()
    async with get_session(str(cfg.database_path)) as s:
        repo = await s.get(Repository, repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="Repository not found")
        return {
            "id": repo.id,
            "owner": repo.owner,
            "name": repo.name,
            "status": repo.status,
            "indexed_at": repo.indexed_at,
        }
