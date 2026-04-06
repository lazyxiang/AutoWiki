"""REST endpoints for repos: submit, list, status, refresh, and wiki-plan graph.

All routes are mounted under the ``/api/repos`` prefix.  Clients interact with
these endpoints to submit GitHub repositories for indexing, poll their status,
trigger incremental refreshes, and retrieve the dependency graph derived from
the wiki plan.
"""

from __future__ import annotations

import hashlib
import json as _json
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update

from api.queue import enqueue_full_index, enqueue_refresh_index
from shared.config import get_config
from shared.database import get_session
from shared.models import Job, Repository
from worker.pipeline.ingestion import parse_github_url

router = APIRouter(prefix="/api/repos")


class IndexRequest(BaseModel):
    """Request body for submitting a repository for indexing.

    Attributes:
        url (str): Full GitHub repository URL in any commonly accepted form,
            e.g. ``"https://github.com/owner/repo"`` or
            ``"github.com/owner/repo"``.  The URL is parsed by
            :func:`worker.pipeline.ingestion.parse_github_url` which accepts
            both ``https://`` and bare ``github.com/`` prefixes.
        wiki_language (str): ISO-639-1 language code for the generated wiki
            content.  Defaults to ``"en"`` (English).  Use ``"zh"`` to
            generate the wiki in Chinese (简体中文).
    """

    url: str
    wiki_language: str = "en"


@router.post("", status_code=202)
async def submit_repo(req: IndexRequest):
    """Submit a GitHub repository for full wiki generation.

    Parses the supplied URL, derives a deterministic ``repo_id`` from the
    ``github:{owner}/{name}`` string (first 16 hex chars of SHA-256), and
    inserts a new ``Job`` row with status ``"queued"``.

    **Idempotency**: if the repository has been submitted before the existing
    ``Repository`` row is reused — only the new ``Job`` is inserted.  This
    means each call always triggers a fresh index run.

    **Stuck-job cancellation**: any ``full_index`` jobs for this repo that are
    still in state ``"queued"`` or ``"running"`` (e.g. timed out by ARQ or
    orphaned after a worker restart) are marked ``"failed"`` before the new job
    is enqueued, so progress polling does not stall on a ghost job.

    Args:
        req (IndexRequest): Request body containing the GitHub URL.

    Returns:
        dict: A JSON object with the following keys:

        .. code-block:: json

            {
                "repo_id": "a1b2c3d4e5f6a7b8",
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "status": "queued"
            }

    Raises:
        HTTPException: 422 if ``req.url`` cannot be parsed as a valid GitHub
            repository URL.
        Exception: Any Redis or database error is propagated after marking the
            job as ``"failed"`` in SQLite.

    Example:
        .. code-block:: http

            POST /api/repos HTTP/1.1
            Content-Type: application/json

            {"url": "https://github.com/octocat/hello-world"}

        Response (202 Accepted):

        .. code-block:: json

            {
                "repo_id": "a1b2c3d4e5f6a7b8",
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "status": "queued"
            }
    """
    try:
        owner, name = parse_github_url(req.url)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid GitHub URL")

    cfg = get_config()
    db_path = str(cfg.database_path)
    # Derive a stable, short identifier from the canonical repo key so the same
    # repo always maps to the same storage directory regardless of how the URL
    # was typed.
    repo_id = hashlib.sha256(f"github:{owner}/{name}".encode()).hexdigest()[:16]
    job_id = str(uuid.uuid4())

    async with get_session(db_path) as s:
        # Cancel any stuck jobs (e.g. timed out by ARQ) so re-submission always works
        await s.execute(
            update(Job)
            .where(
                Job.repo_id == repo_id,
                Job.type == "full_index",
                Job.status.in_(("queued", "running")),
            )
            .values(status="failed", error="Superseded by a new indexing request")
        )
        existing = await s.get(Repository, repo_id)
        if existing is None:
            # First-time submission: create the repository metadata row.
            repo = Repository(
                id=repo_id,
                owner=owner,
                name=name,
                status="pending",
                platform="github",
                wiki_language=req.wiki_language,
            )
            s.add(repo)
        else:
            # Re-submission: update the desired wiki language.
            existing.wiki_language = req.wiki_language
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

    try:
        await enqueue_full_index(
            repo_id, job_id, owner, name, wiki_language=req.wiki_language
        )
    except Exception:
        # If Redis is unavailable, mark the job failed immediately so the
        # client is not left waiting indefinitely.
        async with get_session(db_path) as s:
            job = await s.get(Job, job_id)
            if job is not None:
                job.status = "failed"
                job.error = "Failed to enqueue full_index job"
                await s.commit()
        raise
    return {"repo_id": repo_id, "job_id": job_id, "status": "queued"}


