"""Tests for the universal ZAI HTTP observability hook (P7 #2026-05-21)."""

from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
from unittest.mock import patch

import httpx
import pytest

from universal_agent.services.zai_observability import (
    EVENT_CATEGORY_CLIENT_ERROR,
    EVENT_CATEGORY_FUP,
    EVENT_CATEGORY_OK,
    EVENT_CATEGORY_RATE_LIMITED,
    EVENT_CATEGORY_SERVER_ERROR,
    ZAI_HOSTS,
    _classify_response,
    _events_path,
    _is_zai_url,
    _trim_events_file,
    install_zai_observability,
)


@pytest.fixture
def isolated_events_path(tmp_path, monkeypatch):
    events = tmp_path / "zai_inference_events.jsonl"
    monkeypatch.setenv("UA_ZAI_EVENTS_PATH", str(events))
    yield events


def test_zai_hosts_includes_api_z_ai():
    assert "api.z.ai" in ZAI_HOSTS


def test_is_zai_url_matches_known_hosts():
    assert _is_zai_url("https://api.z.ai/api/anthropic/v1/messages")
    assert _is_zai_url("https://api.z.ai/api/coding/paas/v4/chat/completions")
    assert not _is_zai_url("https://api.openai.com/v1/chat/completions")
    assert not _is_zai_url("https://api.anthropic.com/v1/messages")
    assert not _is_zai_url("https://www.googleapis.com/youtube/v3/playlistItems")


def test_classify_response_200_is_ok():
    assert _classify_response(200, "", "") == EVENT_CATEGORY_OK


def test_classify_response_429_is_rate_limited():
    assert _classify_response(429, "Too Many Requests", "") == EVENT_CATEGORY_RATE_LIMITED


def test_classify_response_429_with_fup_body_is_still_rate_limited():
    """ZAI's ordinary throttle IS a 1313/FUP-texted 429 (verified prod
    2026-06-11, 1058/1058 over 12h). A status==429 is the rate-limit GRADIENT
    and stays `rate_limited_429` even when the body matches FUP keywords —
    otherwise every throttle would falsely read as the cliff and the CRITICAL
    FUP tier would fire continuously. The orthogonal `fup_texted` event field
    preserves 1313 visibility; see the hook tests below."""
    assert (
        _classify_response(429, "fair use policy violation: account flagged", "")
        == EVENT_CATEGORY_RATE_LIMITED
    )
    assert (
        _classify_response(
            429,
            "[1313][Your account's current usage pattern does not comply with the "
            "Fair Usage Policy]",
            "",
        )
        == EVENT_CATEGORY_RATE_LIMITED
    )


def test_classify_response_non_429_with_fup_body_is_fup_cliff():
    """`fup_signal` is reserved for the CLIFF — FUP-keyword bodies on a
    NON-429 status (e.g. a 403 suspension)."""
    assert _classify_response(403, "concurrency limit exceeded for account", "") == EVENT_CATEGORY_FUP
    assert _classify_response(403, "account suspended for policy violation", "") == EVENT_CATEGORY_FUP


def test_classify_response_5xx_is_server_error():
    assert _classify_response(503, "service unavailable", "") == EVENT_CATEGORY_SERVER_ERROR
    assert _classify_response(502, "", "") == EVENT_CATEGORY_SERVER_ERROR


def test_classify_response_400_is_client_error():
    assert _classify_response(400, "bad request", "") == EVENT_CATEGORY_CLIENT_ERROR


def test_events_path_respects_env_override(isolated_events_path):
    assert str(_events_path()) == str(isolated_events_path)


def test_events_path_default_under_workspaces(monkeypatch):
    monkeypatch.delenv("UA_ZAI_EVENTS_PATH", raising=False)
    path = _events_path()
    assert "AGENT_RUN_WORKSPACES" in str(path)
    assert path.name == "zai_inference_events.jsonl"


def test_trim_events_file_keeps_last_n(isolated_events_path):
    with open(isolated_events_path, "w") as f:
        for i in range(100):
            f.write(json.dumps({"i": i}) + "\n")
    _trim_events_file(isolated_events_path, max_lines=30)
    lines = isolated_events_path.read_text().strip().split("\n")
    assert len(lines) == 30
    assert json.loads(lines[0])["i"] == 70
    assert json.loads(lines[-1])["i"] == 99


