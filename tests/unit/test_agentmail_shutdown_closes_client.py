"""Regression guard: AgentMailService.shutdown() must close the httpx client.

Background (2026-05-30 YouTube digest run.log): every one-shot cron that sends
via AgentMailService logged an ERROR-level traceback —

    Task ... <AsyncClient.aclose()> exception=RuntimeError('Event loop is closed')

The digest runs `asyncio.run(_send())`; inside, AgentMailService.startup() built
an AsyncAgentMail whose internal httpx.AsyncClient was never closed. When
asyncio.run() tore down the loop, the client's GC finalizer scheduled aclose()
on the now-dead loop and raised. The fix: own the httpx client (SDK-supported
`httpx_client=` kwarg) and aclose() it inside shutdown(), while the loop is alive.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

_BASE_ENV = {
    "UA_AGENTMAIL_ENABLED": "1",
    "AGENTMAIL_API_KEY": "test-api-key-123",
    "UA_AGENTMAIL_INBOX_ADDRESS": "simone@testdomain.com",
    "UA_AGENTMAIL_WS_ENABLED": "0",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for k, v in _BASE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("UA_AGENTMAIL_INBOX_ADDRESSES", raising=False)
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(tmp_path / "activity_state.db"))


def test_startup_owns_httpx_client_and_shutdown_closes_it(monkeypatch):
    import agentmail

    from universal_agent.services import agentmail_service as ams

    captured: dict = {}

    class _FakeAsyncAgentMail:
        def __init__(self, *, api_key, timeout=None, httpx_client=None, **_kw):
            captured["httpx_client"] = httpx_client

    monkeypatch.setattr(agentmail, "AsyncAgentMail", _FakeAsyncAgentMail)

    svc = ams.AgentMailService()

    async def _fake_ensure_inbox():
        svc._inbox_id = "simone@testdomain.com"
        svc._inbox_address = "simone@testdomain.com"

    monkeypatch.setattr(svc, "_ensure_inbox", _fake_ensure_inbox)

    async def _run():
        await svc.startup()
        # The service must own an httpx client and inject THAT instance.
        assert isinstance(svc._httpx_client, httpx.AsyncClient)
        assert captured["httpx_client"] is svc._httpx_client
        client = svc._httpx_client
        assert client.is_closed is False
        await svc.shutdown()
        return client

    client = asyncio.run(_run())
    # After shutdown the client is closed and the reference is cleared, so no
    # finalizer fires aclose() on a dead loop.
    assert client.is_closed is True
    assert svc._httpx_client is None
    assert svc._client is None


def test_shutdown_without_startup_is_safe():
    """shutdown() before any startup (no client created) must not raise."""
    from universal_agent.services.agentmail_service import AgentMailService

    svc = AgentMailService()
    asyncio.run(svc.shutdown())
    assert svc._httpx_client is None
