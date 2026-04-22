from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class RepoMetadata:
    owner: str
    name: str
    description: str
    stars: int
    language: str
    default_branch: str
    is_private: bool


class PrivateRepoError(Exception):
    """Repo is inaccessible and no token is stored for this platform."""


class AuthenticationError(Exception):
    """A stored token was rejected by the platform API."""


class UnsupportedPlatformError(Exception):
    """The URL hostname does not map to a supported platform."""


class Platform(ABC):
    name: str  # "github" | "gitlab" | "bitbucket"

    @abstractmethod
    def parse_url(self, url: str) -> tuple[str, str]:
        """Return (owner, name). Owner may contain slashes for GitLab subgroups."""

    @abstractmethod
    async def fetch_metadata(
        self, owner: str, name: str, token: str | None
    ) -> RepoMetadata:
        """
        Fetch repo metadata from the platform API.
        Raises PrivateRepoError when inaccessible with no token.
        Raises AuthenticationError when a token is present but rejected.
        """

    @abstractmethod
    def authenticated_clone_url(self, owner: str, name: str, token: str | None) -> str:
        """Return HTTPS clone URL. Embeds token in URL when provided."""
