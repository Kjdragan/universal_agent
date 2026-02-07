import os
import shutil
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from universal_agent import gateway_server
from universal_agent.gateway import GatewaySessionSummary

# Mock OpsService to avoid full gateway dependency chains in unit tests
# OR rely on the fact that lifespan will init a real OpsService with a real InProcessGateway pointing to tmp_path?
# Let's try to use the real one but pointing to tmp path.

@pytest.fixture
def client(tmp_path, monkeypatch):
    # Patch WORKSPACES_DIR to use tmp_path
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    
    # We must reset the global singletons to force re-init with new path
    monkeypatch.setattr(gateway_server, "_gateway", None)
    monkeypatch.setattr(gateway_server, "_ops_service", None)
    
    # Env vars
    monkeypatch.setenv("UA_GATEWAY_PORT", "0") # Avoid binding real port if it tried
    monkeypatch.setenv("UA_DISABLE_HEARTBEAT", "1")
    monkeypatch.setenv("UA_DISABLE_CRON", "1")
    
    with TestClient(gateway_server.app) as c:
        yield c

def _create_dummy_session(base_dir: Path, session_id: str, logs: list[str]):
    session_dir = base_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    if logs:
        (session_dir / "run.log").write_text("\n".join(logs), encoding="utf-8")
    return session_dir

def test_ops_list_sessions(client, tmp_path):
    # Create some dummy sessions on disk
    _create_dummy_session(tmp_path, "session_A", ["logA"])
    _create_dummy_session(tmp_path, "session_B", ["logB"])
    
    # The gateway lists active sessions (in memory) + discovered (on disk)
    # Our mocked gateway won't have active sessions initially unless we create them via gateway.
    # But list_sessions_async also scans disk.
    
    resp = client.get("/api/v1/ops/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert "sessions" in data
    
    # We should see session_A and session_B
    ids = [s["session_id"] for s in data["sessions"]]
    assert "session_A" in ids
    assert "session_B" in ids

def test_ops_get_session(client, tmp_path):
    _create_dummy_session(tmp_path, "session_details", ["foo"])
    
    resp = client.get("/api/v1/ops/sessions/session_details")
    assert resp.status_code == 200
    data = resp.json()
    assert "session" in data
    assert data["session"]["session_id"] == "session_details"
    assert data["session"]["has_run_log"] is True

def test_ops_delete_session(client, tmp_path):
    _create_dummy_session(tmp_path, "session_del", ["foo"])
    assert (tmp_path / "session_del").exists()
    
    # Missing verify param
    resp = client.delete("/api/v1/ops/sessions/session_del")
    assert resp.status_code == 400
    
    # With verify param
    resp = client.delete("/api/v1/ops/sessions/session_del?confirm=true")
    assert resp.status_code == 200
    assert not (tmp_path / "session_del").exists()

def test_ops_log_tail(client, tmp_path):
    lines = [f"line {i}" for i in range(100)]
    _create_dummy_session(tmp_path, "session_logs", lines)
    
    # Tail default (last 500 lines) -> should get all 100
    resp = client.get("/api/v1/ops/logs/tail?session_id=session_logs")
    assert resp.status_code == 200
    data = resp.json()
    # The new implementation wraps result in "file" too
    assert "lines" in data
    assert len(data["lines"]) == 100
    assert data["lines"][0] == "line 0"
    assert data["lines"][-1] == "line 99"

    # Tail limit
    resp = client.get("/api/v1/ops/logs/tail?session_id=session_logs&limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["lines"]) == 5
    assert data["lines"][-1] == "line 99"
    
    # Tail empty (non existent session -> 400 or empty?)
    # Implementation says if session_id provided, it resolves path.
    # _ops_service.tail_file checks file existence and returns empty dict.
    # BUT ops_logs_tail calls _resolve_workspace_path(session_id) which is just path join.
    # Then tail_file calls exists().
    # So it should return empty struct.
    
    resp = client.get("/api/v1/ops/logs/tail?session_id=non_existent")
    assert resp.status_code == 200 
    data = resp.json()
    assert data["lines"] == []
    assert data["size"] == 0


def test_ops_log_tail_rejects_invalid_session_id(client):
    resp = client.get("/api/v1/ops/logs/tail?session_id=../../etc/passwd")
    assert resp.status_code == 400
    assert "Invalid session id format" in resp.text


def test_ops_log_tail_rejects_path_escape(client):
    resp = client.get("/api/v1/ops/logs/tail?path=../gateway.log")
    assert resp.status_code == 400
    assert "Log path must remain under UA_WORKSPACES_DIR" in resp.text

def test_ops_preview_compact_reset(client, tmp_path):
    # Setup session with logs
    lines = [f"line {i}" for i in range(10)]
    session_dir = _create_dummy_session(tmp_path, "session_complex", lines)
    (session_dir / "activity_journal.log").write_text("\n".join(lines), encoding="utf-8")
    
    # Preview (tails activity journal)
    resp = client.get("/api/v1/ops/sessions/session_complex/preview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "session_complex"
    assert len(data["lines"]) == 10
    
    # Compact
    # Compact run.log to 5 lines
    resp = client.post(
        "/api/v1/ops/sessions/session_complex/compact",
        json={"max_lines": 5, "max_bytes": 1000}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "compacted"
    
    # Verify file on disk
    assert len((session_dir / "run.log").read_text().splitlines()) == 5
    
    # Reset
    # Should move files to archive
    resp = client.post(
        "/api/v1/ops/sessions/session_complex/reset",
        json={"clear_logs": True, "clear_memory": False, "clear_work_products": False}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "reset"
    
    # Verify files gone
    assert not (session_dir / "run.log").exists()
    assert not (session_dir / "activity_journal.log").exists()
    # Archive exists
    archive_dir = Path(resp.json()["archive_dir"])
    assert archive_dir.exists()
    assert (archive_dir / "run.log").exists()
