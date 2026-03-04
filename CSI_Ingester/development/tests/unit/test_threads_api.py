from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from csi_ingester.adapters.threads_api import (
    ThreadsQuotaBudgetManager,
    ThreadsTokenManager,
)


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
