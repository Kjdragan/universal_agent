from __future__ import annotations

from datetime import datetime, timedelta, timezone

from csi_ingester.adapters.threads_api import (
    ThreadsAPIClient,
    ThreadsQuotaBudgetManager,
    ThreadsTokenManager,
)
import pytest


def test_threads_quota_budget_blocks_after_limit() -> None:
    manager = ThreadsQuotaBudgetManager.from_config(
        {
            "keyword_search": {"max_requests": 2, "window_seconds": 3600},
        },
        state={},
    )

    assert manager.allow("keyword_search") is True
    assert manager.allow("keyword_search") is True
    assert manager.allow("keyword_search") is False
    assert manager.remaining("keyword_search") == 0


def test_threads_token_expiring_soon_detection() -> None:
    soon = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    manager = ThreadsTokenManager(
        app_id="app-id",
        app_secret="app-secret",
        access_token="tok",
        token_expires_at=soon,
        refresh_buffer_seconds=3600,
    )

    assert manager.is_expiring_soon() is True


@pytest.mark.asyncio
async def test_threads_token_refresh_updates_access_token(monkeypatch):
    class _Resp:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"access_token": "tok-refreshed", "expires_in": 3600}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None):
            return _Resp()

    monkeypatch.setattr("csi_ingester.adapters.threads_api.httpx.AsyncClient", _Client)

    manager = ThreadsTokenManager(
        app_id="app-id",
        app_secret="app-secret",
        access_token="tok-old",
        token_expires_at="",
    )
    token, expires_at = await manager.refresh_long_lived_token()

    assert token == "tok-refreshed"
    assert manager.access_token == "tok-refreshed"
    assert expires_at.endswith("Z")


@pytest.mark.asyncio
async def test_threads_client_create_and_publish_container(monkeypatch):
    class _Resp:
        status_code = 200
        content = b"{}"
        text = ""

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs):
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None, headers=None):
            return _Resp({"data": []})

        async def post(self, url, data=None, headers=None):
            self.calls.append({"url": url, "data": data or {}})
            if url.endswith("/threads_publish"):
                return _Resp({"id": "17890000000000000"})
            return _Resp({"id": "90123456789012345"})

    monkeypatch.setattr("csi_ingester.adapters.threads_api.httpx.AsyncClient", _Client)

    client = ThreadsAPIClient.from_config(
        {
            "user_id": "34355872584027913",
            "access_token": "tok",
            "timeout_seconds": 5,
            "max_retries": 1,
        },
        quota_state={},
    )
    created = await client.create_media_container(media_type="TEXT", text="hello world")
    assert str(created.get("id")) == "90123456789012345"

    published = await client.publish_media_container(creation_id="90123456789012345")
    assert str(published.get("id")) == "17890000000000000"