def test_trim_below_max_is_noop(isolated_events_path):
    with open(isolated_events_path, "w") as f:
        for i in range(5):
            f.write(json.dumps({"i": i}) + "\n")
    mtime_before = isolated_events_path.stat().st_mtime
    time.sleep(0.01)
    _trim_events_file(isolated_events_path, max_lines=10)
    assert isolated_events_path.stat().st_mtime == mtime_before


def test_install_zai_observability_is_idempotent():
    import universal_agent.services.zai_observability as zo
    zo._INSTALLED = False
    try:
        assert zo.install_zai_observability() is True
        assert zo.install_zai_observability() is False
    finally:
        zo._INSTALLED = True


def test_hooks_capture_zai_429_response(isolated_events_path):
    """End-to-end: install hook, mock a 429 ZAI response, confirm capture."""
    import universal_agent.services.zai_observability as zo
    zo._INSTALLED = False
    zo.install_zai_observability()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            text='{"error": "rate limit"}',
            headers={"retry-after": "30", "x-ratelimit-remaining": "0"},
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        client.post(
            "https://api.z.ai/api/anthropic/v1/messages",
            json={"model": "glm-4.6", "messages": []},
        )

    assert isolated_events_path.exists()
    line = isolated_events_path.read_text().strip().split("\n")[-1]
    event = json.loads(line)
    assert event["status"] == 429
    assert event["category"] == EVENT_CATEGORY_RATE_LIMITED
    assert event["url_path"] == "/api/anthropic/v1/messages"
    assert event["retry_after"] == "30"
    assert event["ratelimit_remaining"] == "0"
    assert event.get("caller")
    assert event.get("response_time_ms") is not None


def test_hooks_skip_non_zai_calls(isolated_events_path):
    """Only ZAI URLs should be captured."""
    import universal_agent.services.zai_observability as zo
    zo._INSTALLED = False
    zo.install_zai_observability()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        client.get("https://www.googleapis.com/youtube/v3/playlistItems")
        client.get("https://api.agentmail.to/v0/inboxes")
        client.get("https://export.arxiv.org/api/query")

    if isolated_events_path.exists():
        assert isolated_events_path.read_text().strip() == ""


@pytest.mark.asyncio
async def test_async_hooks_capture_zai_response(isolated_events_path):
    """AsyncClient is the primary path for UA — Cody, Atlas, briefings_agent."""
    import universal_agent.services.zai_observability as zo
    zo._INSTALLED = False
    zo.install_zai_observability()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='{"ok": true}',
            headers={"x-ratelimit-remaining": "150"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await client.post(
            "https://api.z.ai/api/coding/paas/v4/chat/completions",
            json={"model": "glm-4.6"},
        )

    assert isolated_events_path.exists()
    event = json.loads(isolated_events_path.read_text().strip().split("\n")[-1])
    assert event["status"] == 200
    assert event["category"] == EVENT_CATEGORY_OK
    assert event["ratelimit_remaining"] == "150"


def test_hook_never_raises_on_bad_write_path(isolated_events_path, monkeypatch):
    """Hot path safety: a broken events-file path must not crash the request."""
    import universal_agent.services.zai_observability as zo
    zo._INSTALLED = False
    zo.install_zai_observability()

    monkeypatch.setenv("UA_ZAI_EVENTS_PATH", "/proc/nonexistent_dir/events.jsonl")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        resp = client.post("https://api.z.ai/api/anthropic/v1/messages", json={"x": 1})
    assert resp.status_code == 200


def test_fup_keywords_match_p4_set():
    """Keep the FUP-body matcher in lockstep with the P4 rate_limiter
    FUP_KEYWORDS. With the gradient-vs-cliff split, every keyword on a NON-429
    status classifies as the `fup_signal` cliff; on a 429 it stays the
    `rate_limited_429` gradient (but `_is_fup_body` still recognizes it, which
    is what drives the `fup_texted` event field)."""
    from universal_agent.rate_limiter import FUP_KEYWORDS
    from universal_agent.services.zai_observability import _is_fup_body
    for kw in FUP_KEYWORDS:
        body = f"error: {kw} triggered"
        assert _is_fup_body(body)
        assert _classify_response(403, body, "") == EVENT_CATEGORY_FUP
        assert _classify_response(429, body, "") == EVENT_CATEGORY_RATE_LIMITED


# ── Real-socket hook body-read regression (#zai-observability) ───────────────
# The dark-FUP bug: at response-hook time with a REAL transport, the body is
# not yet read, so `_capture`'s `response.text` raises `httpx.ResponseNotRead`
# and the snippet comes back empty — which blinded both the `fup_texted` flag
# and FUP classification. `httpx.MockTransport` responses are PRE-BUFFERED so
# they cannot reproduce the bug; these tests use a real local socket server.

_FUP_1313_BODY = (
    '{"error": {"code": "1313", "message": "[1313][Your account\'s current usage '
    'pattern does not comply with the Fair Usage Policy. Please reduce your '
    'request frequency.]"}}'
)


@contextmanager
def _local_server(status: int, body: str):
    """Spin up a real HTTP server on 127.0.0.1:0 that returns the given status
    and body for any request. Yields the base URL. Real sockets (not a
    MockTransport) so the response body is genuinely unread at hook time —
    required to reproduce the ResponseNotRead bug."""

    class _Handler(BaseHTTPRequestHandler):
        def _respond(self):
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("retry-after", "30")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):  # noqa: N802
            self._respond()

        def do_POST(self):  # noqa: N802
            # Drain the request body so the connection is reusable.
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length:
                self.rfile.read(length)
            self._respond()

        def log_message(self, *args):  # silence test noise
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _install_hooks_fresh():
    import universal_agent.services.zai_observability as zo

    zo._INSTALLED = False
    zo.install_zai_observability()
    return zo


