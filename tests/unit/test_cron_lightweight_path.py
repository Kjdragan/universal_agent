"""Lightweight cron path — validation + registration tests.

The lightweight cron path bypasses the heavyweight Claude-session bootstrap
(Composio session creation, capability snapshot injection, SOUL load,
session dossier registration) for pure-stdlib + sqlite3 housekeeping crons
whose ``!script`` body doesn't need an agent session. See
``plans/fix-2-lightweight-cron-path.md`` and
``docs/operations/operating_hours_dormancy.md``.

These tests cover the registration contract:

* ``_register_system_cron_job(lightweight=True, command="!script ...")``
  raises ``ValueError`` only when the command does not start with
  ``!script`` — including at registration time even when the cron
  service is disabled, so misconfigurations fail fast at startup.
* The ``simone_chat_auto_complete`` cron is wired with
  ``lightweight=True`` (string-grep guard, mirroring the existing
  dormancy-defaults test pattern).
"""
from __future__ import annotations

from pathlib import Path

import pytest

GATEWAY_SERVER = Path("src/universal_agent/gateway_server.py")


def test_register_lightweight_rejects_non_script_command() -> None:
    """A lightweight cron with an LLM-prompt command should fail fast."""
    from universal_agent import gateway_server

    with pytest.raises(ValueError, match="lightweight=True requires a `!script`"):
        gateway_server._register_system_cron_job(
            system_job="bad_lightweight",
            default_cron="*/1 * * * *",
            default_timezone="UTC",
            command="Tell me a joke",  # LLM prompt, not !script
            description="should be rejected at registration time",
            timeout_seconds=30,
            enabled=True,
            lightweight=True,
        )


def test_register_lightweight_accepts_script_command_when_disabled() -> None:
    """Valid lightweight registration returns None when service disabled,
    but does NOT raise — the !script validation passed."""
    from universal_agent import gateway_server

    # The service may or may not be initialised in the test environment;
    # either way a valid !script command should not raise. If the service
    # is unavailable, the function returns None; if it is available, it
    # registers the job. Both are acceptable for this contract test —
    # the assertion is just "no exception".
    try:
        result = gateway_server._register_system_cron_job(
            system_job="ut_lightweight_ok",
            default_cron="*/5 * * * *",
            default_timezone="UTC",
            command="!script universal_agent.scripts.simone_chat_auto_complete",
            description="unit test fixture",
            timeout_seconds=30,
            enabled=False,  # force the disabled short-circuit
            lightweight=True,
        )
    except ValueError:  # pragma: no cover — defensive
        pytest.fail("Valid lightweight=True + !script command should not raise")
    # `enabled=False` short-circuits, so we expect None regardless of
    # whether the cron service global is set.
    assert result is None


def test_simone_chat_auto_complete_registered_lightweight() -> None:
    """Guard: simone_chat_auto_complete is the canonical lightweight cron.

    The original 2026-05-19 incident (gateway /version timeouts blowing
    past the dashboard's 4 s client SLO) was caused by this 1-minute cron
    going through the heavyweight session bootstrap. The fix is wiring it
    as lightweight=True. If this guard fails, the regression has
    re-shipped.
    """
    src = GATEWAY_SERVER.read_text(encoding="utf-8")
    anchor = "def _ensure_simone_chat_autocomplete_cron_job"
    idx = src.find(anchor)
    assert idx != -1, f"{anchor} no longer exists — regression?"
    # Read the function body (next ~1200 chars is enough to span the call).
    body = src[idx : idx + 1200]
    assert 'system_job="simone_chat_auto_complete"' in body, (
        "system_job constant changed — regression?"
    )
    assert "lightweight=True" in body, (
        "simone_chat_auto_complete cron must be registered with "
        "lightweight=True. Removing this regresses the 2026-05-19 gateway "
        "/version timeout fix. See plans/fix-2-lightweight-cron-path.md."
    )
