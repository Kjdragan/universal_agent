"""Regression guard: api/server.py must accept daemon-owner sessions and
resolve `daemon_*` ids to their actual `run_{session_id}_*` workspace.

Both behaviors are required by the Task Hub Workspace button. Without
them, clicking Workspace on a Simone-todo task showed:

  - HTTP 403 from /api/files/daemon_simone_todo/{path}
  - Empty file panel
  - WS attached but every durable-file fetch failed

Two layered root causes were uncovered (the first hid the second):

  1. `gateway.create_session(session_id="daemon_simone_todo",
     user_id=<dispatcher>)` overwrites the daemon's persistent
     `user_id="daemon"` with whatever Composio user id the dispatcher
     passed (gateway.py:510-522). After the first task runs, the
     stored "session owner" is the dispatcher's Composio id, not
     "daemon". `_is_system_session_owner` doesn't match that, so the
     owner-mismatch 403 fires.

  2. Even if the owner check passed, `list_files`/`get_file` resolved
     the workspace as `WORKSPACES_DIR/daemon_simone_todo` directly —
     a path that typically doesn't exist. The actual workspace lives
     at `run_daemon_simone_todo_<ts>_<uuid>`.

Fixes:
  - `_enforce_session_owner` bypasses the owner check entirely for
    `daemon_*` session ids when the requester is the primary
    dashboard owner. Daemon sessions are SHARED runtimes — owner
    semantics don't apply.
  - `_resolve_workspace_for_session()` glob-resolves
    `daemon_simone_todo` → most-recent `run_daemon_simone_todo_*`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent.api import server as api_server


def test_daemon_user_id_is_treated_as_system_owner():
    """Defense-in-depth: even though daemon sessions get their stored
    user_id clobbered by the dispatcher's Composio id at runtime, if
    the persistent ``user_id="daemon"`` ever does survive, the
    whitelist still recognizes it as a system owner."""
    assert "daemon" in api_server._SYSTEM_SESSION_OWNERS
    assert api_server._is_system_session_owner("daemon") is True


@pytest.mark.asyncio
async def test_enforce_session_owner_bypasses_daemon_session_check(monkeypatch):
    """The actual production root cause: gateway.create_session
    overwrites daemon_simone_todo's stored user_id with the
    dispatcher's Composio id (gateway.py:510-522). The owner-mismatch
    check then 403s every dashboard view of a daemon session.

    `_enforce_session_owner` must short-circuit for daemon_* session
    ids when the requester is the primary dashboard owner, BEFORE
    fetching the (irrelevant) gateway-stored owner."""
    # Force a value for `_gateway_url()` so the early `if not _gateway_url(): return`
    # bypass doesn't fire — we want to exercise the real check.
    monkeypatch.setattr(api_server, "_gateway_url", lambda: "http://fake-gateway")

    # If the bypass is missing, this fetcher would be called and would
    # report a clobbered Composio id, triggering the 403. Fail loudly
    # if we ever reach it.
    async def _should_not_be_called(session_id):
        raise AssertionError(
            f"_fetch_gateway_session_owner was called for {session_id!r}; "
            "daemon_* sessions must short-circuit before the gateway probe."
        )

    monkeypatch.setattr(
        api_server, "_fetch_gateway_session_owner", _should_not_be_called
    )

    # Primary dashboard owner viewing a daemon session: must NOT raise.
    primary = api_server._normalize_owner_id(None)
    await api_server._enforce_session_owner(
        "daemon_simone_todo", primary, auth_required=True
    )


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
