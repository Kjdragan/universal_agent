"""Tests for the CSI Ingester app-level health/liveness endpoints (T13).

Exercises `csi_ingester.app`'s route handlers directly as coroutines (no
TestClient / DB startup needed): `healthz`/`livez`/`metrics` only read the
module-level `_service` global that `_startup()` normally assigns, so
monkeypatching that global reproduces real request handling without paying
for a full FastAPI lifespan + sqlite bootstrap.
"""

from __future__ import annotations

import time

from csi_ingester import app as app_module
from csi_ingester.adapters.youtube_channel_rss import YouTubeChannelRSSAdapter
from csi_ingester.metrics import MetricsRegistry
import pytest


class _FakeService:
    def __init__(self, adapters: dict):
        self.adapters = adapters


class _FakeResponse:
    def __init__(self) -> None:
        self.status_code = 200


def _overdue_rss_adapter() -> YouTubeChannelRSSAdapter:
    """An adapter configured to fetch every hour, whose last fetch is
    absurdly stale — the exact condition /livez exists to detect."""
    adapter = YouTubeChannelRSSAdapter(
        {"schedule_fetch_hours": list(range(24)), "poll_interval_seconds": 60}
    )
    adapter._last_fetch_epoch = time.time() - 999_999
    return adapter


@pytest.fixture(autouse=True)
def _reset_app_globals():
    """Restore module-level `_service`/`_metrics` after each test. These
    tests call route handlers directly (bypassing the real startup/shutdown
    lifecycle), so without this the module-level `_metrics` singleton — a
    long-lived Prometheus registry in production, by design — would leak
    gauge values set by one test into the next."""
    original_service = app_module._service
    original_metrics = app_module._metrics
    app_module._metrics = MetricsRegistry()
    yield
    app_module._service = original_service
    app_module._metrics = original_metrics


async def test_healthz_returns_ok_unconditionally_with_no_service():
    """/readyz reports not-ready without _service; /healthz must not — it's
    a plain, unconditional liveness stub and T13 leaves it untouched."""
    app_module._service = None
    assert await app_module.healthz() == {"status": "ok"}


async def test_healthz_stays_ok_even_when_the_rss_poller_is_wedged():
    """The whole point of T13: before this ticket, healthz was CSI's only
    watchdog signal, so a wedged poller was invisible. Now /livez flags it
    while /healthz — deliberately untouched — stays green, proving the two
    checks are decoupled as designed."""
    app_module._service = _FakeService({"youtube_channel_rss": _overdue_rss_adapter()})

    assert await app_module.healthz() == {"status": "ok"}

    livez_result = await app_module.livez(_FakeResponse())
    assert livez_result["live"] is False  # sanity: livez does catch it


async def test_livez_healthy_when_rss_adapter_not_configured():
    app_module._service = None
    response = _FakeResponse()
    result = await app_module.livez(response)
    assert result == {"live": True, "reason": "adapter_not_configured"}
    assert response.status_code == 200


async def test_livez_returns_503_when_fetch_is_genuinely_overdue():
    app_module._service = _FakeService({"youtube_channel_rss": _overdue_rss_adapter()})
    response = _FakeResponse()
    result = await app_module.livez(response)
    assert result["live"] is False
    assert result["reason"] == "fetch_overdue"
    assert response.status_code == 503


async def test_livez_stays_200_inside_a_quiet_window_with_stale_epoch():
    """Mirrors the T13 incident: an hour excluded from schedule_fetch_hours
    must report healthy even with a days-stale epoch. Uses the real wall
    clock with a schedule hour 12h away from "now" (mod 24, so it can never
    equal the current hour) rather than mocking time — deterministic without
    patching `datetime`."""
    now = time.time()
    current_hour_utc = time.gmtime(now).tm_hour
    excluded_hour = (current_hour_utc + 12) % 24
    adapter = YouTubeChannelRSSAdapter(
        {
            "schedule_fetch_hours": [excluded_hour],
            "schedule_timezone": "UTC",
            "poll_interval_seconds": 60,
        }
    )
    adapter._last_fetch_epoch = now - 5 * 24 * 3600  # 5 days stale
    app_module._service = _FakeService({"youtube_channel_rss": adapter})

    response = _FakeResponse()
    result = await app_module.livez(response)
    assert result["live"] is True
    assert result["reason"] == "quiet_window"
    assert response.status_code == 200


async def test_metrics_endpoint_exposes_seconds_since_fetch_gauge():
    adapter = YouTubeChannelRSSAdapter({"poll_interval_seconds": 60})
    adapter._last_fetch_epoch = time.time() - 42.0
    app_module._service = _FakeService({"youtube_channel_rss": adapter})

    body = await app_module.metrics()
    assert "# TYPE csi_rss_seconds_since_fetch gauge" in body
    lines = [ln for ln in body.splitlines() if ln.startswith("csi_rss_seconds_since_fetch ")]
    assert len(lines) == 1
    value = float(lines[0].split()[1])
    assert 40.0 <= value <= 45.0  # allow test-runtime slack


async def test_metrics_endpoint_omits_gauge_when_never_fetched():
    adapter = YouTubeChannelRSSAdapter({"poll_interval_seconds": 60})
    app_module._service = _FakeService({"youtube_channel_rss": adapter})

    body = await app_module.metrics()
    assert "csi_rss_seconds_since_fetch" not in body