@router.get("")
async def list_repos():
    """Return a summary list of all known repositories.

    Queries the ``repositories`` table and returns lightweight metadata for
    each row — enough for a dashboard view without loading wiki content.

    Returns:
        dict: A JSON object with a single ``"repos"`` key:

        .. code-block:: json

            {
                "repos": [
                    {
                        "id": "a1b2c3d4e5f6a7b8",
                        "owner": "octocat",
                        "name": "hello-world",
                        "status": "ready"
                    }
                ]
            }

        Possible ``status`` values: ``"pending"``, ``"indexing"``,
        ``"ready"``, ``"error"``.

    Example:
        .. code-block:: http

            GET /api/repos HTTP/1.1

        Response (200 OK):

        .. code-block:: json

            {"repos": [{"id": "a1b2c3d4", "owner": "acme", "name": "core",
                        "status": "ready"}]}
    """
    cfg = get_config()
    async with get_session(str(cfg.database_path)) as s:
        result = await s.execute(select(Repository))
        repos = result.scalars().all()
    return {
        "repos": [
            {
                "id": r.id,
                "owner": r.owner,
                "name": r.name,
                "description": r.description or "",
                "stars": r.stars or 0,
                "language": r.language or "",
                "status": r.status,
                "default_branch": r.default_branch or "main",
                "indexed_at": r.indexed_at.isoformat() if r.indexed_at else None,
                "wiki_language": r.wiki_language or "en",
            }
            for r in repos
        ]
    }


@router.get("/{repo_id}")
async def get_repo(repo_id: str):
    """Retrieve detailed status and metadata for a single repository.

    Args:
        repo_id (str): The 16-character hex repository identifier returned by
            :func:`submit_repo`.

    Returns:
        dict: A JSON object:

        .. code-block:: json

            {
                "id": "a1b2c3d4e5f6a7b8",
                "owner": "octocat",
                "name": "hello-world",
                "status": "ready",
                "indexed_at": "2024-01-15T12:34:56+00:00"
            }

        ``indexed_at`` is an ISO-8601 UTC timestamp or ``null`` if the
        repository has not yet been indexed successfully.

    Raises:
        HTTPException: 404 if no repository with the given ``repo_id`` exists.

    Example:
        .. code-block:: http

            GET /api/repos/a1b2c3d4e5f6a7b8 HTTP/1.1

        Response (200 OK):

        .. code-block:: json

            {"id": "a1b2c3d4e5f6a7b8", "owner": "octocat",
             "name": "hello-world", "status": "ready",
             "indexed_at": "2024-01-15T12:34:56+00:00"}
    """
    cfg = get_config()
    async with get_session(str(cfg.database_path)) as s:
        repo = await s.get(Repository, repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="Repository not found")
        return {
            "id": repo.id,
            "owner": repo.owner,
            "name": repo.name,
            "description": repo.description or "",
            "stars": repo.stars or 0,
            "language": repo.language or "",
            "status": repo.status,
            "default_branch": repo.default_branch or "main",
            "indexed_at": repo.indexed_at.isoformat() if repo.indexed_at else None,
            "wiki_language": repo.wiki_language or "en",
        }


