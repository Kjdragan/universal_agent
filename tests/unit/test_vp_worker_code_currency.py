"""VP worker deploy-resilience: between-missions code-currency self-restart.

These guard the mechanism that replaced "deploy restarts the worker" — which
killed in-flight missions. A worker now self-restarts only BETWEEN missions
(top of `_tick`) when the deployed git SHA changed, so a deploy never
interrupts a running mission.
"""
from __future__ import annotations

from universal_agent.vp import worker_loop as wl


def _make_loop(
    *,
    start_version: str = "aaaa1111",
    last_check: float = 0.0,
    started: float = 0.0,
    max_uptime: int = 0,
) -> wl.VpWorkerLoop:
    """Build a VpWorkerLoop without running __init__ (which needs a DB/profile);
    only the attributes the decision method reads are set."""
    loop = object.__new__(wl.VpWorkerLoop)
    loop.vp_id = "vp.test"
    loop._start_code_version = start_version
    loop._last_code_version_check_monotonic = last_check
    loop._started_monotonic = started
    loop._max_uptime_seconds = max_uptime
    return loop


def test_restart_when_sha_changed(monkeypatch):
    monkeypatch.setattr(wl, "_deployed_code_version", lambda: "bbbb2222")
    monkeypatch.setattr(wl.time, "monotonic", lambda: 1000.0)
    loop = _make_loop(start_version="aaaa1111", last_check=0.0)
    assert loop._should_restart_for_code_currency() is True


def test_no_restart_when_sha_unchanged(monkeypatch):
    monkeypatch.setattr(wl, "_deployed_code_version", lambda: "aaaa1111")
    monkeypatch.setattr(wl.time, "monotonic", lambda: 1000.0)
    loop = _make_loop(start_version="aaaa1111", last_check=0.0)
    assert loop._should_restart_for_code_currency() is False


def test_no_restart_when_version_unknown_and_backstop_off(monkeypatch):
    # Fail-safe: couldn't read a version at startup + backstop disabled → never
    # self-restart (behave exactly as before the change).
    monkeypatch.setattr(wl, "_deployed_code_version", lambda: "cccc3333")
    monkeypatch.setattr(wl.time, "monotonic", lambda: 10_000.0)
    loop = _make_loop(start_version="", max_uptime=0)
    assert loop._should_restart_for_code_currency() is False


def test_restart_on_uptime_backstop_when_version_unknown(monkeypatch):
    monkeypatch.setattr(wl, "_deployed_code_version", lambda: "")
    monkeypatch.setattr(wl.time, "monotonic", lambda: 10_000.0)
    loop = _make_loop(start_version="", started=0.0, max_uptime=3600)
    assert loop._should_restart_for_code_currency() is True


def test_no_restart_under_uptime_backstop(monkeypatch):
    monkeypatch.setattr(wl, "_deployed_code_version", lambda: "")
    monkeypatch.setattr(wl.time, "monotonic", lambda: 100.0)
    loop = _make_loop(start_version="", started=0.0, max_uptime=3600)
    assert loop._should_restart_for_code_currency() is False


def test_sha_check_is_throttled(monkeypatch):
    """An idle worker must not spawn `git` every tick — the SHA check is gated
    by _CODE_VERSION_CHECK_INTERVAL_SECONDS."""
    calls = {"n": 0}

    def _ver() -> str:
        calls["n"] += 1
        return "bbbb2222"

    monkeypatch.setattr(wl, "_deployed_code_version", _ver)

    # First call: last_check=0, now=100 → interval elapsed → check runs.
    monkeypatch.setattr(wl.time, "monotonic", lambda: 100.0)
    loop = _make_loop(start_version="aaaa1111", last_check=0.0, max_uptime=0)
    assert loop._should_restart_for_code_currency() is True
    assert calls["n"] == 1

    # Second call 10s later (< 30s interval) → throttled, git NOT re-spawned.
    monkeypatch.setattr(wl.time, "monotonic", lambda: 110.0)
    loop._start_code_version = "aaaa1111"  # reset so a True wouldn't come from sha
    assert loop._should_restart_for_code_currency() is False
    assert calls["n"] == 1


def test_deployed_code_version_returns_str():
    # In a git checkout this is the HEAD SHA; otherwise "". Either way a str,
    # and it must never raise.
    assert isinstance(wl._deployed_code_version(), str)
