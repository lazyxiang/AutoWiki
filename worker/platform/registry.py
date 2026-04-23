from __future__ import annotations

from urllib.parse import urlsplit

from worker.platform.base import Platform, UnsupportedPlatformError
from worker.platform.bitbucket import BitbucketPlatform
from worker.platform.github import GitHubPlatform
from worker.platform.gitlab import GitLabPlatform

_BY_HOST: dict[str, Platform] = {
    "github.com": GitHubPlatform(),
    "gitlab.com": GitLabPlatform(),
    "bitbucket.org": BitbucketPlatform(),
}

_BY_NAME: dict[str, Platform] = {
    "github": GitHubPlatform(),
    "gitlab": GitLabPlatform(),
    "bitbucket": BitbucketPlatform(),
}


def detect_platform(url: str) -> Platform:
    parsed = urlsplit(url)
    scheme = parsed.scheme.removeprefix("git+").lower()
    if parsed.scheme:
        if scheme not in {"http", "https"}:
            raise UnsupportedPlatformError(f"Only http(s) URLs are supported: {url!r}")
        host = parsed.netloc.lower()
    else:
        if url.startswith("git@") or ":" in url.split("/", 1)[0]:
            raise UnsupportedPlatformError(f"Only http(s) URLs are supported: {url!r}")
        host = url.split("/", 1)[0].lower()

    platform = _BY_HOST.get(host)
    if platform is None:
        raise UnsupportedPlatformError(f"Unsupported host: {host!r}")
    return platform


def get_platform_by_name(name: str) -> Platform:
    platform = _BY_NAME.get(name)
    if platform is None:
        raise UnsupportedPlatformError(f"Unknown platform name: {name!r}")
    return platform
