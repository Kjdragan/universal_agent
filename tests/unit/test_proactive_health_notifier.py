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


# === Skip-logging diagnostics (WS1, 2026-05-20) ===
#
# The 2026-05-19 production check revealed the notifier silently skipped emails
# when `_agentmail_service` was None — no log line, no counter. These tests pin
# the new diagnostic behavior so a future regression that re-silences the skip
# path fails loudly.


@pytest.fixture(autouse=True)
def _reset_skip_counter():
    """Each test starts with an empty consecutive-skip counter."""
    notifier._skipped_consecutive.clear()
    yield
    notifier._skipped_consecutive.clear()


@pytest.mark.asyncio
async def test_skip_logs_warning_when_agentmail_is_none(
    tmp_path, caplog, notifications_list, add_notification_fn
):
    import logging

    caplog.set_level(logging.WARNING, logger="universal_agent.services.proactive_health_notifier")
    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_critical_payload,
        agentmail_service=None,  # the production failure mode
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    skip_records = [r for r in warnings if "SKIPPED" in r.getMessage()]
    assert len(skip_records) >= 1, f"expected a SKIPPED warning, got {[r.getMessage() for r in warnings]}"
    msg = skip_records[0].getMessage()
    assert "agentmail_service" in msg
    assert "consecutive=1" in msg
    # Sidecar still written even when email path blocked.
    assert (tmp_path / "work_products" / "proactive_health_latest.json").exists()


@pytest.mark.asyncio
async def test_skip_counter_increments_across_consecutive_calls(
    tmp_path, notifications_list, add_notification_fn
):
    # Three consecutive blocked ticks should produce consecutive=1, 2, 3.
    for _ in range(3):
        await run_pre_flight_check(
            workspace_dir=tmp_path,
            payload_builder=_critical_payload,
            agentmail_service=None,
            notifications_list=notifications_list,
            add_notification_fn=add_notification_fn,
        )
    # The fake fingerprint from _critical_payload defaults to
    # "invariant:youtube_enrichment_coverage".
    fp = "invariant:youtube_enrichment_coverage"
    assert notifier._skipped_consecutive[fp] == 3


@pytest.mark.asyncio
async def test_skip_counter_resets_on_successful_send(
    tmp_path, fake_agentmail, notifications_list, add_notification_fn
):
    # First call with no agentmail → bumps counter.
    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_critical_payload,
        agentmail_service=None,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )
    fp = "invariant:youtube_enrichment_coverage"
    assert notifier._skipped_consecutive[fp] == 1

    # Second call with working agentmail → email sends, counter resets.
    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_critical_payload,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )
    fake_agentmail.send_email.assert_awaited_once()
    assert fp not in notifier._skipped_consecutive


@pytest.mark.asyncio
async def test_lazy_fallback_resolves_agentmail_via_gateway(
    tmp_path, monkeypatch, fake_agentmail, notifications_list, add_notification_fn
):
    """If the caller passed None but gateway_server._agentmail_service is set,
    the notifier should fall through and use it instead of skipping."""
    import sys
    import types

    fake_gateway = types.ModuleType("universal_agent.gateway_server")
    fake_gateway._agentmail_service = fake_agentmail
    monkeypatch.setitem(sys.modules, "universal_agent.gateway_server", fake_gateway)

    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_critical_payload,
        agentmail_service=None,  # passed None
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )
    # Lazy fallback found the gateway-side instance and used it.
    fake_agentmail.send_email.assert_awaited_once()
    assert len(notifications_list) == 1


# === Manual test endpoint helper (send_test_critical_email) ===