def _restore_hooks(zo):
    # Restore the pristine httpx __init__ so the monkey-patch doesn't leak into
    # other tests, and reset the flag.
    httpx.Client.__init__ = zo._ORIG_SYNC_INIT
    httpx.AsyncClient.__init__ = zo._ORIG_ASYNC_INIT
    zo._INSTALLED = False


def test_hook_force_reads_error_body_sync_real_socket(isolated_events_path, monkeypatch):
    """REGRESSION: 1313-texted 429 over a real socket must capture a non-empty
    body_snippet, classify as the rate_limited_429 GRADIENT (not the cliff),
    set fup_texted=True, and leave the response consumable downstream."""
    # Point the events file at the isolated path even though host is localhost.
    monkeypatch.setattr(
        "universal_agent.services.zai_observability._is_zai_url", lambda url: True
    )
    zo = _install_hooks_fresh()
    try:
        with _local_server(429, _FUP_1313_BODY) as base:
            with httpx.Client() as client:
                resp = client.post(f"{base}/api/anthropic/v1/messages",
                                   json={"model": "glm-5-turbo", "messages": []})
            # Downstream consumption must still work (httpx cached the body).
            assert resp.status_code == 429
            assert "1313" in resp.text
            assert resp.json()["error"]["code"] == "1313"
    finally:
        _restore_hooks(zo)

    event = json.loads(isolated_events_path.read_text().strip().split("\n")[-1])
    assert event["status"] == 429
    assert event["category"] == EVENT_CATEGORY_RATE_LIMITED  # gradient, not cliff
    assert event["fup_texted"] is True
    assert event.get("body_snippet"), "body_snippet must be non-empty after force-read"
    assert "1313" in event["body_snippet"]
    assert event["model"] == "glm-5-turbo"


@pytest.mark.asyncio
async def test_hook_force_reads_error_body_async_real_socket(isolated_events_path, monkeypatch):
    """Async variant of the body-read regression over a real socket."""
    monkeypatch.setattr(
        "universal_agent.services.zai_observability._is_zai_url", lambda url: True
    )
    zo = _install_hooks_fresh()
    try:
        with _local_server(429, _FUP_1313_BODY) as base:
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"{base}/api/anthropic/v1/messages",
                                        json={"model": "glm-5.1", "messages": []})
            assert resp.status_code == 429
            assert "1313" in resp.text  # consumable downstream
    finally:
        _restore_hooks(zo)

    event = json.loads(isolated_events_path.read_text().strip().split("\n")[-1])
    assert event["status"] == 429
    assert event["category"] == EVENT_CATEGORY_RATE_LIMITED
    assert event["fup_texted"] is True
    assert event.get("body_snippet")
    assert "1313" in event["body_snippet"]
    assert event["model"] == "glm-5.1"


