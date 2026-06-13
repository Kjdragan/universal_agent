"""Tests for the ZAI status aggregator and the gateway control endpoints.

Verifies (a) the dashboard status payload aggregates events+snapshot+control
and fails soft when sources are missing, and (b) the two new ops endpoints
exist, are auth-gated, dispatch each lever action, and validate input.
"""

from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_ZAI_CONTROL_PATH", str(tmp_path / "zai_control.json"))
    monkeypatch.setenv("UA_ZAI_INFERENCE_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("UA_ZAI_EVENTS_PATH", str(tmp_path / "events.jsonl"))
    from universal_agent.services import zai_control

    zai_control._invalidate_cache()
    yield tmp_path
    zai_control._invalidate_cache()


# ── Status aggregator ───────────────────────────────────────────────────────


def test_build_status_empty_sources_fails_soft(isolated_paths):
    from universal_agent.services.zai_status import build_status

    status = build_status()
    assert "events" in status and "snapshot" in status and "control" in status
    assert status["events"]["available"] is False
    assert status["control"]["intervention_level"] == 0
    # Level presets exposed for the dashboard's ladder UI.
    assert set(status["level_presets"]) >= {"0", "4"}


def test_build_status_aggregates_events_by_tier(isolated_paths):
    import time

    from universal_agent.services.zai_status import build_status

    now = time.time()
    events = [
        {"ts": now - 5, "category": "rate_limited_429", "model": "glm-5-turbo", "fup_texted": True, "caller": "x/llm_classifier.py"},
        {"ts": now - 5, "category": "ok", "model": "glm-5-turbo"},
        {"ts": now - 5, "category": "ok", "model": "glm-4.5-air"},
    ]
    (isolated_paths / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events))

    status = build_status()
    ev = status["events"]
    assert ev["available"] is True
    w1 = ev["windows"]["1m"]
    assert w1["total"] == 3 and w1["r429"] == 1
    assert w1["fup_texted"] == 1
    # glm-5-turbo collapses to the sonnet tier.
    assert "sonnet" in w1["tiers"]
    assert w1["tiers"]["sonnet"]["r429"] == 1


def test_build_status_reads_snapshot_caps(isolated_paths):
    from universal_agent.services.zai_status import build_status

    (isolated_paths / "state.json").write_text(json.dumps({
        "tiers": {"opus": {"cap": 1}, "sonnet": {"cap": 2}},
        "total_429s_exhausted": 3,
        "pid": 1234,
    }))
    status = build_status()
    assert status["snapshot"]["tier_caps"]["opus"] == 1
    assert status["snapshot"]["total_429s_exhausted"] == 3


def test_build_status_surfaces_control_state(isolated_paths):
    from universal_agent.services import zai_control
    from universal_agent.services.zai_status import build_status

    zai_control.apply_level(4, reason="emergency")
    zai_control._invalidate_cache()
    status = build_status()
    assert status["control"]["global_pause_active"] is True
    assert status["control"]["intervention_level"] == 4


# ── Gateway endpoints ───────────────────────────────────────────────────────


def _client(monkeypatch):
    """FastAPI TestClient over the gateway app, with ops-auth neutralized so
    these tests target the lever logic (auth is covered by its own tests)."""
    from fastapi.testclient import TestClient

    gateway_server = importlib.import_module("universal_agent.gateway_server")
    monkeypatch.setattr(gateway_server, "_require_ops_auth", lambda *a, **k: None)
    return TestClient(gateway_server.app), gateway_server


def test_control_endpoint_requires_auth(isolated_paths):
    """Sanity: WITHOUT neutralizing auth, the control endpoint is gated
    (the test env has an ops token set, so an unauthenticated call is 401)."""
    from fastapi.testclient import TestClient

    gateway_server = importlib.import_module("universal_agent.gateway_server")
    client = TestClient(gateway_server.app)
    resp = client.post("/api/v1/ops/zai/control", json={"action": "clear"})
    assert resp.status_code == 401


def test_zai_endpoints_are_registered():
    gateway_server = importlib.import_module("universal_agent.gateway_server")
    routes = {getattr(r, "path", None) for r in gateway_server.app.routes}
    assert "/api/v1/ops/zai/status" in routes
    assert "/api/v1/ops/zai/control" in routes


def test_status_endpoint_returns_payload(isolated_paths, monkeypatch):
    client, _ = _client(monkeypatch)
    resp = client.get("/api/v1/ops/zai/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "events" in body and "control" in body


def test_control_endpoint_set_level(isolated_paths, monkeypatch):
    client, _ = _client(monkeypatch)
    from universal_agent.services import zai_control

    resp = client.post("/api/v1/ops/zai/control", json={"action": "set_level", "level": 4})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["control"]["intervention_level"] == 4
    zai_control._invalidate_cache()
    assert zai_control.is_globally_paused()[0] is True


def test_control_endpoint_clear(isolated_paths, monkeypatch):
    client, _ = _client(monkeypatch)
    from universal_agent.services import zai_control

    client.post("/api/v1/ops/zai/control", json={"action": "set_level", "level": 4})
    resp = client.post("/api/v1/ops/zai/control", json={"action": "clear"})
    assert resp.status_code == 200
    zai_control._invalidate_cache()
    assert zai_control.is_globally_paused()[0] is False


def test_control_endpoint_set_tier_caps(isolated_paths, monkeypatch):
    client, _ = _client(monkeypatch)
    from universal_agent.services import zai_control

    resp = client.post("/api/v1/ops/zai/control", json={
        "action": "set_tier_caps", "overrides": {"opus": {"cap": 1, "max": 1}},
    })
    assert resp.status_code == 200
    zai_control._invalidate_cache()
    assert zai_control.effective_tier_cap("opus", ai_cap=3, tier_max=3) == 1


def test_control_endpoint_rejects_unknown_tier(isolated_paths, monkeypatch):
    client, _ = _client(monkeypatch)
    resp = client.post("/api/v1/ops/zai/control", json={
        "action": "set_tier_caps", "overrides": {"bogus": {"cap": 1}},
    })
    assert resp.status_code == 400


def test_control_endpoint_rejects_bad_action(isolated_paths, monkeypatch):
    client, _ = _client(monkeypatch)
    resp = client.post("/api/v1/ops/zai/control", json={"action": "self_destruct"})
    assert resp.status_code == 400


def test_control_endpoint_rejects_bad_level(isolated_paths, monkeypatch):
    client, _ = _client(monkeypatch)
    resp = client.post("/api/v1/ops/zai/control", json={"action": "set_level", "level": 99})
    assert resp.status_code == 400
