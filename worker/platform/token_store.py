from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import PlatformToken


async def get_platform_token(platform_name: str, session: AsyncSession) -> str | None:
    result = await session.get(PlatformToken, platform_name)
    if result:
        return result.token
    if platform_name.startswith("gitlab:"):
        fallback = await session.get(PlatformToken, "gitlab")
        return fallback.token if fallback else None
    return None
