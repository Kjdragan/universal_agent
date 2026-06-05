"""Unit tests for the proactive health pre-flight notifier.

Locks the behavior described in the 2026-05-18 incident analysis:
the watchdog must run every heartbeat tick (no skip-mode dependency), email
Kevin on new critical findings, and dedup repeats within a 6h cooldown so a
stuck pipeline doesn't spam his inbox.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
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


# ─── proactive_health no longer writes Task Hub rows ─────────────────────────
# As of the 2026-06-03 surface change, proactive_health invariant findings are
# delivered via critical email (durable alert-of-record) + the live
# /api/v1/ops/proactive_health endpoint (dashboard surface). The notifier no
# longer takes a task_hub_emit_fn and creates ZERO Task Hub rows. The
# dedicated regression suite lives in
# tests/unit/test_proactive_health_no_task_hub_write.py.


# ─── Daemon-subprocess construct path (S1, 2026-06-04) ───────────────────────
# Bug (b): in the heartbeat daemon subprocess gateway_server._agentmail_service
# is the pristine module-level None, so the watchdog could never email. The fix
# stands up a fresh, short-lived AgentMailService when no gateway handle is
# available, then shuts it down. These tests pin that behavior — and a hermetic
# guard so the REAL AgentMailService (network) is never built in unit tests.


@pytest.fixture(autouse=True)
def _hermetic_no_real_agentmail_construct(monkeypatch):
    """Default: never build a real AgentMailService in unit tests.

    Keeps every existing test (which passes ``agentmail_service=None`` and
    expects the graceful-skip path) deterministic regardless of whether the
    developer's environment happens to have ``UA_AGENTMAIL_ENABLED=1`` +
    ``AGENTMAIL_API_KEY`` set. Tests that exercise the construct path override
    this with their own stub via ``monkeypatch.setattr``.
    """

    async def _none():
        return None

    monkeypatch.setattr(notifier, "_construct_started_agentmail_service", _none)
    yield


def _fresh_mail_stub(message_id: str = "fresh-1"):
    mock = AsyncMock()
    mock.send_email = AsyncMock(return_value={"message_id": message_id, "status": "sent"})
    mock.shutdown = AsyncMock()
    return mock


@pytest.mark.asyncio
async def test_constructs_fresh_agentmail_when_no_gateway_handle(
    tmp_path, monkeypatch, notifications_list, add_notification_fn
):
    """Daemon-subprocess path: gateway injected None and no in-process handle
    exists, so the notifier stands up a fresh mailer, emails, and shuts it
    down (the owned handle must be cleaned up)."""
    fresh = _fresh_mail_stub()

    async def _construct():
        return fresh

    monkeypatch.setattr(notifier, "_resolve_agentmail_service_via_gateway", lambda: None)
    monkeypatch.setattr(notifier, "_construct_started_agentmail_service", _construct)

    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_critical_payload,
        agentmail_service=None,  # daemon subprocess always passes None
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )

    fresh.send_email.assert_awaited_once()
    assert fresh.send_email.call_args.kwargs["to"] == KEVIN_EMAIL
    assert "CRITICAL" in fresh.send_email.call_args.kwargs["subject"]
    # The freshly-constructed (owned) handle must be shut down.
    fresh.shutdown.assert_awaited_once()
    assert len(notifications_list) == 1


@pytest.mark.asyncio
async def test_no_construct_attempt_when_no_criticals(
    tmp_path, monkeypatch, notifications_list, add_notification_fn
):
    """A healthy tick (no critical findings) must NOT pay the mailer-startup
    cost — construction is gated on there being something to send."""
    calls = {"n": 0}

    async def _construct():
        calls["n"] += 1
        return _fresh_mail_stub()

    monkeypatch.setattr(notifier, "_resolve_agentmail_service_via_gateway", lambda: None)
    monkeypatch.setattr(notifier, "_construct_started_agentmail_service", _construct)

    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_ok_payload,
        agentmail_service=None,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_acquire_prefers_gateway_handle_over_construct(monkeypatch, fake_agentmail):
    from universal_agent.services.proactive_health_notifier import (
        _acquire_agentmail_service,
    )

    monkeypatch.setattr(
        notifier, "_resolve_agentmail_service_via_gateway", lambda: fake_agentmail
    )
    called = {"n": 0}

    async def _construct():
        called["n"] += 1
        return _fresh_mail_stub()

    monkeypatch.setattr(notifier, "_construct_started_agentmail_service", _construct)

    svc, owned = await _acquire_agentmail_service()
    assert svc is fake_agentmail
    assert owned is False  # gateway handle is NOT owned by us
    assert called["n"] == 0  # construct never attempted when a handle exists


@pytest.mark.asyncio
async def test_acquire_constructs_and_marks_owned(monkeypatch):
    from universal_agent.services.proactive_health_notifier import (
        _acquire_agentmail_service,
    )

    fresh = _fresh_mail_stub()
    monkeypatch.setattr(notifier, "_resolve_agentmail_service_via_gateway", lambda: None)

    async def _construct():
        return fresh

    monkeypatch.setattr(notifier, "_construct_started_agentmail_service", _construct)

    svc, owned = await _acquire_agentmail_service()
    assert svc is fresh
    assert owned is True  # we built it → caller must shut it down


def test_resolver_finds_agentmail_on_main_module(monkeypatch, fake_agentmail):
    """The gateway runs as `python -m ...gateway_server`, so its
    _agentmail_service lands on sys.modules['__main__']; the resolver must look
    there (the importlib copy is a different, pristine module object)."""
    import sys as _sys

    from universal_agent.services.proactive_health_notifier import (
        _resolve_agentmail_service_via_gateway,
    )

    main_mod = _sys.modules.get("__main__")
    assert main_mod is not None
    monkeypatch.setattr(main_mod, "_agentmail_service", fake_agentmail, raising=False)
    assert _resolve_agentmail_service_via_gateway() is fake_agentmail


@pytest.mark.asyncio
async def test_send_test_critical_email_constructs_and_shuts_down(monkeypatch):
    from universal_agent.services.proactive_health_notifier import (
        send_test_critical_email,
    )

    fresh = _fresh_mail_stub("fresh-test-1")
    monkeypatch.setattr(notifier, "_resolve_agentmail_service_via_gateway", lambda: None)

    async def _construct():
        return fresh

    monkeypatch.setattr(notifier, "_construct_started_agentmail_service", _construct)

    result = await send_test_critical_email(agentmail_service=None, note="probe")
    assert result["sent"] is True
    fresh.send_email.assert_awaited_once()
    fresh.shutdown.assert_awaited_once()  # owned handle cleaned up
