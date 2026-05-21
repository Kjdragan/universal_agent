"""Tests for the universal ZAI HTTP observability hook (P7 #2026-05-21)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
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


def test_classify_response_fup_body_overrides_429():
    """FUP body language outranks the status code — critical-immediate."""
    assert _classify_response(429, "fair use policy violation: account flagged", "") == EVENT_CATEGORY_FUP


def test_classify_response_403_with_fup_body_is_fup():
    assert _classify_response(403, "concurrency limit exceeded for account", "") == EVENT_CATEGORY_FUP


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
    """Keep classifier in lockstep with the P4 rate_limiter FUP_KEYWORDS."""
    from universal_agent.rate_limiter import FUP_KEYWORDS
    for kw in FUP_KEYWORDS:
        assert _classify_response(429, f"error: {kw} triggered", "") == EVENT_CATEGORY_FUP
