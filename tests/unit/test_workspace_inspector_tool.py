import json
from pathlib import Path

from mcp_server import inspect_session_workspace


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_inspect_session_workspace_includes_transcript(monkeypatch, tmp_path):
    workspaces_root = tmp_path / "AGENT_RUN_WORKSPACES"
    session_dir = workspaces_root / "session_alpha"

    _write_text(session_dir / "run.log", "line1\nline2\nline3\nline4")
    _write_text(session_dir / "activity_journal.log", "a1\na2\na3")
    _write_text(session_dir / "transcript.md", "t1\nt2\nt3")
    _write_text(
        session_dir / "trace.json",
        json.dumps({"run_id": "r1", "query": "hello", "tool_calls": []}),
    )
    _write_text(
        session_dir / "heartbeat_state.json",
        json.dumps({"enabled": True, "last_run": "2026-02-11T22:00:00Z"}),
    )
    _write_text(session_dir / "work_products" / "report.md", "# Report")
    _write_text(session_dir / "tasks" / "topic1" / "refined_corpus.md", "content")

    monkeypatch.setenv("UA_WORKSPACES_DIR", str(workspaces_root))

    result = inspect_session_workspace(
        session_id="session_alpha",
        tail_lines=2,
        max_bytes_per_file=40960,
        recent_file_limit=10,
    )
    payload = json.loads(result)

    assert payload["ok"] is True
    assert payload["session_id"] == "session_alpha"
    assert payload["files"]["run.log"]["exists"] is True
    assert payload["files"]["run.log"]["tail"] == ["line3", "line4"]
    assert payload["files"]["transcript.md"]["exists"] is True
    assert payload["files"]["transcript.md"]["tail"] == ["t2", "t3"]
    assert payload["files"]["trace.json"]["exists"] is True
    assert "run_id" in payload["files"]["trace.json"]["keys"]
    assert payload["artifacts"]["work_products"]["total_files"] == 1


def test_inspect_session_workspace_rejects_bad_session_id(monkeypatch, tmp_path):
    workspaces_root = tmp_path / "AGENT_RUN_WORKSPACES"
    workspaces_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("UA_WORKSPACES_DIR", str(workspaces_root))

    result = inspect_session_workspace(session_id="../../etc/passwd")
    payload = json.loads(result)

    assert payload["ok"] is False
    assert "Invalid session_id format" in payload["error"]


def test_inspect_session_workspace_uses_current_workspace(monkeypatch, tmp_path):
    workspaces_root = tmp_path / "AGENT_RUN_WORKSPACES"
    session_dir = workspaces_root / "session_current"
    _write_text(session_dir / "run.log", "r1\nr2")

    monkeypatch.setenv("UA_WORKSPACES_DIR", str(workspaces_root))
    monkeypatch.setenv("CURRENT_SESSION_WORKSPACE", str(session_dir))

    result = inspect_session_workspace(include_transcript=False, tail_lines=10)
    payload = json.loads(result)

    assert payload["ok"] is True
    assert payload["source"] == "current_workspace"
    assert payload["session_id"] == "session_current"
    assert "transcript.md" not in payload["files"]
