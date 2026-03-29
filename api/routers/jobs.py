"""REST endpoint for polling ARQ job progress.

Provides a single GET endpoint that returns a snapshot of a job's current
state.  Clients that need real-time streaming should use the WebSocket
endpoint at ``/ws/jobs/{job_id}`` instead (see :mod:`api.ws.jobs`).
"""

from fastapi import APIRouter, HTTPException

from shared.config import get_config
from shared.database import get_session
from shared.models import Job

router = APIRouter(prefix="/api/jobs")


@router.get("/{job_id}")
async def get_job(job_id: str):
    """Retrieve the current state of an indexing or refresh job.

    Looks up the ``Job`` row by primary key and returns all observable fields.
    This endpoint is suitable for one-shot polling; for a push-based progress
    stream use the WebSocket endpoint ``WS /ws/jobs/{job_id}``.

    **Status lifecycle**: ``queued`` → ``running`` → ``done`` | ``failed``.
    A job in ``running`` state may also have ``retrying`` semantics visible
    via ``status_description`` (see :func:`api.ws.jobs.ws_job_progress`).

    Args:
        job_id (str): UUID string of the job, as returned by
            :func:`api.routers.repos.submit_repo` or
            :func:`api.routers.repos.refresh_repo`.

    Returns:
        dict: A JSON object:

        .. code-block:: json

            {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "repo_id": "a1b2c3d4e5f6a7b8",
                "type": "full_index",
                "status": "running",
                "progress": 42,
                "status_description": "Generating wiki pages...",
                "error": null
            }

        ``type`` is one of ``"full_index"`` or ``"refresh"``.
        ``status`` is one of ``"queued"``, ``"running"``, ``"done"``,
        ``"failed"``.
        ``progress`` is an integer in the range 0–100.
        ``error`` is a human-readable message string or ``null``.

    Raises:
        HTTPException: 404 if no job with the given ``job_id`` exists.

    Example:
        .. code-block:: http

            GET /api/jobs/550e8400-e29b-41d4-a716-446655440000 HTTP/1.1

        Response (200 OK):

        .. code-block:: json

            {"id": "550e8400-e29b-41d4-a716-446655440000",
             "repo_id": "a1b2c3d4e5f6a7b8", "type": "full_index",
             "status": "done", "progress": 100,
             "status_description": "Wiki generated successfully",
             "error": null}
    """
    cfg = get_config()
    async with get_session(str(cfg.database_path)) as s:
        job = await s.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return {
            "id": job.id,
            "repo_id": job.repo_id,
            "type": job.type,
            "status": job.status,
            "progress": job.progress,
            "status_description": job.status_description,
            "error": job.error,
        }
