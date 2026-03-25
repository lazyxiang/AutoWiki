from __future__ import annotations

import hashlib
import json as _json
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from api.queue import enqueue_full_index, enqueue_refresh_index
from shared.config import get_config
from shared.database import get_session
from shared.models import Job, Repository
from worker.pipeline.ingestion import parse_github_url

router = APIRouter(prefix="/api/repos")


class IndexRequest(BaseModel):
    url: str


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
        existing = await s.get(Repository, repo_id)
        if existing is None:
            repo = Repository(
                id=repo_id, owner=owner, name=name, status="pending", platform="github"
            )
            s.add(repo)
        job = Job(
            id=job_id, repo_id=repo_id, type="full_index", status="queued", progress=0
        )
        s.add(job)
        await s.commit()

    await enqueue_full_index(repo_id, job_id, owner, name)
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


@router.post("/{repo_id}/refresh", status_code=202)
async def refresh_repo(repo_id: str):
    cfg = get_config()
    db_path = str(cfg.database_path)
    async with get_session(db_path) as s:
        repo = await s.get(Repository, repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="Repository not found")
        if repo.status not in ("ready", "error"):
            raise HTTPException(status_code=409, detail="Repository is not in a refreshable state")
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, repo_id=repo_id, type="refresh", status="queued", progress=0)
        s.add(job)
        repo.status = "indexing"
        await s.commit()
    await enqueue_refresh_index(repo_id, job_id, repo.owner, repo.name)
    return {"repo_id": repo_id, "job_id": job_id, "status": "queued"}


@router.get("/{repo_id}/graph")
async def get_repo_graph(repo_id: str):
    cfg = get_config()
    module_tree_path = cfg.data_dir / "repos" / repo_id / "ast" / "module_tree.json"
    if not module_tree_path.exists():
        raise HTTPException(status_code=404, detail="Graph not available — run index first")
    module_tree = _json.loads(module_tree_path.read_text())
    nodes = [
        {"id": m["path"], "label": m["path"], "file_count": len(m.get("files", []))}
        for m in module_tree
    ]
    return {"nodes": nodes, "edges": []}