@pytest.mark.asyncio
async def test_send_test_critical_email_uses_agentmail_and_returns_sent(fake_agentmail):
    from universal_agent.services.proactive_health_notifier import (
        send_test_critical_email,
    )

    result = await send_test_critical_email(agentmail_service=fake_agentmail, note="ping")
    assert result["sent"] is True
    assert result["to"] == KEVIN_EMAIL
    assert result["subject"].startswith("[Proactive Health CRITICAL]")
    assert "[TEST]" in result["subject"]
    fake_agentmail.send_email.assert_awaited_once()
    call = fake_agentmail.send_email.call_args
    assert "[TEST]" in call.kwargs["text"]
    assert "ping" in call.kwargs["text"]


@pytest.mark.asyncio
async def test_send_test_critical_email_handles_send_failure(fake_agentmail):
    from universal_agent.services.proactive_health_notifier import (
        send_test_critical_email,
    )

    fake_agentmail.send_email.side_effect = RuntimeError("smtp down")
    result = await send_test_critical_email(agentmail_service=fake_agentmail)
    assert result["sent"] is False
    assert "smtp down" in result["reason"]


@pytest.mark.asyncio
async def test_send_test_critical_email_falls_back_via_gateway(
    monkeypatch, fake_agentmail
):
    """If no agentmail passed and gateway has it, fall back automatically."""
    import sys
    import types

    from universal_agent.services.proactive_health_notifier import (
        send_test_critical_email,
    )

    fake_gateway = types.ModuleType("universal_agent.gateway_server")
    fake_gateway._agentmail_service = fake_agentmail
    monkeypatch.setitem(sys.modules, "universal_agent.gateway_server", fake_gateway)

    result = await send_test_critical_email(agentmail_service=None)
    assert result["sent"] is True
    fake_agentmail.send_email.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_test_critical_email_returns_unsent_when_no_agentmail(monkeypatch):
    """No injected agentmail AND no gateway fallback → returns sent=False with reason."""
    import sys
    import types

    from universal_agent.services.proactive_health_notifier import (
        send_test_critical_email,
    )

    fake_gateway = types.ModuleType("universal_agent.gateway_server")
    fake_gateway._agentmail_service = None
    monkeypatch.setitem(sys.modules, "universal_agent.gateway_server", fake_gateway)

    result = await send_test_critical_email(agentmail_service=None)
    assert result["sent"] is False
    assert "agentmail_service=None" in result["reason"]


# ─── P0c: task_hub emission so critical findings persist past one tick ────────
# Background: PR #389 added per-finding email + 6h dedup. Email is necessary
# but not sufficient — emails get archived/ignored. Findings should also park
# a `needs_review` row in Task Hub so Simone has a persistent backlog of
# unresolved criticals. This closes the loop the original HEARTBEAT.md
# directive failed to close (Simone wasn't auto-emitting Task Hub rows for
# findings even though she had access to them).

@pytest.mark.asyncio
async def test_critical_finding_emits_task_hub_row(
    tmp_path, fake_agentmail, notifications_list, add_notification_fn
):
    """When task_hub_emit_fn is provided, the notifier passes each critical
    finding to it. Lets the caller wire actual Task Hub upsert without the
    notifier importing gateway_server / task_hub modules directly."""
    emitted: list[dict] = []

    def _emit(finding: dict) -> None:
        emitted.append(finding)

    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_critical_payload,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
        task_hub_emit_fn=_emit,
    )
    assert len(emitted) == 1
    assert emitted[0]["metric_key"] == "youtube_enrichment_coverage"
    assert emitted[0]["severity"] == "critical"


@pytest.mark.asyncio
async def test_task_hub_emit_fn_failure_does_not_crash(
    tmp_path, fake_agentmail, notifications_list, add_notification_fn
):
    """The notifier must never crash the heartbeat. If task_hub_emit_fn raises,
    it logs and continues. Email still gets sent."""
    def _emit_boom(finding: dict) -> None:
        raise RuntimeError("simulated task_hub DB locked")

    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_critical_payload,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
        task_hub_emit_fn=_emit_boom,
    )
    # Email still sent despite the emit failure.
    fake_agentmail.send_email.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_task_hub_emit_fn_is_backwards_compatible(
    tmp_path, fake_agentmail, notifications_list, add_notification_fn
):
    """Existing callers that don't pass task_hub_emit_fn keep working (the
    parameter is optional). Email still sent, sidecar still written."""
    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_critical_payload,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
        # task_hub_emit_fn omitted entirely
    )
    fake_agentmail.send_email.assert_awaited_once()
    assert (tmp_path / "work_products" / "proactive_health_latest.json").exists()


