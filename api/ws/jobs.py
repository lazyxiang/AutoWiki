import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from shared.config import get_config
from shared.database import get_session
from shared.models import Job

router = APIRouter()


@router.websocket("/ws/jobs/{job_id}")
async def ws_job_progress(websocket: WebSocket, job_id: str):
    await websocket.accept()
    cfg = get_config()
    try:
        while True:
            async with get_session(str(cfg.database_path)) as s:
                job = await s.get(Job, job_id)
            if job is None:
                await websocket.send_json({"error": "Job not found"})
                break
            await websocket.send_json(
                {
                    "progress": job.progress,
                    "status": job.status,
                    "status_description": job.status_description,
                }
            )
            if job.status in ("done", "failed"):
                break
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
