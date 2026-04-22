"""REST endpoints for platform token management."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from shared.config import get_config
from shared.database import get_session
from shared.models import PlatformToken

router = APIRouter(prefix="/api/settings")

VALID_PLATFORMS = {"github", "gitlab", "bitbucket"}
_ORDERED_PLATFORMS = ("github", "gitlab", "bitbucket")


def _mask(token: str) -> str:
    if len(token) <= 4:
        return "••••"
    return "••••••••" + token[-4:]


@router.get("/tokens")
async def list_tokens() -> list[dict]:
    cfg = get_config()
    async with get_session(str(cfg.database_path)) as s:
        result = await s.execute(select(PlatformToken))
        stored = {row.platform: row for row in result.scalars().all()}
    return [
        {
            "platform": p,
            "has_token": p in stored,
            "masked_token": _mask(stored[p].token) if p in stored else None,
        }
        for p in _ORDERED_PLATFORMS
    ]


class TokenRequest(BaseModel):
    token: str


@router.put("/tokens/{platform}", status_code=204)
async def upsert_token(platform: str, req: TokenRequest) -> None:
    if platform not in VALID_PLATFORMS:
        raise HTTPException(status_code=422, detail=f"Unknown platform: {platform!r}")
    token = req.token.strip()
    if not token:
        raise HTTPException(status_code=422, detail="Blank token not allowed")
    cfg = get_config()
    now = datetime.now(UTC)
    async with get_session(str(cfg.database_path)) as s:
        existing = await s.get(PlatformToken, platform)
        if existing is None:
            s.add(
                PlatformToken(
                    platform=platform,
                    token=token,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            existing.token = token
            existing.updated_at = now
        await s.commit()


@router.delete("/tokens/{platform}", status_code=204)
async def delete_token(platform: str) -> None:
    if platform not in VALID_PLATFORMS:
        raise HTTPException(status_code=422, detail=f"Unknown platform: {platform!r}")
    cfg = get_config()
    async with get_session(str(cfg.database_path)) as s:
        existing = await s.get(PlatformToken, platform)
        if existing is not None:
            await s.delete(existing)
            await s.commit()
