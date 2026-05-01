"""Phase 2c tests for the Link reconciler service.

Covers:
  - Disabled when UA_ENABLE_LINK=0 (returns ran=False).
  - Disabled when UA_LINK_RECONCILER_DISABLED=1.
  - Empty audit → ran=True with checked=0.
  - Skips spend requests whose last observed status is terminal.
  - Invokes retrieve for non-terminal candidates and counts transitions.
  - Bounded by max_per_tick.
  - Errors counted, not raised.
  - Honors lookback window — stale entries excluded.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from universal_agent.services import link_reconciler
from universal_agent.tools import link_bridge


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setenv("UA_LINK_AUDIT_PATH", str(audit))
    for var in list(os.environ):
        if var.startswith(("UA_LINK_", "UA_ENABLE_LINK")) and var != "UA_LINK_AUDIT_PATH":
            monkeypatch.delenv(var, raising=False)
    return tmp_path


def _append(audit: Path, entry: dict) -> None:
    audit.parent.mkdir(parents=True, exist_ok=True)
    with audit.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


def test_reconciler_disabled_when_link_off(isolated):
    res = link_reconciler.reconcile_once()
    assert res == {"ran": False, "reason": "disabled"}


def test_reconciler_disabled_when_explicit_kill(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setenv("UA_LINK_RECONCILER_DISABLED", "1")
    res = link_reconciler.reconcile_once()
    assert res == {"ran": False, "reason": "disabled"}


def test_reconciler_empty_audit(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    res = link_reconciler.reconcile_once()
    assert res["ran"] is True
    assert res["checked"] == 0


def test_reconciler_skips_terminal_ids(isolated, monkeypatch):
    """A spend request whose last audit entry has status='approved' is skipped."""
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    audit = isolated / "audit.jsonl"
    now = time.time()
    _append(audit, {"event": "create_attempt", "spend_request_id": "lsrq_done",
                    "ts": now - 100, "status": "approved"})

    called = []

    def fake_retrieve(*, caller, spend_request_id, include_card=False):
        called.append(spend_request_id)
        return {"ok": True, "data": {"id": spend_request_id, "status": "approved"},
                "error": None, "audit_id": "x", "mode": "test"}

    monkeypatch.setattr(link_bridge, "retrieve_spend_request", fake_retrieve)

    res = link_reconciler.reconcile_once()
    assert res["ran"] is True
    assert res["checked"] == 0
    assert called == []


def test_reconciler_polls_non_terminal_and_counts_transition(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    audit = isolated / "audit.jsonl"
    now = time.time()
    _append(audit, {"event": "create_attempt", "spend_request_id": "lsrq_pending",
                    "ts": now - 60})  # no terminal status

    def fake_retrieve(*, caller, spend_request_id, include_card=False):
        return {"ok": True, "data": {"id": spend_request_id, "status": "approved"},
                "error": None, "audit_id": "x", "mode": "test"}

    monkeypatch.setattr(link_bridge, "retrieve_spend_request", fake_retrieve)

    res = link_reconciler.reconcile_once()
    assert res["checked"] == 1
    assert res["transitioned_to_terminal"] == 1


def test_reconciler_bounded_by_max_per_tick(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    audit = isolated / "audit.jsonl"
    now = time.time()
    for i in range(15):
        _append(audit, {"event": "create_attempt", "spend_request_id": f"lsrq_{i}",
                        "ts": now - (60 - i)})  # ascending ts

    def fake_retrieve(*, caller, spend_request_id, include_card=False):
        return {"ok": True, "data": {"id": spend_request_id, "status": "pending_approval"},
                "error": None, "audit_id": "x", "mode": "test"}

    monkeypatch.setattr(link_bridge, "retrieve_spend_request", fake_retrieve)

    res = link_reconciler.reconcile_once(max_per_tick=5)
    assert res["candidates"] == 15
    assert res["checked"] == 5
    assert res["transitioned_to_terminal"] == 0


def test_reconciler_counts_errors(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    audit = isolated / "audit.jsonl"
    now = time.time()
    _append(audit, {"event": "create_attempt", "spend_request_id": "lsrq_err", "ts": now})

    def fake_retrieve(*, caller, spend_request_id, include_card=False):
        return {"ok": False, "error": {"code": "cli_timeout", "message": "x"},
                "data": None, "audit_id": "x", "mode": "test"}

    monkeypatch.setattr(link_bridge, "retrieve_spend_request", fake_retrieve)

    res = link_reconciler.reconcile_once()
    assert res["checked"] == 1
    assert res["errors"] == 1


def test_reconciler_honors_lookback(isolated, monkeypatch):
    """Audit entries older than lookback_hours are excluded."""
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    audit = isolated / "audit.jsonl"
    now = time.time()
    _append(audit, {"event": "create_attempt", "spend_request_id": "lsrq_old",
                    "ts": now - (100 * 3600)})
    _append(audit, {"event": "create_attempt", "spend_request_id": "lsrq_new",
                    "ts": now - (3 * 3600)})

    seen = []

    def fake_retrieve(*, caller, spend_request_id, include_card=False):
        seen.append(spend_request_id)
        return {"ok": True, "data": {"id": spend_request_id, "status": "pending_approval"},
                "error": None, "audit_id": "x", "mode": "test"}

    monkeypatch.setattr(link_bridge, "retrieve_spend_request", fake_retrieve)

    res = link_reconciler.reconcile_once(lookback_hours=48)
    assert "lsrq_new" in seen
    assert "lsrq_old" not in seen
    assert res["candidates"] == 1


def test_reconciler_dedupes_by_spend_request_id(isolated, monkeypatch):
    """Multiple audit rows for the same id only count as one candidate."""
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    audit = isolated / "audit.jsonl"
    now = time.time()
    for _ in range(5):
        _append(audit, {"event": "create_attempt", "spend_request_id": "lsrq_dup", "ts": now})

    def fake_retrieve(*, caller, spend_request_id, include_card=False):
        return {"ok": True, "data": {"id": spend_request_id, "status": "pending_approval"},
                "error": None, "audit_id": "x", "mode": "test"}

    monkeypatch.setattr(link_bridge, "retrieve_spend_request", fake_retrieve)

    res = link_reconciler.reconcile_once()
    assert res["candidates"] == 1
    assert res["checked"] == 1


def test_reconciler_swallows_exceptions(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    audit = isolated / "audit.jsonl"
    _append(audit, {"event": "create_attempt", "spend_request_id": "lsrq_x", "ts": time.time()})

    def boom(*a, **kw):
        raise RuntimeError("simulated CLI crash")

    monkeypatch.setattr(link_bridge, "retrieve_spend_request", boom)

    # Must not raise.
    res = link_reconciler.reconcile_once()
    assert res["errors"] == 1
