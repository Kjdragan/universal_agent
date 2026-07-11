"""The session file-upload endpoint must require auth.

`upload_session_file` auto-creates a workspace and returns file content the
frontend injects into the session prompt. It was the only /api/v1/sessions/*
route with no auth call and no Request param, so an unauthenticated internet
caller (it's proxied to the public dashboard host) could fill disk or inject a
prompt into another session. The handler now takes a Request and calls
_require_session_api_auth first; the dashboard sends the upload through the
token-injecting proxy.
"""

import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import universal_agent.gateway_server as gs


def _req() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],  # no token
            "query_string": b"",
        }
    )


def test_upload_requires_auth(monkeypatch):
    # A session token is configured, so auth is enforced; no token supplied.
    monkeypatch.setattr(gs, "SESSION_API_TOKEN", "sess-token", raising=False)
    monkeypatch.setattr(gs, "OPS_TOKEN", "", raising=False)
    with pytest.raises(HTTPException) as ei:
        # file arg is never reached — auth is the first statement.
        asyncio.run(gs.upload_session_file("sess-1", _req(), None))
    assert ei.value.status_code == 401
