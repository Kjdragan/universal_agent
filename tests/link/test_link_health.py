"""Phase 2a tests for the Link startup health probe.

Covers:
  - Skipped + ok=True when master switch is off (stub mode).
  - auth_status failure → snapshot.error set, ok=False, no raise.
  - auth_status returns unauthenticated → snapshot.error.code='auth_unauthenticated'.
  - payment-methods empty → snapshot.error.code='no_payment_methods'.
  - All checks pass → snapshot.ok=True with payment_methods_count > 0.
  - last_probe() reflects the most recent run.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from universal_agent.services import link_health
from universal_agent.tools import link_bridge


@pytest.fixture(autouse=True)
def reset_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setenv("UA_LINK_AUDIT_PATH", str(audit))
    for var in list(os.environ):
        if var.startswith(("UA_LINK_", "UA_ENABLE_LINK")) and var != "UA_LINK_AUDIT_PATH":
            monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("LINK_AUTH_BLOB", raising=False)

    link_bridge._AUTH_SEED_STATUS.clear()
    link_bridge._AUTH_SEED_STATUS.update({"applied": False, "path": None, "reason": None})
    link_health._LAST_PROBE.update(
        {
            "ran": False,
            "ts": None,
            "ok": False,
            "mode": "unknown",
            "auth_seed": None,
            "auth_status": None,
            "payment_methods_count": 0,
            "error": None,
        }
    )


def test_probe_skipped_in_stub_mode():
    snap = link_health.run_link_health_probe()
    assert snap["ran"] is True
    assert snap["mode"] == "stub"
    assert snap["ok"] is True
    assert snap["error"] is None
    assert link_health.last_probe()["ok"] is True


def test_probe_auth_status_failure(monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setattr(
        link_bridge,
        "auth_status",
        lambda caller="ops": {
            "ok": False,
            "error": {"code": "cli_not_found", "message": "no binary"},
            "data": None,
            "audit_id": "x",
            "mode": "test",
        },
    )
    snap = link_health.run_link_health_probe()
    assert snap["ok"] is False
    assert snap["error"]["code"] == "cli_not_found"


def test_probe_unauthenticated(monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setattr(
        link_bridge,
        "auth_status",
        lambda caller="ops": {
            "ok": True,
            "data": {"authenticated": False},
            "error": None,
            "audit_id": "x",
            "mode": "test",
        },
    )
    snap = link_health.run_link_health_probe()
    assert snap["ok"] is False
    assert snap["error"]["code"] == "auth_unauthenticated"


def test_probe_empty_wallet(monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setattr(
        link_bridge,
        "auth_status",
        lambda caller="ops": {
            "ok": True,
            "data": {"authenticated": True},
            "error": None,
            "audit_id": "x",
            "mode": "test",
        },
    )
    monkeypatch.setattr(
        link_bridge,
        "list_payment_methods",
        lambda caller="ops": {
            "ok": True,
            "data": {"payment_methods": []},
            "error": None,
            "audit_id": "y",
            "mode": "test",
        },
    )
    snap = link_health.run_link_health_probe()
    assert snap["ok"] is False
    assert snap["error"]["code"] == "no_payment_methods"


def test_probe_full_success(monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setattr(
        link_bridge,
        "auth_status",
        lambda caller="ops": {
            "ok": True,
            "data": {"authenticated": True},
            "error": None,
            "audit_id": "x",
            "mode": "test",
        },
    )
    monkeypatch.setattr(
        link_bridge,
        "list_payment_methods",
        lambda caller="ops": {
            "ok": True,
            "data": {"payment_methods": [{"id": "csmrpd_x", "last4": "4242"}]},
            "error": None,
            "audit_id": "y",
            "mode": "test",
        },
    )
    snap = link_health.run_link_health_probe()
    assert snap["ok"] is True
    assert snap["payment_methods_count"] == 1
    assert snap["auth_status"]["authenticated"] is True
    assert snap["error"] is None


def test_last_probe_reflects_most_recent_run(monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")

    monkeypatch.setattr(
        link_bridge,
        "auth_status",
        lambda caller="ops": {
            "ok": False,
            "error": {"code": "first_fail", "message": "x"},
            "data": None,
            "audit_id": "x",
            "mode": "test",
        },
    )
    link_health.run_link_health_probe()
    assert link_health.last_probe()["error"]["code"] == "first_fail"

    monkeypatch.setattr(
        link_bridge,
        "auth_status",
        lambda caller="ops": {
            "ok": True,
            "data": {"authenticated": True},
            "error": None,
            "audit_id": "x",
            "mode": "test",
        },
    )
    monkeypatch.setattr(
        link_bridge,
        "list_payment_methods",
        lambda caller="ops": {
            "ok": True,
            "data": {"payment_methods": [{"id": "x"}]},
            "error": None,
            "audit_id": "y",
            "mode": "test",
        },
    )
    link_health.run_link_health_probe()
    assert link_health.last_probe()["ok"] is True
    assert link_health.last_probe()["error"] is None