@router.post("/{repo_id}/refresh", status_code=202)
async def refresh_repo(repo_id: str):
    """Trigger an incremental refresh of an already-indexed repository.

    Validates that the repository exists and is in a state where a refresh
    makes sense (``"ready"`` or ``"error"``), then creates a new ``Job`` row
    of type ``"refresh"``, updates the repository status to ``"indexing"``,
    and enqueues the worker task.

    The worker will fetch new commits, diff against the last indexed SHA, and
    regenerate only the wiki pages whose source files changed.

    Args:
        repo_id (str): The 16-character hex repository identifier.

    Returns:
        dict: A JSON object:

        .. code-block:: json

            {
                "repo_id": "a1b2c3d4e5f6a7b8",
                "job_id": "660e9500-f30c-52e5-b827-557766551111",
                "status": "queued"
            }

    Raises:
        HTTPException: 404 if the repository does not exist.
        HTTPException: 409 if the repository status is not ``"ready"`` or
            ``"error"`` (e.g. it is currently ``"indexing"`` or
            ``"pending"``).

    Example:
        .. code-block:: http

            POST /api/repos/a1b2c3d4e5f6a7b8/refresh HTTP/1.1

        Response (202 Accepted):

        .. code-block:: json

            {"repo_id": "a1b2c3d4e5f6a7b8",
             "job_id": "660e9500-f30c-52e5-b827-557766551111",
             "status": "queued"}
    """
    cfg = get_config()
    db_path = str(cfg.database_path)
    async with get_session(db_path) as s:
        repo = await s.get(Repository, repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="Repository not found")
        # Only allow refreshes when the previous index completed (or errored);
        # reject mid-flight requests to avoid parallel pipeline runs.
        if repo.status not in ("ready", "error"):
            raise HTTPException(
                status_code=409, detail="Repository is not in a refreshable state"
            )
        owner, name = repo.owner, repo.name
        wiki_language = repo.wiki_language or "en"
        job_id = str(uuid.uuid4())
        job = Job(
            id=job_id, repo_id=repo_id, type="refresh", status="queued", progress=0
        )
        s.add(job)
        # Immediately flip to "indexing" so subsequent GET /repos/{id} reflects
        # that work is in progress.
        repo.status = "indexing"
        await s.commit()
    await enqueue_refresh_index(
        repo_id, job_id, owner, name, wiki_language=wiki_language
    )
    return {"repo_id": repo_id, "job_id": job_id, "status": "queued"}


@router.get("/{repo_id}/graph")
async def get_repo_graph(repo_id: str):
    """Return the wiki-plan dependency graph for a repository.

    Reads the internal ``ast/wiki_plan.json`` file produced by the Wiki Planner
    pipeline stage and converts it into a nodes-and-edges structure suitable
    for graph visualisation libraries (e.g. React Flow, D3).

    Each **node** represents a logical wiki page; each **edge** represents a
    parent–child relationship in the page hierarchy.

    Args:
        repo_id (str): The 16-character hex repository identifier.

    Returns:
        dict: A JSON object:

        .. code-block:: json

            {
                "nodes": [
                    {"id": "Overview", "label": "Overview", "file_count": 3},
                    {"id": "API Layer", "label": "API Layer", "file_count": 5}
                ],
                "edges": [
                    {"source": "Overview", "target": "API Layer"}
                ]
            }

        ``file_count`` is the number of source files assigned to that wiki
        page by the planner.  ``edges`` only contain entries for pages that
        have a ``parent`` field set in the plan.

    Raises:
        HTTPException: 404 if the repository does not exist.
        HTTPException: 404 if ``ast/wiki_plan.json`` has not been generated yet
            (i.e. the index pipeline has not completed at least the Wiki Planner
            stage).

    Example:
        .. code-block:: http

            GET /api/repos/a1b2c3d4e5f6a7b8/graph HTTP/1.1

        Response (200 OK):

        .. code-block:: json

            {"nodes": [{"id": "Overview", "label": "Overview",
                        "file_count": 2}],
             "edges": []}
    """
    cfg = get_config()
    async with get_session(str(cfg.database_path)) as s:
        repo = await s.get(Repository, repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="Repository not found")
    # The wiki plan lives under the repo's ast/ subdirectory and is written by
    # the Wiki Planner pipeline stage after AST analysis is complete.
    wiki_plan_path = cfg.data_dir / "repos" / repo_id / "ast" / "wiki_plan.json"
    if not wiki_plan_path.exists():
        raise HTTPException(
            status_code=404, detail="Graph not available — run index first"
        )
    wiki_plan = _json.loads(wiki_plan_path.read_text())
    nodes = [
        {
            "id": p["title"],
            "label": p["title"],
            # file_count may be 0 for top-level summary pages with no direct
            # file assignments.
            "file_count": len(p.get("files", [])),
        }
        for p in wiki_plan.get("pages", [])
    ]
    edges = [
        {"source": p["parent"], "target": p["title"]}
        for p in wiki_plan.get("pages", [])
        if p.get("parent")
    ]
    return {"nodes": nodes, "edges": edges}
