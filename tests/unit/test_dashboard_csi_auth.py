"""The mutating /api/v1/dashboard/csi/* gateway endpoints must require ops auth.

These endpoints (digest send/delete/clear, purge, specialist-loop actions) were
reachable unauthenticated — a review probe confirmed the sibling GET returned
200 with no token on the live gateway while an auth-gated control returned 401.
They are called from the dashboard exclusively through the token-injecting
proxy (`/api/dashboard/gateway/*`), so gating them with `_require_ops_auth`
does not affect the UI. Each test calls the handler directly with a token-less
Request (auth is the first line, so no destructive work runs) and asserts 401.
"""

import asyncio
import inspect

from fastapi import HTTPException
import pytest
from starlette.requests import Request

import universal_agent.gateway_server as gs


def _req() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],  # no Authorization / x-ua-ops-token
            "query_string": b"",
        }
    )


@pytest.fixture(autouse=True)
def _ops_token_set(monkeypatch):
    # With a token configured, _require_ops_auth must enforce it (not fail open).
    monkeypatch.setattr(gs, "OPS_TOKEN", "test-ops-token", raising=False)
    monkeypatch.setattr(gs, "OPS_JWT_SECRET", "", raising=False)


def _assert_401(call):
    # Handlers may be sync `def` (blocking work runs in Starlette's threadpool)
    # or `async def`; invoke inside pytest.raises so a sync handler's immediate
    # raise is caught, and only drive the event loop when we actually got a
    # coroutine back.
    with pytest.raises(HTTPException) as ei:
        result = call()
        if inspect.iscoroutine(result):
            asyncio.run(result)
    assert ei.value.status_code == 401


def test_purge_requires_auth():
    _assert_401(lambda: gs.dashboard_csi_purge(_req()))


def test_clear_all_digests_requires_auth():
    _assert_401(lambda: gs.dashboard_csi_clear_all(_req()))


def test_delete_digest_requires_auth():
    _assert_401(lambda: gs.dashboard_csi_delete_digest("some-id", _req()))


def test_send_to_simone_requires_auth():
    _assert_401(lambda: gs.send_csi_digest_to_simone("some-id", _req()))


def test_specialist_loop_action_requires_auth():
    payload = gs.CSISpecialistLoopActionRequest(action="request_followup")
    _assert_401(lambda: gs.dashboard_csi_specialist_loop_action("topic", payload, _req()))


def test_specialist_loop_triage_requires_auth():
    payload = gs.CSISpecialistLoopTriageRequest()
    _assert_401(lambda: gs.dashboard_csi_specialist_loop_triage(payload, _req()))


def test_specialist_loop_cleanup_requires_auth():
    payload = gs.CSISpecialistLoopCleanupRequest()
    _assert_401(lambda: gs.dashboard_csi_specialist_loop_cleanup(payload, _req()))
