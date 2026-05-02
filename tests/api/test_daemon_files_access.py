"""Regression guard: api/server.py must accept daemon-owner sessions and
resolve `daemon_*` ids to their actual `run_{session_id}_*` workspace.

Both behaviors are required by the Task Hub Workspace button. Without
them, clicking Workspace on a Simone-todo task showed:

  - HTTP 403 from /api/files/daemon_simone_todo/{path}  (owner not
    whitelisted; daemons set user_id="daemon" in
    services/daemon_sessions.py:97 but the dashboard owner is the
    primary user)
  - Empty file panel (no daemon-glob fallback — the bare directory
    `WORKSPACES_DIR/daemon_simone_todo` typically doesn't exist; the
    actual workspace lives at `run_daemon_simone_todo_<ts>_<uuid>`)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent.api import server as api_server


def test_daemon_user_id_is_treated_as_system_owner():
    """`_enforce_session_owner` must not 403 when the session belongs
    to the daemon executor. The whitelist on `_SYSTEM_SESSION_OWNERS`
    is the gate."""
    assert "daemon" in api_server._SYSTEM_SESSION_OWNERS
    assert api_server._is_system_session_owner("daemon") is True


def test_resolve_workspace_picks_latest_daemon_run_dir(tmp_path, monkeypatch):
    """For a session id like `daemon_simone_todo`, the resolver must
    glob `run_daemon_simone_todo_*` and return the most recently
    modified candidate, not the bare-name stub which usually does not
    exist on disk."""
    ws_root = tmp_path / "AGENT_RUN_WORKSPACES"
    ws_root.mkdir()

    older = ws_root / "run_daemon_simone_todo_20260101_120000_aaaa"
    older.mkdir()
    (older / "marker.txt").write_text("old")

    newer = ws_root / "run_daemon_simone_todo_20260502_220000_bbbb"
    newer.mkdir()
    (newer / "marker.txt").write_text("new")

    # Make `newer` strictly more recent than `older`.
    import os, time
    os.utime(older, (time.time() - 3600, time.time() - 3600))
    os.utime(newer, (time.time(), time.time()))

    monkeypatch.setattr(api_server, "WORKSPACES_DIR", ws_root)

    resolved = api_server._resolve_workspace_for_session("daemon_simone_todo")
    assert resolved == newer
    assert (resolved / "marker.txt").read_text() == "new"


def test_resolve_workspace_falls_back_to_archive(tmp_path, monkeypatch):
    """If no active `run_*` candidate exists, fall back to
    `_daemon_archives/run_{session_id}_*`."""
    ws_root = tmp_path / "AGENT_RUN_WORKSPACES"
    ws_root.mkdir()
    archive = ws_root / "_daemon_archives"
    archive.mkdir()

    archived = archive / "run_daemon_simone_todo_20251225_120000_zzzz"
    archived.mkdir()

    monkeypatch.setattr(api_server, "WORKSPACES_DIR", ws_root)

    resolved = api_server._resolve_workspace_for_session("daemon_simone_todo")
    assert resolved == archived


def test_resolve_workspace_prefers_direct_match_when_present(tmp_path, monkeypatch):
    """Non-daemon sessions and any session that has its workspace at
    the canonical `WORKSPACES_DIR/{session_id}` path resolve directly
    without the glob."""
    ws_root = tmp_path / "AGENT_RUN_WORKSPACES"
    ws_root.mkdir()
    direct = ws_root / "session_abc123"
    direct.mkdir()

    monkeypatch.setattr(api_server, "WORKSPACES_DIR", ws_root)

    assert api_server._resolve_workspace_for_session("session_abc123") == direct


def test_resolve_workspace_returns_direct_path_when_nothing_matches(tmp_path, monkeypatch):
    """When neither the direct dir nor any glob candidate exists, the
    resolver returns the direct (non-existent) path. Callers downstream
    treat that as "empty workspace" rather than raising — same UX as a
    non-daemon session whose workspace hasn't been created yet."""
    ws_root = tmp_path / "AGENT_RUN_WORKSPACES"
    ws_root.mkdir()
    monkeypatch.setattr(api_server, "WORKSPACES_DIR", ws_root)

    resolved = api_server._resolve_workspace_for_session("daemon_unknown_agent")
    assert resolved == ws_root / "daemon_unknown_agent"
    assert not resolved.exists()
