"""WebSocket endpoint for real-time job progress streaming.

Clients connect to ``WS /ws/jobs/{job_id}`` immediately after submitting a
repository for indexing.  The server polls the SQLite database every second
and pushes progress updates until the job reaches a terminal state
(``"done"`` or ``"failed"``).
"""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from shared.config import get_config
from shared.database import get_session
from shared.models import Job

router = APIRouter()


@router.websocket("/ws/jobs/{job_id}")
async def ws_job_progress(websocket: WebSocket, job_id: str):
    """Stream job progress updates to a connected WebSocket client.

    After accepting the connection, the handler enters a polling loop that
    queries the ``jobs`` table once per second and forwards the current
    snapshot to the client as a JSON frame:

    .. code-block:: json

        {
            "progress": 42,
            "status": "running",
            "status_description": "Generating wiki pages...",
            "retrying": false
        }

    The loop terminates (and the connection is closed) when ``status`` is
    ``"done"`` or ``"failed"``.

    If the ``job_id`` is not found in the database, the server sends::

        {"error": "Job not found"}

    and closes the connection.

    **``retrying`` flag**: set to ``true`` when ``status == "running"`` and
    ``status_description`` starts with the literal prefix ``"Retry "``.  This
    string is written by the worker's retry logic so clients can show a
    distinct UI state (e.g. a spinner with "Retrying…" label) instead of a
    plain progress bar.

    Args:
        websocket (WebSocket): The FastAPI WebSocket connection object.
        job_id (str): UUID string of the job to monitor, injected from the
            URL path.

    Example WebSocket session (client receives these frames in order):

    .. code-block:: json

        {"progress": 0,   "status": "queued",
         "status_description": "Queued...", "retrying": false}
        {"progress": 15,  "status": "running",
         "status_description": "Cloning repo...", "retrying": false}
        {"progress": 80,  "status": "running",
         "status_description": "Retry 1/3: page generation", "retrying": true}
        {"progress": 100, "status": "done",
         "status_description": "Wiki generated", "retrying": false}
    """
    await websocket.accept()
    cfg = get_config()
    try:
        while True:
            async with get_session(str(cfg.database_path)) as s:
                job = await s.get(Job, job_id)
            if job is None:
                await websocket.send_json({"error": "Job not found"})
                break
            # Detect whether the worker is currently in a retry cycle by
            # inspecting the status_description prefix written by the worker's
            # retry wrapper (format: "Retry N/M: <stage name>").
            retrying = (
                job.status == "running"
                and bool(job.status_description)
                and job.status_description.startswith("Retry ")
            )
            await websocket.send_json(
                {
                    "progress": job.progress,
                    "status": job.status,
                    "status_description": job.status_description,
                    "retrying": retrying,
                }
            )
            # Stop polling once the job has reached a terminal state.
            if job.status in ("done", "failed"):
                break
            # 1-second poll interval balances UI responsiveness against DB load.
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
