import json
from pathlib import Path
from types import SimpleNamespace

from universal_agent import gateway_server


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def test_turn_lineage_file_tracks_start_and_finalize(tmp_path, monkeypatch):
    session_id = "session_lineage"
    turn_id = "turn_demo"
    workspace = tmp_path / session_id
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "run.log").write_text("seed\n", encoding="utf-8")

    monkeypatch.setattr(gateway_server, "_sessions", {})
    monkeypatch.setattr(gateway_server, "_session_turn_state", {})
    session = SimpleNamespace(session_id=session_id, workspace_dir=str(workspace), metadata={})
    gateway_server._sessions[session_id] = session

    admitted = gateway_server._admit_turn(
        session_id=session_id,
        connection_id="conn-1",
        user_input="send interim and final updates",
        force_complex=True,
        metadata={"source": "user"},
        client_turn_id=turn_id,
    )
    assert admitted["decision"] == "accepted"
    assert admitted["turn_id"] == turn_id

    gateway_server._finalize_turn(
        session_id,
        turn_id,
        gateway_server.TURN_STATUS_COMPLETED,
        completion={"tool_calls": 2, "duration_seconds": 1.23},
    )

    lineage_path = workspace / gateway_server.TURN_LINEAGE_DIRNAME / f"{turn_id}.jsonl"
    assert lineage_path.exists()
    rows = _read_jsonl(lineage_path)
    assert len(rows) == 2
    assert rows[0]["event"] == "turn_started"
    assert rows[1]["event"] == "turn_finalized"
    assert rows[1]["status"] == gateway_server.TURN_STATUS_COMPLETED
    assert rows[1]["run_log_offset_end"] >= rows[0]["run_log_offset_start"]


def test_runtime_foreground_counters_are_separate_from_heartbeat(monkeypatch):
    session_id = "session_runtime"
    monkeypatch.setattr(gateway_server, "_session_runtime", {})

    gateway_server._increment_session_active_runs(session_id, run_source="heartbeat")
    runtime = gateway_server._session_runtime_snapshot(session_id)
    assert runtime["active_runs"] == 1
    assert runtime["active_foreground_runs"] == 0

    gateway_server._increment_session_active_runs(session_id, run_source="user")
    runtime = gateway_server._session_runtime_snapshot(session_id)
    assert runtime["active_runs"] == 2
    assert runtime["active_foreground_runs"] == 1

    gateway_server._decrement_session_active_runs(session_id, run_source="heartbeat")
    runtime = gateway_server._session_runtime_snapshot(session_id)
    assert runtime["active_runs"] == 1
    assert runtime["active_foreground_runs"] == 1

    gateway_server._decrement_session_active_runs(session_id, run_source="user")
    runtime = gateway_server._session_runtime_snapshot(session_id)
    assert runtime["active_runs"] == 0
    assert runtime["active_foreground_runs"] == 0