# ─── P3: warn-severity escalation via Task Hub (no email) ────────────────────


def _warn_payload(finding_id: str = "morning_briefing_freshness") -> dict:
    return {
        "overall_status": "warn",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "crons": [],
        "stale_tasks": {"count": 0, "samples": []},
        "parked_tasks": {"count": 0, "samples": []},
        "invariants": [
            {
                "finding_id": f"invariant:{finding_id}",
                "category": "proactive_health",
                "severity": "warn",
                "metric_key": finding_id,
                "observed_value": {"today": "2026-05-20"},
                "title": "Test warn invariant",
                "recommendation": "investigate",
                "runbook_command": "ls -la artifacts/",
                "metadata": {},
            }
        ],
    }


@pytest.fixture(autouse=True)
def _reset_warn_counter():
    """Each test starts with a fresh consecutive-warn counter."""
    notifier._consecutive_warns.clear()
    yield
    notifier._consecutive_warns.clear()


@pytest.mark.asyncio
async def test_single_warn_tick_does_not_escalate(
    tmp_path, fake_agentmail, notifications_list, add_notification_fn
):
    """A warn finding seen ONCE should NOT park a Task Hub row. The
    escalation threshold is 3 consecutive ticks by default — transient
    warns shouldn't pollute the backlog."""
    emitted: list[dict] = []
    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_warn_payload,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
        task_hub_emit_fn=emitted.append,
    )
    assert emitted == []  # warns don't emit on first sighting
    fake_agentmail.send_email.assert_not_called()  # warns never email


@pytest.mark.asyncio
async def test_warn_escalates_at_threshold(
    tmp_path, fake_agentmail, notifications_list, add_notification_fn, monkeypatch
):
    """A warn finding observed 3 consecutive ticks should park ONE Task Hub row
    on the 3rd tick — not before, and not again on tick 4+."""
    monkeypatch.setenv("UA_HEARTBEAT_PROACTIVE_HEALTH_WARN_ESCALATION_THRESHOLD", "3")
    emitted: list[dict] = []

    # Tick 1, 2 — should NOT emit
    for _ in range(2):
        await run_pre_flight_check(
            workspace_dir=tmp_path,
            payload_builder=_warn_payload,
            agentmail_service=fake_agentmail,
            notifications_list=notifications_list,
            add_notification_fn=add_notification_fn,
            task_hub_emit_fn=emitted.append,
        )
    assert emitted == []

    # Tick 3 — escalates
    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_warn_payload,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
        task_hub_emit_fn=emitted.append,
    )
    assert len(emitted) == 1
    assert emitted[0]["severity"] == "warn"
    assert emitted[0]["metric_key"] == "morning_briefing_freshness"

    # Tick 4, 5 — NOT re-emitted (Task Hub upsert handles persistence
    # on the consumer side; notifier only emits once per crossing).
    for _ in range(2):
        await run_pre_flight_check(
            workspace_dir=tmp_path,
            payload_builder=_warn_payload,
            agentmail_service=fake_agentmail,
            notifications_list=notifications_list,
            add_notification_fn=add_notification_fn,
            task_hub_emit_fn=emitted.append,
        )
    assert len(emitted) == 1  # still only the one


