import pytest

from worker.platform.base import (
    AuthenticationError,
    PrivateRepoError,
    RepoMetadata,
    UnsupportedPlatformError,
)


def test_repo_metadata_fields():
    meta = RepoMetadata(
        owner="acme",
        name="core",
        description="Core lib",
        stars=10,
        language="Python",
        default_branch="main",
        is_private=False,
    )
    assert meta.owner == "acme"
    assert meta.is_private is False


def test_private_repo_error_is_exception():
    with pytest.raises(PrivateRepoError):
        raise PrivateRepoError("no token")


def test_authentication_error_is_exception():
    with pytest.raises(AuthenticationError):
        raise AuthenticationError("bad token")


def test_unsupported_platform_error_is_exception():
    with pytest.raises(UnsupportedPlatformError):
        raise UnsupportedPlatformError("unknown host")