def test_hook_non_429_fup_body_classifies_as_cliff_real_socket(isolated_events_path, monkeypatch):
    """A 403 carrying FUP text over a real socket → the `fup_signal` cliff."""
    monkeypatch.setattr(
        "universal_agent.services.zai_observability._is_zai_url", lambda url: True
    )
    body = '{"error": "account suspended for fair use policy violation"}'
    zo = _install_hooks_fresh()
    try:
        with _local_server(403, body) as base:
            with httpx.Client() as client:
                resp = client.post(f"{base}/api/anthropic/v1/messages",
                                   json={"model": "glm-5-turbo"})
            assert resp.status_code == 403
    finally:
        _restore_hooks(zo)

    event = json.loads(isolated_events_path.read_text().strip().split("\n")[-1])
    assert event["status"] == 403
    assert event["category"] == EVENT_CATEGORY_FUP  # genuine cliff
    assert event["fup_texted"] is True
    assert event.get("body_snippet")


# ── model field parsing (fail-soft) ─────────────────────────────────────────

def test_model_field_parsed_from_json_body(isolated_events_path):
    """A JSON request body's `model` is captured on the event."""
    zo = _install_hooks_fresh()
    try:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text='{"ok": true}')

        transport = httpx.MockTransport(handler)
        with httpx.Client(transport=transport) as client:
            client.post("https://api.z.ai/api/anthropic/v1/messages",
                       json={"model": "glm-5-turbo", "messages": []})
    finally:
        _restore_hooks(zo)
    event = json.loads(isolated_events_path.read_text().strip().split("\n")[-1])
    assert event["model"] == "glm-5-turbo"


def test_model_field_unknown_on_non_json_body(isolated_events_path):
    zo = _install_hooks_fresh()
    try:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="ok")

        transport = httpx.MockTransport(handler)
        with httpx.Client(transport=transport) as client:
            client.post("https://api.z.ai/api/anthropic/v1/messages",
                       content=b"this is not json")
    finally:
        _restore_hooks(zo)
    event = json.loads(isolated_events_path.read_text().strip().split("\n")[-1])
    assert event["model"] == "unknown"


def test_model_field_unknown_on_empty_body(isolated_events_path):
    zo = _install_hooks_fresh()
    try:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="ok")

        transport = httpx.MockTransport(handler)
        with httpx.Client(transport=transport) as client:
            client.get("https://api.z.ai/api/anthropic/v1/messages")  # no body
    finally:
        _restore_hooks(zo)
    event = json.loads(isolated_events_path.read_text().strip().split("\n")[-1])
    assert event["model"] == "unknown"


def test_model_field_unknown_on_streaming_request_content_raises(isolated_events_path):
    """A streaming request body raises httpx.RequestNotRead when `.content` is
    accessed — `_model_from_request` must fail-soft to 'unknown' and still
    append the event."""
    from universal_agent.services.zai_observability import _model_from_request

    def _gen():
        yield b'{"model": "glm-5-turbo"}'

    req = httpx.Request("POST", "https://api.z.ai/api/anthropic/v1/messages", content=_gen())
    # `.content` on a streaming request raises RequestNotRead.
    with pytest.raises(httpx.RequestNotRead):
        _ = req.content
    assert _model_from_request(req) == "unknown"


# ── trim atomicity ──────────────────────────────────────────────────────────

def test_trim_events_file_atomic_no_tmp_residue(isolated_events_path):
    """After an atomic trim the file has exactly the last N lines and there is
    no `.tmp` residue left behind."""
    with open(isolated_events_path, "w") as f:
        for i in range(200):
            f.write(json.dumps({"i": i}) + "\n")
    _trim_events_file(isolated_events_path, max_lines=50)
    lines = isolated_events_path.read_text().strip().split("\n")
    assert len(lines) == 50
    assert json.loads(lines[0])["i"] == 150
    assert json.loads(lines[-1])["i"] == 199
    tmp = isolated_events_path.with_suffix(isolated_events_path.suffix + ".tmp")
    assert not tmp.exists(), "atomic trim must not leave a .tmp residue"
