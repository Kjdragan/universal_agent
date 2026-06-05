"""Unit tests for the standalone Mission Control sweeper launcher.

The launcher's whole job is correct ORDERING: bootstrap Infisical secrets
BEFORE importing/running ``run_sweeper_loop`` (its own docstring calls skipping
that "the #1 trap"), and gate on ``UA_MC_PHASE_1_ENABLED`` so a disabled phase
idles instead of spinning. These tests pin both invariants.

Monkeypatching note: the launcher does function-local ``from X import Y``
imports inside ``_run``, so patching the attribute on the *source module object*
(not a string path) is what the bound name resolves to at call time.
"""

from __future__ import annotations

import asyncio

import pytest

import universal_agent.infisical_loader as infisical_loader
import universal_agent.services.mission_control_db as mission_control_db
import universal_agent.services.mission_control_intelligence_sweeper as sweeper_mod
import universal_agent.services.mission_control_sweeper_main as launcher


def test_secrets_bootstrap_runs_before_sweeper_loop(monkeypatch):
    """initialize_runtime_secrets() must be called before run_sweeper_loop —
    otherwise the tier-1/tier-2 LLM lane runs keyless (the #1 trap)."""
    calls: list[str] = []

    def fake_init_secrets(*_args, **_kwargs):
        calls.append("secrets")
        return None

    async def fake_run_loop(stop_event):
        calls.append("loop")
        # Sanity: the launcher hands the loop an asyncio.Event stop signal.
        assert isinstance(stop_event, asyncio.Event)
        return None

    monkeypatch.setattr(infisical_loader, "initialize_runtime_secrets", fake_init_secrets)
    monkeypatch.setattr(sweeper_mod, "run_sweeper_loop", fake_run_loop)
    monkeypatch.setattr(mission_control_db, "is_phase_enabled", lambda _phase: True)

    asyncio.run(launcher._run())

    assert calls == ["secrets", "loop"], (
        f"secrets must bootstrap before the loop runs; got order {calls}"
    )


def test_phase_disabled_idles_without_starting_loop(monkeypatch):
    """When UA_MC_PHASE_1_ENABLED is unset (is_phase_enabled(1) False), the
    launcher must NOT start the loop — it idles awaiting a stop signal."""
    calls: list[str] = []

    monkeypatch.setattr(
        infisical_loader, "initialize_runtime_secrets", lambda *a, **k: calls.append("secrets")
    )

    async def fake_run_loop(_stop_event):  # pragma: no cover - must never run
        calls.append("loop")

    monkeypatch.setattr(sweeper_mod, "run_sweeper_loop", fake_run_loop)
    monkeypatch.setattr(mission_control_db, "is_phase_enabled", lambda _phase: False)

    async def _drive():
        # _run() idles on stop_event.wait() forever when disabled; bound it.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(launcher._run(), timeout=0.25)

    asyncio.run(_drive())

    assert "secrets" in calls, "secrets bootstrap still runs before the phase gate"
    assert "loop" not in calls, "disabled phase must not start run_sweeper_loop"
