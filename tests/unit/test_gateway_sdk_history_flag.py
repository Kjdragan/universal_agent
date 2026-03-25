from __future__ import annotations

import asyncio

from universal_agent.gateway import GatewaySession, InProcessGateway


def test_gateway_list_sessions_augments_with_sdk_history(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_ENABLE_SDK_SESSION_HISTORY", "1")
    monkeypatch.setattr(
        "universal_agent.gateway.session_history_adapter.list_session_summaries_for_workspace",
        lambda *_args, **_kwargs: [
            {
                "session_id": "sdk_hist_1",
                "workspace_dir": str(tmp_path / "sdk_hist_1"),
                "summary": "history session",
                "cwd": str(tmp_path),
                "last_modified": "2026-03-08T00:00:00+00:00",
                "file_size": 100,
            }
        ],
    )
    gateway = InProcessGateway(workspace_base=tmp_path)
    try:
        summaries = gateway.list_sessions()
        ids = {item.session_id for item in summaries}
        assert "sdk_hist_1" in ids
    finally:
        asyncio.run(gateway.close())


def test_gateway_list_live_sessions_excludes_disk_archives_and_sdk_history(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_ENABLE_SDK_SESSION_HISTORY", "1")
    monkeypatch.setattr(
        "universal_agent.gateway.session_history_adapter.list_session_summaries_for_workspace",
        lambda *_args, **_kwargs: [
            {
                "session_id": "sdk_hist_1",
                "workspace_dir": str(tmp_path / "sdk_hist_1"),
                "summary": "history session",
                "cwd": str(tmp_path),
                "last_modified": "2026-03-08T00:00:00+00:00",
                "file_size": 100,
            }
        ],
    )
    archived_dir = tmp_path / "session_archived_1"
    archived_dir.mkdir(parents=True)
    (archived_dir / "trace.json").write_text("{}", encoding="utf-8")

    gateway = InProcessGateway(workspace_base=tmp_path)
    live_session = GatewaySession(
        session_id="live_1",
        user_id="user-1",
        workspace_dir=str((tmp_path / "live_1").resolve()),
        metadata={},
    )
    gateway._sessions[live_session.session_id] = live_session
    try:
        summaries = gateway.list_live_sessions()
        ids = {item.session_id for item in summaries}
        assert ids == {"live_1"}
    finally:
        asyncio.run(gateway.close())
