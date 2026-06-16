"""Daemon liveness + respawn (the 2026-06-16 8h Simone-outage fix).

Two halves of one contract:
  - heartbeat_service._touch_runtime_activity + the per-event refresh align the
    daemon idle reaper (_check_session_idle) with the LivenessWatchdog
    convention: a working/retrying daemon turn is a "sign of life", so it is
    never reaped on a wall-clock-ish idle threshold.
  - daemon_sessions.respawn_missing_heartbeat_sessions revives a reaped daemon
    within a scheduler tick — tearing down the stale SDK adapter (so the next
    turn builds a fresh one), clearing stale busy/wake state, with backoff and a
    circuit-breaker — so a kill self-heals instead of staying dead until restart.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from universal_agent.services.daemon_sessions import (
    DAEMON_ROLE_HEARTBEAT,
    DAEMON_ROLE_TODO,
    DaemonSessionManager,
)


# ── liveness: a fresh "sign of life" protects a daemon from the reaper ───────


def test_touch_runtime_activity_sets_last_activity():
    from universal_agent import heartbeat_service as hs

    sess = SimpleNamespace(metadata={"runtime": {}})
    hs._touch_runtime_activity(sess)
    assert sess.metadata["runtime"]["last_activity_at"]


def test_touch_runtime_activity_is_fail_soft_without_runtime():
    from universal_agent import heartbeat_service as hs

    sess = SimpleNamespace(metadata={})
    hs._touch_runtime_activity(sess)  # creates runtime, never raises
    assert "last_activity_at" in sess.metadata["runtime"]


def _idle_check_svc():
    """A HeartbeatService with only the attributes _check_session_idle reads."""
    from universal_agent.heartbeat_service import HeartbeatService

    svc = HeartbeatService.__new__(HeartbeatService)
    svc.connection_manager = None
    svc.wake_sessions = set()
    svc.wake_next_sessions = set()
    svc.active_sessions = {}
    return svc


def test_event_refresh_flips_a_stale_daemon_from_reaped_to_safe(monkeypatch):
    """The causal link: a stale daemon IS reaped, but a single sign-of-life
    refresh (what the per-event _touch_runtime_activity does during a turn) flips
    it to safe. This proves the refresh — not the function in isolation — is what
    protects a working daemon."""
    from universal_agent import heartbeat_service as hs
    from universal_agent.heartbeat_service import HeartbeatService

    monkeypatch.setattr(HeartbeatService, "_write_daemon_timeout_crash_report", lambda self, *a, **k: {}, raising=False)
    monkeypatch.setattr(HeartbeatService, "_notify_session_timeout", lambda self, *a, **k: None, raising=False)
    monkeypatch.setattr(
        HeartbeatService, "unregister_session", lambda self, sid: self.active_sessions.pop(sid, None)
    )
    svc = _idle_check_svc()
    sess = SimpleNamespace(
        session_id="daemon_simone_heartbeat",
        # Stale + a leaked active_run: the reaper bypasses active_runs for daemons.
        metadata={"runtime": {"active_runs": 1, "last_activity_at": "2020-01-01T00:00:00+00:00"}},
    )
    svc.active_sessions[sess.session_id] = sess
    assert svc._check_session_idle(sess) is True  # stale → reaped

    # Re-arm + emit a "sign of life" (the per-event refresh) → no longer reaped.
    svc.active_sessions[sess.session_id] = sess
    hs._touch_runtime_activity(sess)
    assert svc._check_session_idle(sess) is False


def test_stale_daemon_with_no_sign_of_life_is_still_reaped(monkeypatch):
    from universal_agent.heartbeat_service import HeartbeatService

    svc = _idle_check_svc()
    monkeypatch.setattr(svc, "_write_daemon_timeout_crash_report", lambda *a, **k: {}, raising=False)
    monkeypatch.setattr(svc, "_notify_session_timeout", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(
        HeartbeatService, "unregister_session", lambda self, sid: self.active_sessions.pop(sid, None)
    )
    monkeypatch.setenv("UA_DAEMON_IDLE_TIMEOUT", "1800")

    sess = SimpleNamespace(
        session_id="daemon_simone_heartbeat",
        metadata={"runtime": {"active_runs": 0, "last_activity_at": "2020-01-01T00:00:00+00:00"}},
    )
    svc.active_sessions[sess.session_id] = sess
    # Genuinely brain-dead (no sign of life for years) → reaped (the fix narrows
    # WHEN this fires; it does not disable the reaper) AND actually unregistered.
    assert svc._check_session_idle(sess) is True
    assert sess.session_id not in svc.active_sessions


# ── respawn supervisor ──────────────────────────────────────────────────────


class _FakeHB:
    def __init__(self):
        self.active_sessions: dict = {}
        self.busy_sessions: set = set()
        self.wake_sessions: set = set()
        self.wake_next_sessions: set = set()

    def register_session(self, session):
        self.active_sessions[session.session_id] = session


def _mgr(tmp_path, hb=None):
    hb = hb if hb is not None else _FakeHB()
    mgr = DaemonSessionManager(
        workspaces_dir=tmp_path, heartbeat_service=hb, agent_names=["simone"]
    )
    return mgr, hb


def _seed(mgr, session_id, role):
    sess = SimpleNamespace(
        session_id=session_id,
        workspace_dir="/tmp/ws",
        metadata={"runtime": {"active_runs": 3, "lifecycle_state": "terminal",
                              "terminal_reason": "cancelled",
                              "last_activity_at": "2020-01-01T00:00:00+00:00"}},
    )
    mgr._sessions[session_id] = sess
    mgr._session_ids[("simone", role)] = session_id
    return sess


async def _revive_recorder(calls):
    async def _revive(session):
        calls.append(session.session_id)
    return _revive


@pytest.mark.asyncio
async def test_respawn_revives_reaped_heartbeat(tmp_path, monkeypatch):
    monkeypatch.delenv("UA_DAEMON_RESPAWN_MIN_INTERVAL_SECONDS", raising=False)
    mgr, hb = _mgr(tmp_path)
    sess = _seed(mgr, "daemon_simone_heartbeat", DAEMON_ROLE_HEARTBEAT)  # reaped
    calls: list = []
    revive = await _revive_recorder(calls)

    out = await mgr.respawn_missing_heartbeat_sessions(revive_in_gateway=revive)

    assert out == ["daemon_simone_heartbeat"]
    assert "daemon_simone_heartbeat" in hb.active_sessions  # re-registered
    assert calls == ["daemon_simone_heartbeat"]  # adapter torn down + re-wired
    assert sess.metadata["runtime"]["lifecycle_state"] == "idle"  # terminal reset
    assert sess.metadata["runtime"]["terminal_reason"] is None
    # Leaked run counters cleared so the revived daemon isn't locked "run_active".
    assert sess.metadata["runtime"]["active_runs"] == 0
    assert sess.metadata["runtime"]["active_foreground_runs"] == 0
    assert sess.metadata["last_activity_at"]  # liveness refreshed


@pytest.mark.asyncio
async def test_respawn_clears_stale_busy_and_wake_state(tmp_path, monkeypatch):
    # A daemon reaped mid-heartbeat leaves stale busy/wake markers (keyed by id);
    # respawn must clear them or the revived daemon is locked out of its turn.
    monkeypatch.delenv("UA_DAEMON_RESPAWN_MIN_INTERVAL_SECONDS", raising=False)
    mgr, hb = _mgr(tmp_path)
    sid = "daemon_simone_heartbeat"
    _seed(mgr, sid, DAEMON_ROLE_HEARTBEAT)
    hb.busy_sessions.add(sid)
    hb.wake_sessions.add(sid)
    hb.wake_next_sessions.add(sid)

    await mgr.respawn_missing_heartbeat_sessions()

    assert sid not in hb.busy_sessions
    assert sid not in hb.wake_sessions
    assert sid not in hb.wake_next_sessions


@pytest.mark.asyncio
async def test_respawn_leaves_alive_session_untouched(tmp_path):
    mgr, hb = _mgr(tmp_path)
    sess = _seed(mgr, "daemon_simone_heartbeat", DAEMON_ROLE_HEARTBEAT)
    hb.active_sessions[sess.session_id] = sess  # still alive
    assert await mgr.respawn_missing_heartbeat_sessions() == []


@pytest.mark.asyncio
async def test_respawn_skips_todo_role(tmp_path):
    mgr, hb = _mgr(tmp_path)
    _seed(mgr, "daemon_simone_todo", DAEMON_ROLE_TODO)  # not scheduler-driven
    assert await mgr.respawn_missing_heartbeat_sessions() == []
    assert "daemon_simone_todo" not in hb.active_sessions


@pytest.mark.asyncio
async def test_respawn_backoff_prevents_hot_loop(tmp_path, monkeypatch):
    monkeypatch.delenv("UA_DAEMON_RESPAWN_MIN_INTERVAL_SECONDS", raising=False)  # default 120s
    mgr, hb = _mgr(tmp_path)
    _seed(mgr, "daemon_simone_heartbeat", DAEMON_ROLE_HEARTBEAT)

    assert await mgr.respawn_missing_heartbeat_sessions() == ["daemon_simone_heartbeat"]
    hb.active_sessions.clear()  # died again immediately
    assert await mgr.respawn_missing_heartbeat_sessions() == []  # backoff holds


@pytest.mark.asyncio
async def test_respawn_circuit_breaker_disables_after_max(tmp_path, monkeypatch):
    # After UA_DAEMON_MAX_RESPAWNS_PER_HOUR respawns, a persistently-dying daemon
    # is DISABLED (escalated) rather than respawned forever.
    monkeypatch.setenv("UA_DAEMON_RESPAWN_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("UA_DAEMON_MAX_RESPAWNS_PER_HOUR", "2")
    mgr, hb = _mgr(tmp_path)
    sid = "daemon_simone_heartbeat"
    _seed(mgr, sid, DAEMON_ROLE_HEARTBEAT)

    assert await mgr.respawn_missing_heartbeat_sessions() == [sid]
    hb.active_sessions.clear()
    assert await mgr.respawn_missing_heartbeat_sessions() == [sid]
    hb.active_sessions.clear()
    # 3rd time: window already has 2 >= max → breaker trips, no respawn.
    assert await mgr.respawn_missing_heartbeat_sessions() == []
    assert sid in mgr._respawn_disabled
    # Stays disabled on subsequent ticks.
    assert await mgr.respawn_missing_heartbeat_sessions() == []


@pytest.mark.asyncio
async def test_respawn_none_heartbeat_service_is_safe(tmp_path):
    mgr, _ = _mgr(tmp_path, hb=None)
    # Manager with no heartbeat service must not crash — returns [] (the guard
    # the review asked for, so a shutdown/odd state can't mis-respawn everything).
    assert await mgr.respawn_missing_heartbeat_sessions() == []
