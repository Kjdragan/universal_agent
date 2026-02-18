from types import SimpleNamespace
from datetime import datetime, timezone

from universal_agent.heartbeat_service import HeartbeatService


class _ConnMgr:
    def __init__(self):
        self.session_connections = {}


def _session(session_id: str, runtime: dict):
    return SimpleNamespace(session_id=session_id, metadata={"runtime": runtime}, workspace_dir="/tmp")


def test_lock_reason_busy_session_wins():
    svc = HeartbeatService(gateway=SimpleNamespace(), connection_manager=_ConnMgr())
    svc.busy_sessions.add("s1")
    reason = svc._session_heartbeat_lock_reason(_session("s1", {}), now_ts=1000.0)
    assert reason == "heartbeat_busy"


def test_lock_reason_foreground_run_active():
    svc = HeartbeatService(gateway=SimpleNamespace(), connection_manager=_ConnMgr())
    reason = svc._session_heartbeat_lock_reason(
        _session("s2", {"active_foreground_runs": 1}),
        now_ts=1000.0,
    )
    assert reason == "foreground_run_active"


def test_lock_reason_foreground_connection_active():
    cm = _ConnMgr()
    cm.session_connections["s3"] = {"c1"}
    svc = HeartbeatService(gateway=SimpleNamespace(), connection_manager=cm)
    reason = svc._session_heartbeat_lock_reason(_session("s3", {}), now_ts=1000.0)
    assert reason == "foreground_connection_active"


def test_lock_reason_foreground_cooldown_active():
    svc = HeartbeatService(gateway=SimpleNamespace(), connection_manager=_ConnMgr())
    svc.foreground_cooldown_seconds = 1800
    recent = datetime.now(timezone.utc).isoformat()
    reason = svc._session_heartbeat_lock_reason(
        _session("s4", {"last_foreground_run_finished_at": recent}),
        now_ts=datetime.now(timezone.utc).timestamp(),
    )
    assert reason == "foreground_cooldown_active"
