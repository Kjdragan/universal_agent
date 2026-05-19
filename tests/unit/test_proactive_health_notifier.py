"""Unit tests for the proactive health pre-flight notifier.

Locks the behavior described in the 2026-05-18 incident analysis:
the watchdog must run every heartbeat tick (no skip-mode dependency), email
Kevin on new critical findings, and dedup repeats within a 6h cooldown so a
stuck pipeline doesn't spam his inbox.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from universal_agent.services import proactive_health_notifier as notifier
from universal_agent.services.proactive_health_notifier import (
    DEFAULT_COOLDOWN_SECONDS,
    KEVIN_EMAIL,
    NOTIFICATION_KIND_PREFIX,
    _within_cooldown,
    run_pre_flight_check,
)


def _critical_payload(finding_id: str = "youtube_enrichment_coverage") -> dict:
    return {
        "overall_status": "critical",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "crons": [],
        "stale_tasks": {"count": 0, "samples": []},
        "parked_tasks": {"count": 0, "samples": []},
        "invariants": [
            {
                "finding_id": f"invariant:{finding_id}",
                "category": "proactive_health",
                "severity": "critical",
                "metric_key": finding_id,
                "observed_value": {"coverage_pct": 0.0, "total_events": 349},
                "title": "Test critical invariant",
                "recommendation": "fix it",
                "runbook_command": "sqlite3 ... SELECT ...",
                "metadata": {},
            }
        ],
    }


def _ok_payload() -> dict:
    return {
        "overall_status": "ok",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "crons": [],
        "stale_tasks": {"count": 0, "samples": []},
        "parked_tasks": {"count": 0, "samples": []},
        "invariants": [],
    }


@pytest.fixture
def fake_agentmail():
    mock = AsyncMock()
    mock.send_email = AsyncMock(return_value={"message_id": "abc", "status": "sent"})
    return mock


@pytest.fixture
def notifications_list() -> list:
    return []


@pytest.fixture
def add_notification_fn(notifications_list):
    """Mirror gateway_server._add_notification just enough for dedup tracking."""

    def _add(*, kind, title, message, summary=None, severity="info", requires_action=False, metadata=None, **_):
        record = {
            "kind": kind,
            "title": title,
            "message": message,
            "summary": summary,
            "severity": severity,
            "requires_action": requires_action,
            "metadata": dict(metadata or {}),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        notifications_list.append(record)
        return record

    return _add


# === sidecar artifact ===


@pytest.mark.asyncio
async def test_pre_flight_writes_sidecar_even_on_ok(tmp_path: Path, fake_agentmail, notifications_list, add_notification_fn):
    payload_returned = await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_ok_payload,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )
    sidecar = tmp_path / "work_products" / "proactive_health_latest.json"
    assert sidecar.exists()
    body = sidecar.read_text()
    assert "ok" in body
    assert payload_returned["overall_status"] == "ok"


@pytest.mark.asyncio
async def test_pre_flight_writes_sidecar_on_critical(tmp_path: Path, fake_agentmail, notifications_list, add_notification_fn):
    payload = _critical_payload()
    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=lambda: payload,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )
    sidecar = tmp_path / "work_products" / "proactive_health_latest.json"
    assert sidecar.exists()
    assert "youtube_enrichment_coverage" in sidecar.read_text()


# === email-on-critical ===


@pytest.mark.asyncio
async def test_critical_finding_emails_kevin(tmp_path, fake_agentmail, notifications_list, add_notification_fn):
    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_critical_payload,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )
    fake_agentmail.send_email.assert_awaited_once()
    call = fake_agentmail.send_email.call_args
    assert call.kwargs["to"] == KEVIN_EMAIL
    assert call.kwargs["force_send"] is True
    assert "CRITICAL" in call.kwargs["subject"]
    assert "youtube_enrichment_coverage" in call.kwargs["text"]
    # dedup record was inserted
    assert len(notifications_list) == 1
    assert notifications_list[0]["kind"].startswith(NOTIFICATION_KIND_PREFIX)
    assert notifications_list[0]["severity"] == "critical"


@pytest.mark.asyncio
async def test_no_email_when_overall_ok(tmp_path, fake_agentmail, notifications_list, add_notification_fn):
    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_ok_payload,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )
    fake_agentmail.send_email.assert_not_called()
    assert notifications_list == []


@pytest.mark.asyncio
async def test_warn_severity_does_not_trigger_email(tmp_path, fake_agentmail, notifications_list, add_notification_fn):
    payload = _critical_payload()
    payload["invariants"][0]["severity"] = "warn"
    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=lambda: payload,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )
    fake_agentmail.send_email.assert_not_called()


# === dedup / cooldown ===


@pytest.mark.asyncio
async def test_second_call_within_cooldown_suppresses_email(tmp_path, fake_agentmail, notifications_list, add_notification_fn):
    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_critical_payload,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )
    assert fake_agentmail.send_email.await_count == 1

    # Second tick, same finding — should be suppressed because the dedup
    # record is now in notifications_list and within the cooldown window.
    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_critical_payload,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )
    assert fake_agentmail.send_email.await_count == 1
    assert len(notifications_list) == 1  # no new dedup record either


@pytest.mark.asyncio
async def test_distinct_finding_ids_each_send_independently(tmp_path, fake_agentmail, notifications_list, add_notification_fn):
    """A critical for invariant A must not silence invariant B's first fire."""
    p = _critical_payload("invariant_a")
    p["invariants"].append({
        "finding_id": "invariant:invariant_b",
        "category": "proactive_health",
        "severity": "critical",
        "metric_key": "invariant_b",
        "title": "B",
        "recommendation": "fix B",
        "runbook_command": "ls",
        "observed_value": None,
        "metadata": {},
    })
    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=lambda: p,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )
    assert fake_agentmail.send_email.await_count == 2
    assert len(notifications_list) == 2
    kinds = {n["kind"] for n in notifications_list}
    assert any(k.endswith("invariant:invariant_a") for k in kinds)
    assert any(k.endswith("invariant:invariant_b") for k in kinds)


