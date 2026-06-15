"""Tests for ``ProcessTurnAdapter.execute``'s timeout resolution after the
2026-06-14 liveness redesign.

The adapter no longer kills a turn at an arbitrary tier wall-clock cap. The
default control is now the idle / no-progress watchdog (``timeout_policy.
LivenessWatchdog``): a turn is killed only when it shows no sign of life past
the idle threshold while no tool is in flight, with a very high absolute
backstop for a fully-wedged process. An EXPLICIT per-request
``turn_timeout_seconds`` (a cron's per-job budget) or the legacy
``UA_PROCESS_TURN_TIMEOUT_SECONDS`` env is preserved as an *additional* hard cap
on top of the idle kill — never the implicit tier default that used to kill
Simone's long turns.

This mirrors ``execution_engine.py::ProcessTurnAdapter.execute``'s resolution
block (which can't be imported without the SDK). Kept in sync with the
production code; the watchdog mechanics themselves are covered by
``test_liveness_watchdog.py``.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest


def _resolve_watchdog_params(
    request_metadata: Optional[dict[str, Any]],
) -> tuple[float, float, float]:
    """Mirror of execution_engine.py's watchdog parameter resolution.

    Returns ``(idle_kill_s, backstop_s, hard_cap_s)``.
    """
    from universal_agent.timeout_policy import (
        process_turn_absolute_backstop_seconds,
        process_turn_idle_kill_seconds,
        process_turn_timeout_seconds,
    )

    hard_cap_s = 0.0
    if isinstance(request_metadata, dict):
        raw = request_metadata.get("turn_timeout_seconds")
        if raw is not None:
            try:
                hard_cap_s = max(0.0, float(raw))
            except (TypeError, ValueError):
                hard_cap_s = 0.0
    if hard_cap_s <= 0:
        hard_cap_s = process_turn_timeout_seconds()  # legacy env escape hatch

    idle_kill_s = process_turn_idle_kill_seconds()
    backstop_s = process_turn_absolute_backstop_seconds()
    return idle_kill_s, backstop_s, hard_cap_s


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip the env vars that would otherwise perturb the resolver."""
    for var in (
        "UA_PROCESS_TURN_TIMEOUT_SECONDS",
        "UA_PROCESS_TURN_IDLE_KILL_SECONDS",
        "UA_PROCESS_TURN_ABSOLUTE_BACKSTOP_SECONDS",
    ):
        monkeypatch.delenv(var, raising=False)


def test_no_metadata_uses_idle_defaults_no_hard_cap(clean_env) -> None:
    """THE regression guard for the Simone daemon stall: with no explicit
    per-request budget there is NO hard wall-clock cap — the idle watchdog (with
    a generous default + a high backstop) governs instead."""
    idle_kill_s, backstop_s, hard_cap_s = _resolve_watchdog_params(None)
    assert hard_cap_s == 0.0, "no implicit tier wall-clock cap any more"
    assert idle_kill_s >= 300.0, f"idle default should be generous, got {idle_kill_s}"
    assert backstop_s >= 3600.0, f"backstop should be very high, got {backstop_s}"


def test_per_request_override_becomes_hard_cap(clean_env) -> None:
    _, _, hard_cap_s = _resolve_watchdog_params({"turn_timeout_seconds": 3600})
    assert hard_cap_s == 3600.0


def test_per_request_override_wins_over_legacy_env(
    clean_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UA_PROCESS_TURN_TIMEOUT_SECONDS", "900")
    _, _, hard_cap_s = _resolve_watchdog_params({"turn_timeout_seconds": 2400})
    assert hard_cap_s == 2400.0


def test_legacy_env_used_when_no_per_request(
    clean_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UA_PROCESS_TURN_TIMEOUT_SECONDS", "900")
    _, _, hard_cap_s = _resolve_watchdog_params(None)
    assert hard_cap_s == 900.0


def test_per_request_override_accepts_string(clean_env) -> None:
    # JSON round-trips can land as strings; the resolver should coerce.
    _, _, hard_cap_s = _resolve_watchdog_params({"turn_timeout_seconds": "1500"})
    assert hard_cap_s == 1500.0


def test_invalid_per_request_falls_back_to_no_hard_cap(clean_env) -> None:
    _, _, hard_cap_s = _resolve_watchdog_params(
        {"turn_timeout_seconds": "not-a-number"}
    )
    assert hard_cap_s == 0.0  # bad value ignored, idle watchdog governs


def test_zero_per_request_means_no_hard_cap(clean_env) -> None:
    _, _, hard_cap_s = _resolve_watchdog_params({"turn_timeout_seconds": 0})
    assert hard_cap_s == 0.0


def test_idle_and_backstop_env_overrides(
    clean_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UA_PROCESS_TURN_IDLE_KILL_SECONDS", "300")
    monkeypatch.setenv("UA_PROCESS_TURN_ABSOLUTE_BACKSTOP_SECONDS", "3600")
    idle_kill_s, backstop_s, _ = _resolve_watchdog_params(None)
    assert idle_kill_s == 300.0
    assert backstop_s == 3600.0


def test_real_execution_engine_signature_accepts_kwarg() -> None:
    """Importing the real adapter is gated on optional deps. Just verify the
    public signature still has the request_metadata kwarg so cron_service's call
    site doesn't bit-rot."""
    pytest.importorskip("claude_agent_sdk", reason="SDK not installed")
    import inspect

    from universal_agent.execution_engine import ProcessTurnAdapter

    sig = inspect.signature(ProcessTurnAdapter.execute)
    assert "request_metadata" in sig.parameters
