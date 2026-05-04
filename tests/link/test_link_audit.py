"""Phase 1 tests for link_bridge audit log.

Covers:
  - Every entry point writes a JSONL record.
  - Guardrail blocks emit `guardrail_blocked` field.
  - No card PAN / CVC ever appears in audit entries.
  - Daily-window filter respects the 24h cutoff.
  - resolve_audit_path honors UA_LINK_AUDIT_PATH override.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from universal_agent.tools import link_bridge


VALID_CONTEXT = (
    "User initiated this purchase via the shopping assistant flow. "
    "Buying a single hardcover copy of 'Working in Public' for personal reading. "
    "Final amount includes shipping and tax."
)


@pytest.fixture
def isolated_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    audit_file = tmp_path / "link_audit.jsonl"
    monkeypatch.setenv("UA_LINK_AUDIT_PATH", str(audit_file))
    for var in list(os.environ):
        if var.startswith(("UA_LINK_", "UA_ENABLE_LINK")):
            if var != "UA_LINK_AUDIT_PATH":
                monkeypatch.delenv(var, raising=False)
    return audit_file


def _read_audit(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_resolve_audit_path_uses_override(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("UA_LINK_AUDIT_PATH", str(tmp_path / "custom.jsonl"))
    assert link_bridge.resolve_audit_path() == (tmp_path / "custom.jsonl").resolve()


def test_resolve_audit_path_default_under_agent_run_workspaces(monkeypatch):
    monkeypatch.delenv("UA_LINK_AUDIT_PATH", raising=False)
    path = link_bridge.resolve_audit_path()
    assert path.name == "link_audit.jsonl"
    assert path.parent.name == "AGENT_RUN_WORKSPACES"


def test_create_writes_audit_entry(isolated_audit, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setenv("UA_LINK_FORCE_STUB", "1")
    link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=3500,
    )
    entries = _read_audit(isolated_audit)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["event"] == "create_attempt"
    assert entry["caller"] == "test"
    assert entry["amount_cents"] == 3500
    assert entry["merchant_url"] == "https://press.stripe.com"
    assert entry["guardrail_blocked"] is None
    assert entry["mode"] == "stub"  # UA_LINK_FORCE_STUB=1 → stub mode
    assert entry["audit_id"].startswith("audit_")
    assert "ts" in entry
    assert "ts_iso" in entry


def test_blocked_create_writes_audit_with_guardrail(isolated_audit, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setenv("UA_LINK_FORCE_STUB", "1")
    monkeypatch.setenv("UA_LINK_MAX_AMOUNT_CENTS", "1000")
    link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=5000,
    )
    entries = _read_audit(isolated_audit)
    assert len(entries) == 1
    assert entries[0]["guardrail_blocked"] == "guardrail_per_call_cap"
    assert entries[0]["error"]["code"] == "guardrail_per_call_cap"


def test_disabled_master_writes_stub_mode_audit(isolated_audit):
    link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=3500,
    )
    entries = _read_audit(isolated_audit)
    assert len(entries) == 1
    assert entries[0]["mode"] == "stub"
    assert entries[0]["guardrail_blocked"] == "guardrail_disabled"


def test_retrieve_writes_audit(isolated_audit, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setenv("UA_LINK_FORCE_STUB", "1")
    link_bridge.retrieve_spend_request(
        caller="test", spend_request_id="lsrq_x", include_card=True
    )
    entries = _read_audit(isolated_audit)
    assert len(entries) == 1
    assert entries[0]["event"] == "retrieve_attempt"
    assert entries[0]["spend_request_id"] == "lsrq_x"
    assert entries[0]["include_card"] is True


def test_list_payment_methods_writes_audit(isolated_audit, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setenv("UA_LINK_FORCE_STUB", "1")
    link_bridge.list_payment_methods(caller="test")
    entries = _read_audit(isolated_audit)
    assert len(entries) == 1
    assert entries[0]["event"] == "payment_methods_list"


def test_mpp_pay_writes_audit(isolated_audit, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setenv("UA_LINK_FORCE_STUB", "1")
    link_bridge.mpp_pay(
        caller="test",
        spend_request_id="lsrq_x",
        url="https://example.com/pay",
        method="POST",
        data={"amount": 100},
    )
    entries = _read_audit(isolated_audit)
    assert len(entries) == 1
    assert entries[0]["event"] == "mpp_pay_attempt"
    assert entries[0]["url"] == "https://example.com/pay"


def test_audit_never_contains_card_pan_or_cvc(isolated_audit, monkeypatch):
    """Card details must never appear in audit entries — even from the stub."""
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setenv("UA_LINK_FORCE_STUB", "1")
    link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=3500,
    )
    link_bridge.retrieve_spend_request(
        caller="test", spend_request_id="lsrq_x", include_card=True
    )

    raw = isolated_audit.read_text()
    # No 16-digit card number, no field named 'number' or 'cvc' in the log.
    assert '"number"' not in raw
    assert '"cvc"' not in raw
    assert "4242424242424242" not in raw


def test_daily_cap_window_excludes_old_entries(isolated_audit, monkeypatch):
    """Entries older than 24h must not count toward today's spent total."""
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setenv("UA_LINK_FORCE_STUB", "1")
    monkeypatch.setenv("UA_LINK_MAX_AMOUNT_CENTS", "10000")
    monkeypatch.setenv("UA_LINK_DAILY_BUDGET_CENTS", "5000")

    # Manually inject a stale entry 25h old.
    stale_ts = time.time() - (25 * 3600)
    with isolated_audit.open("a") as fh:
        fh.write(
            json.dumps(
                {
                    "audit_id": "audit_stale",
                    "event": "create_attempt",
                    "ts": stale_ts,
                    "amount_cents": 4900,
                    "guardrail_blocked": None,
                    "mode": "live",
                }
            )
            + "\n"
        )

    # New attempt at 4900 should still pass — stale doesn't count.
    result = link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=4900,
    )
    assert result["ok"] is True


def test_audit_entries_are_jsonl_one_per_line(isolated_audit, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setenv("UA_LINK_FORCE_STUB", "1")
    for _ in range(3):
        link_bridge.list_payment_methods(caller="test")

    lines = isolated_audit.read_text().splitlines()
    assert len(lines) == 3
    for line in lines:
        # Each line parses as JSON independently.
        json.loads(line)