def test_within_cooldown_helper_respects_window():
    now = datetime.now(timezone.utc)
    notifs = [
        {
            "kind": "proactive_health_critical:test",
            "created_at": (now - timedelta(seconds=100)).isoformat(),
            "updated_at": (now - timedelta(seconds=100)).isoformat(),
        }
    ]
    # 100s ago is well within a 600s cooldown.
    assert _within_cooldown(
        kind="proactive_health_critical:test",
        cooldown_seconds=600,
        notifications_list=notifs,
    ) is True
    # Same record vs a 50s cooldown — outside.
    assert _within_cooldown(
        kind="proactive_health_critical:test",
        cooldown_seconds=50,
        notifications_list=notifs,
    ) is False
    # Different kind — not in cooldown for "test".
    assert _within_cooldown(
        kind="proactive_health_critical:other",
        cooldown_seconds=600,
        notifications_list=notifs,
    ) is False


def test_within_cooldown_ignores_malformed_timestamps():
    notifs = [
        {"kind": "proactive_health_critical:test", "created_at": "not-a-date"},
        {"kind": "proactive_health_critical:test"},  # no timestamps at all
    ]
    assert _within_cooldown(
        kind="proactive_health_critical:test",
        cooldown_seconds=600,
        notifications_list=notifs,
    ) is False


# === env-var disable ===


@pytest.mark.asyncio
async def test_disabled_via_env_returns_short_circuit(tmp_path, fake_agentmail, notifications_list, add_notification_fn, monkeypatch):
    monkeypatch.setenv("UA_HEARTBEAT_PROACTIVE_HEALTH_ENABLED", "0")
    result = await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_critical_payload,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )
    assert result == {"status": "disabled"}
    fake_agentmail.send_email.assert_not_called()
    # Sidecar should NOT exist when disabled.
    assert not (tmp_path / "work_products" / "proactive_health_latest.json").exists()


@pytest.mark.asyncio
async def test_email_disabled_via_env_still_writes_sidecar(tmp_path, fake_agentmail, notifications_list, add_notification_fn, monkeypatch):
    monkeypatch.setenv("UA_HEARTBEAT_PROACTIVE_HEALTH_EMAIL_CRITICAL", "0")
    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_critical_payload,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )
    fake_agentmail.send_email.assert_not_called()
    assert (tmp_path / "work_products" / "proactive_health_latest.json").exists()


# === resilience ===


@pytest.mark.asyncio
async def test_payload_builder_failure_does_not_crash(tmp_path, fake_agentmail, notifications_list, add_notification_fn):
    def _boom():
        raise RuntimeError("DB down")

    result = await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_boom,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )
    assert "error" in result
    assert "DB down" in result["error"]
    fake_agentmail.send_email.assert_not_called()


@pytest.mark.asyncio
async def test_send_email_failure_does_not_crash(tmp_path, fake_agentmail, notifications_list, add_notification_fn):
    fake_agentmail.send_email.side_effect = RuntimeError("smtp down")
    # Should NOT raise.
    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_critical_payload,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )
    # No dedup record should be created when send failed — we want to retry
    # next cycle, not silence ourselves.
    assert notifications_list == []


@pytest.mark.asyncio
async def test_no_agentmail_service_still_writes_sidecar(tmp_path):
    # Heartbeat boot before agentmail is initialized — must degrade gracefully.
    payload = await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_critical_payload,
        agentmail_service=None,
        notifications_list=None,
        add_notification_fn=None,
    )
    assert payload["overall_status"] == "critical"
    assert (tmp_path / "work_products" / "proactive_health_latest.json").exists()


def test_default_cooldown_is_6h():
    assert DEFAULT_COOLDOWN_SECONDS == 21600
