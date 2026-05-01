"""Phase 3 tests for MPP support (mpp_decode + mpp routes + live-mode flip).

Covers:
  - mpp_decode in stub mode returns synthetic network_id.
  - mpp_decode validates non-empty challenge string.
  - mpp_decode dispatches to CLI in non-stub modes.
  - POST /api/link/mpp/decode 200 on success, 400 on guardrail/validation fail.
  - POST /api/link/mpp/pay forwards to bridge with all fields.
  - bridge_status reports mode=live when both gates open.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from universal_agent.tools import link_bridge


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UA_LINK_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    for var in list(os.environ):
        if var.startswith(("UA_LINK_", "UA_ENABLE_LINK")) and var != "UA_LINK_AUDIT_PATH":
            monkeypatch.delenv(var, raising=False)
    return tmp_path


# ── mpp_decode bridge ────────────────────────────────────────────────────────


def test_mpp_decode_disabled_returns_guardrail(isolated):
    res = link_bridge.mpp_decode(caller="ui", challenge="Payment id=...")
    assert res["ok"] is False
    assert res["error"]["code"] == "guardrail_disabled"


def test_mpp_decode_validates_empty_challenge(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setenv("UA_LINK_FORCE_STUB", "1")
    res = link_bridge.mpp_decode(caller="ui", challenge="")
    assert res["ok"] is False
    assert res["error"]["code"] == "validation_challenge"


def test_mpp_decode_stub_returns_synthetic(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setenv("UA_LINK_FORCE_STUB", "1")
    res = link_bridge.mpp_decode(caller="ui", challenge='Payment id="ch_001"')
    assert res["ok"] is True
    assert res["data"]["network_id"] == "stub_network_001"
    assert res["data"]["method"] == "stripe"


def test_mpp_decode_dispatches_to_cli(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return subprocess.CompletedProcess(
            argv, 0,
            json.dumps({"network_id": "net_real", "method": "stripe"}),
            "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        link_bridge.shutil, "which",
        lambda n: "/usr/bin/" + n if n == "link-cli" else None,
    )

    res = link_bridge.mpp_decode(
        caller="ui",
        challenge='Payment id="ch_001", method="stripe", request="..."',
    )
    assert res["ok"] is True
    assert res["data"]["network_id"] == "net_real"
    assert "decode" in captured["argv"]
    assert "--challenge" in captured["argv"]


def test_mpp_decode_writes_audit(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setenv("UA_LINK_FORCE_STUB", "1")
    link_bridge.mpp_decode(caller="ui", challenge='Payment id="x"')

    audit = link_bridge.resolve_audit_path()
    rows = [json.loads(line) for line in audit.read_text().splitlines() if line.strip()]
    assert any(r["event"] == "mpp_decode_attempt" for r in rows)


# ── HTTP routes ──────────────────────────────────────────────────────────────


@pytest.fixture
def app(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setenv("UA_LINK_FORCE_STUB", "1")
    monkeypatch.setenv("UA_LINK_DEFAULT_PAYMENT_METHOD_ID", "csmrpd_x")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from universal_agent.api.link_routes import router

    a = FastAPI()
    a.include_router(router)
    return TestClient(a)


def test_mpp_decode_endpoint_returns_200(app):
    res = app.post(
        "/api/link/mpp/decode",
        json={"challenge": 'Payment id="ch_001", method="stripe", request="..."'},
    )
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert "network_id" in res.json()["data"]


def test_mpp_decode_endpoint_validates(app):
    res = app.post("/api/link/mpp/decode", json={"challenge": ""})
    # Pydantic min_length=1 → 422
    assert res.status_code == 422


def test_mpp_pay_endpoint_returns_200(app):
    res = app.post(
        "/api/link/mpp/pay",
        json={
            "spend_request_id": "lsrq_001",
            "url": "https://climate.stripe.dev/api/contribute",
            "method": "POST",
            "data": {"amount": 100},
            "headers": {"X-Custom": "value"},
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["data"]["url"] == "https://climate.stripe.dev/api/contribute"


def test_mpp_pay_endpoint_validates_required_fields(app):
    res = app.post("/api/link/mpp/pay", json={"url": "https://x"})
    assert res.status_code == 422  # missing spend_request_id


# ── Live-mode flip ────────────────────────────────────────────────────────────


def test_live_mode_requires_both_gates(isolated, monkeypatch):
    """bridge_status reports mode=live only when both gates are open."""
    monkeypatch.setenv("UA_ENABLE_LINK", "1")

    # No live gate → test
    s = link_bridge.bridge_status()
    assert s["mode"] == "test"
    assert s["live_mode"] is False

    # Live gate but test_mode default-on → still test
    monkeypatch.setenv("UA_ENABLE_LINK_LIVE", "1")
    s = link_bridge.bridge_status()
    assert s["mode"] == "test"

    # Both gates → live
    monkeypatch.setenv("UA_LINK_TEST_MODE", "0")
    s = link_bridge.bridge_status()
    assert s["mode"] == "live"
    assert s["live_mode"] is True

    # FORCE_STUB overrides everything → stub
    monkeypatch.setenv("UA_LINK_FORCE_STUB", "1")
    s = link_bridge.bridge_status()
    assert s["mode"] == "stub"
