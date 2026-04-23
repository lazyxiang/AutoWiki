"""Tests for /api/settings/tokens CRUD endpoints."""

import pytest


def test_settings_auth_allows_requests_when_token_unset(monkeypatch):
    from api.routers.settings import verify_settings_auth
    from shared.config import reset_config

    monkeypatch.delenv("AUTOWIKI_SERVER_AUTH_TOKEN", raising=False)
    reset_config()
    try:
        verify_settings_auth(None)
    finally:
        reset_config()


def test_settings_auth_requires_bearer_token_when_configured(monkeypatch):
    from fastapi import HTTPException

    from api.routers.settings import verify_settings_auth
    from shared.config import reset_config

    monkeypatch.setenv("AUTOWIKI_SERVER_AUTH_TOKEN", "secret")
    reset_config()
    try:
        with pytest.raises(HTTPException) as exc:
            verify_settings_auth(None)
        assert exc.value.status_code == 401

        with pytest.raises(HTTPException) as bad:
            verify_settings_auth("Bearer wrong")
        assert bad.value.status_code == 403

        verify_settings_auth("Bearer secret")
    finally:
        reset_config()


async def test_get_tokens_empty(client):
    resp = await client.get("/api/settings/tokens")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    platforms = {item["platform"] for item in data}
    assert platforms == {"github", "gitlab", "bitbucket"}
    assert all(not item["has_token"] for item in data)
    assert all(item["masked_token"] is None for item in data)


async def test_put_token_github(client):
    resp = await client.put(
        "/api/settings/tokens/github", json={"token": "ghp_test1234abcd"}
    )
    assert resp.status_code == 204


async def test_put_token_rejects_blank(client):
    resp = await client.put("/api/settings/tokens/github", json={"token": "   "})

    assert resp.status_code == 422
    tokens = (await client.get("/api/settings/tokens")).json()
    github = next(item for item in tokens if item["platform"] == "github")
    assert github["has_token"] is False


async def test_put_token_trims_before_storing(client):
    await client.put(
        "/api/settings/tokens/github", json={"token": "  ghp_test1234abcd  "}
    )

    resp = await client.get("/api/settings/tokens")
    github = next(item for item in resp.json() if item["platform"] == "github")
    assert github["masked_token"].endswith("abcd")


async def test_get_tokens_after_put(client):
    await client.put("/api/settings/tokens/github", json={"token": "ghp_test1234abcd"})
    resp = await client.get("/api/settings/tokens")
    data = resp.json()
    github = next(item for item in data if item["platform"] == "github")
    assert github["has_token"] is True
    assert github["masked_token"] is not None
    assert github["masked_token"].endswith("abcd")
    assert "ghp_test" not in github["masked_token"]


async def test_put_token_replaces_existing(client):
    await client.put("/api/settings/tokens/github", json={"token": "ghp_old1111"})
    await client.put("/api/settings/tokens/github", json={"token": "ghp_new2222"})
    resp = await client.get("/api/settings/tokens")
    github = next(item for item in resp.json() if item["platform"] == "github")
    assert github["masked_token"].endswith("2222")


async def test_delete_token(client):
    await client.put("/api/settings/tokens/github", json={"token": "ghp_test1234abcd"})
    resp = await client.delete("/api/settings/tokens/github")
    assert resp.status_code == 204
    resp2 = await client.get("/api/settings/tokens")
    github = next(item for item in resp2.json() if item["platform"] == "github")
    assert github["has_token"] is False
    assert github["masked_token"] is None


async def test_delete_token_idempotent(client):
    resp = await client.delete("/api/settings/tokens/github")
    assert resp.status_code == 204


async def test_put_invalid_platform(client):
    resp = await client.put("/api/settings/tokens/unknown", json={"token": "abc"})
    assert resp.status_code == 422


async def test_delete_invalid_platform(client):
    resp = await client.delete("/api/settings/tokens/unknown")
    assert resp.status_code == 422