@pytest.mark.asyncio
async def test_warn_disappears_resets_counter(
    tmp_path, fake_agentmail, notifications_list, add_notification_fn, monkeypatch
):
    """If a warn fires twice then the issue resolves and it disappears, the
    counter must RESET. If the warn comes back later, it starts from 1 not
    from 3 — otherwise a flapping warn would immediately escalate on its
    second flare."""
    monkeypatch.setenv("UA_HEARTBEAT_PROACTIVE_HEALTH_WARN_ESCALATION_THRESHOLD", "3")
    emitted: list[dict] = []

    # Two warn ticks
    for _ in range(2):
        await run_pre_flight_check(
            workspace_dir=tmp_path,
            payload_builder=_warn_payload,
            agentmail_service=fake_agentmail,
            notifications_list=notifications_list,
            add_notification_fn=add_notification_fn,
            task_hub_emit_fn=emitted.append,
        )
    assert emitted == []

    # Resolution tick — no warn in payload
    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_ok_payload,  # imported at top of file
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
        task_hub_emit_fn=emitted.append,
    )
    # Counter for morning_briefing_freshness should be gone.
    assert "invariant:morning_briefing_freshness" not in notifier._consecutive_warns

    # Warn flares again — should be tick 1 of 3, not immediate escalation
    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_warn_payload,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
        task_hub_emit_fn=emitted.append,
    )
    assert emitted == []  # still no emission — counter restarted


@pytest.mark.asyncio
async def test_warn_escalation_independent_of_email_path(
    tmp_path, notifications_list, add_notification_fn, monkeypatch
):
    """Warn escalation must run even when the email channel is broken (no
    agentmail_service). Task Hub is the warn-tier escalation surface, not
    a fallback for blocked emails."""
    monkeypatch.setenv("UA_HEARTBEAT_PROACTIVE_HEALTH_WARN_ESCALATION_THRESHOLD", "2")
    emitted: list[dict] = []

    for _ in range(2):
        await run_pre_flight_check(
            workspace_dir=tmp_path,
            payload_builder=_warn_payload,
            agentmail_service=None,
            notifications_list=notifications_list,
            add_notification_fn=add_notification_fn,
            task_hub_emit_fn=emitted.append,
        )
    assert len(emitted) == 1
    assert emitted[0]["severity"] == "warn"


@pytest.mark.asyncio
async def test_warn_never_emails_even_at_escalation(
    tmp_path, fake_agentmail, notifications_list, add_notification_fn, monkeypatch
):
    """Warn escalation parks Task Hub rows but NEVER sends email. Warn-tier
    emails would noise out the critical-tier alerts Kevin needs to act on."""
    monkeypatch.setenv("UA_HEARTBEAT_PROACTIVE_HEALTH_WARN_ESCALATION_THRESHOLD", "2")
    emitted: list[dict] = []

    for _ in range(3):
        await run_pre_flight_check(
            workspace_dir=tmp_path,
            payload_builder=_warn_payload,
            agentmail_service=fake_agentmail,
            notifications_list=notifications_list,
            add_notification_fn=add_notification_fn,
            task_hub_emit_fn=emitted.append,
        )
    # Task Hub row created at tick 2 — but ZERO emails sent.
    assert len(emitted) >= 1
    fake_agentmail.send_email.assert_not_called()


@pytest.mark.asyncio
async def test_task_hub_emit_runs_even_when_email_skipped(
    tmp_path, notifications_list, add_notification_fn, caplog
):
    """If email plumbing is broken (no agentmail_service) but task_hub_emit_fn
    is wired, the Task Hub row should still be created. Email and task hub
    are independent escalation channels — losing one shouldn't lose both."""
    import logging
    caplog.set_level(logging.WARNING)
    emitted: list[dict] = []

    def _emit(finding: dict) -> None:
        emitted.append(finding)

    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_critical_payload,
        agentmail_service=None,  # no email path
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
        task_hub_emit_fn=_emit,
    )
    # Email path skipped (logged) but task_hub_emit_fn still fired
    assert len(emitted) == 1
    assert any("SKIPPED" in r.getMessage() for r in caplog.records)
