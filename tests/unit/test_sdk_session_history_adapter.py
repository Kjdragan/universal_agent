from __future__ import annotations

import sys
import types

from universal_agent.sdk import session_history_adapter as adapter


def test_list_sessions_returns_empty_when_unavailable(monkeypatch):
    monkeypatch.setattr(adapter, "sdk_history_available", lambda: False)
    assert adapter.list_sessions() == []


def test_list_and_messages_normalize(monkeypatch):
    fake_module = types.SimpleNamespace(
        list_sessions=lambda directory=None, limit=None, include_worktrees=True: [
            types.SimpleNamespace(
                session_id="sess_1",
                summary="Summary",
                first_prompt="Prompt",
                custom_title=None,
                git_branch="main",
                cwd="/tmp/ws",
                file_size=123,
                last_modified=1_700_000_000_000,
            )
        ],
        get_session_messages=lambda session_id, directory=None, limit=None, offset=0: [
            types.SimpleNamespace(
                type="user",
                uuid="m1",
                session_id=session_id,
                parent_tool_use_id=None,
                message={"role": "user", "content": "hello"},
            )
        ],
    )
    monkeypatch.setattr(adapter, "sdk_history_available", lambda: True)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_module)

    sessions = adapter.list_sessions(directory="/tmp", limit=5)
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "sess_1"
    assert sessions[0]["source"] == "sdk_history"
    assert sessions[0]["workspace_dir"] == "/tmp/ws"

    messages = adapter.get_session_messages("sess_1", directory="/tmp", limit=5, offset=0)
    assert len(messages) == 1
    assert messages[0]["uuid"] == "m1"
    assert "hello" in messages[0]["preview"]
