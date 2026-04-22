from __future__ import annotations

import httpx

from worker.platform.base import (
    AuthenticationError,
    Platform,
    PrivateRepoError,
    RepoMetadata,
)


class BitbucketPlatform(Platform):
    name = "bitbucket"

    def parse_url(self, url: str) -> tuple[str, str]:
        cleaned = url.replace("https://", "").replace("http://", "").rstrip("/")
        parts = cleaned.split("/")
        try:
            idx = next(i for i, p in enumerate(parts) if p.lower() == "bitbucket.org")
            owner = parts[idx + 1]
            name = parts[idx + 2].removesuffix(".git")
        except (StopIteration, IndexError):
            raise ValueError(f"Cannot parse Bitbucket URL: {url!r}")
        if not owner or not name:
            raise ValueError(f"Cannot parse Bitbucket URL: {url!r}")
        return owner, name

    def authenticated_clone_url(self, owner: str, name: str, token: str | None) -> str:
        if token:
            return f"https://x-token-auth:{token}@bitbucket.org/{owner}/{name}.git"
        return f"https://bitbucket.org/{owner}/{name}.git"

    async def fetch_metadata(
        self, owner: str, name: str, token: str | None
    ) -> RepoMetadata:
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = f"https://api.bitbucket.org/2.0/repositories/{owner}/{name}"
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code == 401 and token is None:
                    raise PrivateRepoError(
                        f"Bitbucket repo {owner}/{name} is private. "
                        "Add a Bitbucket token in Settings."
                    )
                if code in (401, 403):
                    raise AuthenticationError(
                        "Bitbucket rejected the stored token. Check it in Settings."
                    )
                raise
        data = resp.json()
        mainbranch = data.get("mainbranch") or {}
        return RepoMetadata(
            owner=owner,
            name=name,
            description=data.get("description") or "",
            stars=0,
            language=data.get("language") or "",
            default_branch=mainbranch.get("name") or "main",
            is_private=bool(data.get("is_private", False)),
        )
