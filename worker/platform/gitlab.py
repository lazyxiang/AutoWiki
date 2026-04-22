from __future__ import annotations

import urllib.parse

import httpx

from worker.platform.base import (
    AuthenticationError,
    Platform,
    PrivateRepoError,
    RepoMetadata,
)


class GitLabPlatform(Platform):
    name = "gitlab"

    def parse_url(self, url: str) -> tuple[str, str]:
        cleaned = url.replace("https://", "").replace("http://", "").rstrip("/")
        parts = cleaned.split("/")
        try:
            idx = next(i for i, p in enumerate(parts) if p.lower() == "gitlab.com")
            segments = [p.removesuffix(".git") for p in parts[idx + 1 :]]
        except StopIteration:
            raise ValueError(f"Cannot parse GitLab URL: {url!r}")
        if len(segments) < 2:
            raise ValueError(f"Cannot parse GitLab URL: {url!r}")
        name = segments[-1]
        owner = "/".join(segments[:-1])
        return owner, name

    def authenticated_clone_url(self, owner: str, name: str, token: str | None) -> str:
        if token:
            return f"https://oauth2:{token}@gitlab.com/{owner}/{name}.git"
        return f"https://gitlab.com/{owner}/{name}.git"

    async def fetch_metadata(
        self, owner: str, name: str, token: str | None
    ) -> RepoMetadata:
        encoded_path = urllib.parse.quote(f"{owner}/{name}", safe="")
        headers: dict[str, str] = {}
        if token:
            headers["PRIVATE-TOKEN"] = token

        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.get(
                    f"https://gitlab.com/api/v4/projects/{encoded_path}",
                    headers=headers,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code == 404 and token is None:
                    raise PrivateRepoError(
                        f"GitLab repo {owner}/{name} is private. "
                        "Add a GitLab token in Settings."
                    )
                if code in (401, 403):
                    raise AuthenticationError(
                        "GitLab rejected the stored token. Check it in Settings."
                    )
                raise
            data = resp.json()

            language = ""
            try:
                lang_resp = await client.get(
                    f"https://gitlab.com/api/v4/projects/{encoded_path}/languages",
                    headers=headers,
                )
                lang_resp.raise_for_status()
                langs = lang_resp.json()
                language = next(iter(langs), "")
            except Exception:
                pass

        return RepoMetadata(
            owner=owner,
            name=name,
            description=data.get("description") or "",
            stars=data.get("star_count") or 0,
            language=language,
            default_branch=data.get("default_branch") or "main",
            is_private=data.get("visibility") != "public",
        )
