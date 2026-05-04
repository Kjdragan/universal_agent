"""Tests for the heartbeat liveness diagnostic script.

The script polls the dashboard overview endpoint and reports whether
the heartbeat is currently ticking within an acceptable staleness
window.  Designed for ad-hoc use post-deploy or during incident
investigation.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from scripts import check_heartbeat_liveness as probe


def _build_overview_response(
    *,
    last_run_epoch: int | None,
    interval_seconds: int = 1500,
    enabled: bool = True,
) -> dict[str, Any]:
    return {
        "heartbeat": {
            "enabled": enabled,
            "heartbeat_effective_interval_seconds": interval_seconds,
            "latest_last_run_epoch": last_run_epoch,
        }
    }


def _build_transport(payload: dict[str, Any]) -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/v1/dashboard/todolist/overview":
            return httpx.Response(200, json=payload)
        return httpx.Response(404)
    return httpx.MockTransport(_handler)


def test_check_passes_when_heartbeat_recent(monkeypatch):
    import time as _time
    now = int(_time.time())
    payload = _build_overview_response(last_run_epoch=now - 100, interval_seconds=600)
    transport = _build_transport(payload)

    result = probe.run_check(base_url="http://gw.test", transport=transport, now_fn=lambda: now)

    assert result.exit_code == 0
    assert result.fresh is True
    assert result.staleness_seconds < 600


def test_check_fails_when_heartbeat_stale(monkeypatch):
    import time as _time
    now = int(_time.time())
    # Last tick was 4x the interval ago — way past the 2x threshold.
    payload = _build_overview_response(last_run_epoch=now - 2400, interval_seconds=600)
    transport = _build_transport(payload)

    result = probe.run_check(base_url="http://gw.test", transport=transport, now_fn=lambda: now)

    assert result.exit_code != 0
    assert result.fresh is False
    assert result.staleness_seconds >= 1200  # 2x interval threshold


def test_check_fails_when_heartbeat_never_ticked():
    """A `latest_last_run_epoch=None` from the dashboard means the
    heartbeat has never produced a tick — the 2026-05-01 silence
    shape.  Must exit non-zero."""
    payload = _build_overview_response(last_run_epoch=None, interval_seconds=600)
    transport = _build_transport(payload)

    result = probe.run_check(base_url="http://gw.test", transport=transport, now_fn=lambda: 1_700_000_000)

    assert result.exit_code != 0
    assert result.never_ticked is True


def test_check_handles_heartbeat_disabled():
    """If `heartbeat.enabled=False`, exit 0 with a clear note — disabled
    is not a failure mode, it's a configuration choice."""
    payload = _build_overview_response(last_run_epoch=None, enabled=False)
    transport = _build_transport(payload)

    result = probe.run_check(base_url="http://gw.test", transport=transport, now_fn=lambda: 1_700_000_000)

    assert result.exit_code == 0
    assert result.disabled is True


def test_check_fails_when_dashboard_returns_error():
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "service unavailable"})
    transport = httpx.MockTransport(_handler)

    result = probe.run_check(base_url="http://gw.test", transport=transport, now_fn=lambda: 1_700_000_000)

    assert result.exit_code != 0
    assert result.api_failed is True
