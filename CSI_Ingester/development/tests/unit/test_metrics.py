"""Tests for csi_ingester.metrics.MetricsRegistry gauge support (T13).

Gauges were added to expose `csi.rss.seconds_since_fetch` — a computed,
point-in-time value, unlike the existing monotonic counters.
"""

from __future__ import annotations

from csi_ingester.metrics import MetricsRegistry


def test_counters_only_render_as_before():
    reg = MetricsRegistry()
    reg.inc("csi.healthz.calls")
    reg.inc("csi.healthz.calls", 2)
    body = reg.render_prometheus()
    assert "# TYPE csi_healthz_calls counter" in body
    assert "csi_healthz_calls 3" in body


def test_set_gauge_renders_as_gauge_type():
    reg = MetricsRegistry()
    reg.set_gauge("csi.rss.seconds_since_fetch", 12.5)
    body = reg.render_prometheus()
    assert "# TYPE csi_rss_seconds_since_fetch gauge" in body
    assert "csi_rss_seconds_since_fetch 12.5" in body


def test_set_gauge_overwrites_not_accumulates():
    reg = MetricsRegistry()
    reg.set_gauge("csi.rss.seconds_since_fetch", 100.0)
    reg.set_gauge("csi.rss.seconds_since_fetch", 5.0)
    body = reg.render_prometheus()
    assert "csi_rss_seconds_since_fetch 5.0" in body
    assert "csi_rss_seconds_since_fetch 100.0" not in body


def test_empty_registry_renders_empty_string():
    reg = MetricsRegistry()
    assert reg.render_prometheus() == ""
