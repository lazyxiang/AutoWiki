from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import PlatformToken


async def get_platform_token(platform_name: str, session: AsyncSession) -> str | None:
    result = await session.get(PlatformToken, platform_name)
    return result.token if result else None
