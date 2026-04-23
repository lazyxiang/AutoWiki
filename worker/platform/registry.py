from __future__ import annotations

from urllib.parse import urlsplit

from worker.platform.base import Platform, UnsupportedPlatformError
from worker.platform.bitbucket import BitbucketPlatform
from worker.platform.gitee import GiteePlatform
from worker.platform.github import GitHubPlatform
from worker.platform.gitlab import GitLabPlatform

_BY_HOST: dict[str, Platform] = {
    "github.com": GitHubPlatform(),
    "gitlab.com": GitLabPlatform(),
    "bitbucket.org": BitbucketPlatform(),
    "gitee.com": GiteePlatform(),
}

_BY_NAME: dict[str, Platform] = {
    "github": GitHubPlatform(),
    "gitlab": GitLabPlatform(),
    "bitbucket": BitbucketPlatform(),
    "gitee": GiteePlatform(),
}


def detect_platform(url: str) -> Platform:
    parsed = urlsplit(url)
    raw_scheme = parsed.scheme.lower()
    force_gitlab = False
    if raw_scheme.startswith("gitlab+"):
        force_gitlab = True
        scheme = raw_scheme.removeprefix("gitlab+")
    else:
        scheme = raw_scheme.removeprefix("git+")
    if parsed.scheme:
        if scheme not in {"http", "https"}:
            raise UnsupportedPlatformError(f"Only http(s) URLs are supported: {url!r}")
        host = parsed.netloc.lower()
    else:
        if url.startswith("git@") or ":" in url.split("/", 1)[0]:
            raise UnsupportedPlatformError(f"Only http(s) URLs are supported: {url!r}")
        host = url.split("/", 1)[0].lower()

    if force_gitlab:
        return GitLabPlatform(host=host)

    platform = _BY_HOST.get(host)
    if platform is not None:
        return platform

    # Support self-hosted GitLab instances on custom domains.
    if "gitlab" in host:
        return GitLabPlatform(host=host)

    raise UnsupportedPlatformError(f"Unsupported host: {host!r}")


def get_platform_by_name(name: str) -> Platform:
    if name.startswith("gitlab:"):
        _, _, host = name.partition(":")
        if host:
            return GitLabPlatform(host=host)
    platform = _BY_NAME.get(name)
    if platform is None:
        raise UnsupportedPlatformError(f"Unknown platform name: {name!r}")
    return platform
