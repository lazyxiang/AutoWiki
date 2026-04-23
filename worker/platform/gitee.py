from __future__ import annotations

import httpx

from worker.platform.base import (
    AuthenticationError,
    Platform,
    PrivateRepoError,
    RepoMetadata,
)


class GiteePlatform(Platform):
    name = "gitee"

    def parse_url(self, url: str) -> tuple[str, str]:
        cleaned = url
        for prefix in ("git+https://", "git+http://", "https://", "http://"):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix) :]
                break
        cleaned = cleaned.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        parts = cleaned.split("/")
        try:
            idx = next(i for i, p in enumerate(parts) if p.lower() == "gitee.com")
            owner = parts[idx + 1]
            name = parts[idx + 2].removesuffix(".git")
        except (StopIteration, IndexError) as exc:
            raise ValueError(f"Cannot parse Gitee URL: {url!r}") from exc
        if not owner or not name:
            raise ValueError(f"Cannot parse Gitee URL: {url!r}")
        return owner, name

    def authenticated_clone_url(self, owner: str, name: str, token: str | None) -> str:
        if token:
            return f"https://{token}@gitee.com/{owner}/{name}.git"
        return f"https://gitee.com/{owner}/{name}.git"

    async def fetch_metadata(
        self, owner: str, name: str, token: str | None
    ) -> RepoMetadata:
        params: dict[str, str] = {}
        if token:
            params["access_token"] = token
        url = f"https://gitee.com/api/v5/repos/{owner}/{name}"

        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code in (401, 403) and token is not None:
                    raise AuthenticationError(
                        "Gitee rejected the stored token. Check it in Settings."
                    )
                if code in (401, 403, 404) and token is None:
                    raise PrivateRepoError(
                        f"Gitee repo {owner}/{name} is private or not found. "
                        "Add a Gitee token in Settings."
                    )
                if code == 404 and token is not None:
                    raise AuthenticationError(
                        f"Gitee repo {owner}/{name} was not found or the stored "
                        "token cannot access it. Check it in Settings."
                    )
                raise

        data = resp.json()
        return RepoMetadata(
            owner=owner,
            name=name,
            description=data.get("description") or "",
            stars=data.get("stargazers_count") or 0,
            language=data.get("language") or "",
            default_branch=data.get("default_branch") or "master",
            is_private=bool(data.get("private", False)),
        )
