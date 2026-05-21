"""REST + WebSocket endpoints for Deep Research.

B5: Deep Research is temporarily disabled pending migration to KeywordIndex.
See issue #43.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, WebSocket

logger = logging.getLogger(__name__)
router = APIRouter()

_DISABLED_MSG = (
    "Deep Research is temporarily unavailable while migrating to keyword retrieval "
    "(see issue #43)."
)


@router.post("/api/repos/{repo_id}/research", status_code=503)
async def start_research(repo_id: str) -> dict:  # type: ignore[override]
    """B5: Disabled — returns 503 until KeywordIndex migration is complete."""
    raise HTTPException(status_code=503, detail=_DISABLED_MSG)


@router.get("/api/repos/{repo_id}/research/{job_id}")
async def get_research(repo_id: str, job_id: str) -> dict:
    """B5: Disabled — returns 410 for all new lookups."""
    raise HTTPException(status_code=410, detail=_DISABLED_MSG)


@router.websocket("/ws/repos/{repo_id}/research/{job_id}")
async def ws_research(websocket: WebSocket, repo_id: str, job_id: str) -> None:
    """B5: Disabled — accepts and immediately closes with code 1011."""
    await websocket.accept()
    await websocket.close(code=1011, reason="feature disabled")
