"""Unit tests for the proactive health notifier.

The in-process heartbeat pre-flight (``run_pre_flight_check`` / ``_notify_critical``)
was retired in S5 Phase C (the compute moved to the deploy-independent systemd
timer + ``send_critical_digest``; the heartbeat now only reads the snapshot).
What remains here is the still-live surface: the manual verification ping
(``send_test_critical_email``), the shared mailer-acquire helpers
(``_acquire_agentmail_service`` / ``_construct_started_agentmail_service`` /
``_resolve_agentmail_service_via_gateway``), the ``_within_cooldown`` primitive,
and the cooldown default.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from universal_agent.services import proactive_health_notifier as notifier
from universal_agent.services.proactive_health_notifier import (
    DEFAULT_COOLDOWN_SECONDS,
    KEVIN_EMAIL,
    _within_cooldown,
)


@pytest.fixture
def fake_agentmail():
    mock = AsyncMock()
    mock.send_email = AsyncMock(return_value={"message_id": "abc", "status": "sent"})
    return mock


# === dedup / cooldown helper ===


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


def test_default_cooldown_is_6h():
    assert DEFAULT_COOLDOWN_SECONDS == 21600


# === hermetic guard ===
#
# Never build a real AgentMailService in unit tests — the construct-path tests
# below override this with their own stub via ``monkeypatch.setattr``.


@pytest.fixture(autouse=True)
def _hermetic_no_real_agentmail_construct(monkeypatch):
    async def _none():
        return None

    monkeypatch.setattr(notifier, "_construct_started_agentmail_service", _none)
    yield


def _fresh_mail_stub(message_id: str = "fresh-1"):
    mock = AsyncMock()
    mock.send_email = AsyncMock(return_value={"message_id": message_id, "status": "sent"})
    mock.shutdown = AsyncMock()
    return mock


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


# === Mailer-acquire helpers (shared by send_critical_digest + the test ping) ===


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
