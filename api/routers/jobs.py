from fastapi import APIRouter, HTTPException

from shared.config import get_config
from shared.database import get_session
from shared.models import Job

router = APIRouter(prefix="/api/jobs")


@router.get("/{job_id}")
async def get_job(job_id: str):
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
            "error": job.error,
        }
