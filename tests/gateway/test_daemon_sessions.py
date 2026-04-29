"""Tests for DaemonSessionManager and daemon session utilities."""

from __future__ import annotations

import os
from pathlib import Path
import time
from unittest.mock import MagicMock, patch

import pytest

from universal_agent.services.daemon_sessions import (
    DAEMON_ROLE_HEARTBEAT,
    DAEMON_ROLE_TODO,
    DAEMON_SESSION_PREFIX,
    DaemonSessionManager,
    configured_daemon_agents,
    daemon_sessions_enabled,
    is_daemon_session,
)

# ── is_daemon_session ────────────────────────────────────────────────────────


def test_is_daemon_session_true():
    assert is_daemon_session("daemon_simone") is True
    assert is_daemon_session("daemon_atlas") is True
    assert is_daemon_session("daemon_cody") is True


def test_is_daemon_session_false():
    assert is_daemon_session("user_123") is False
    assert is_daemon_session("") is False
    assert is_daemon_session(None) is False


# ── daemon_sessions_enabled ──────────────────────────────────────────────────


def test_daemon_sessions_enabled_default_with_heartbeat():
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("UA_DAEMON_SESSIONS_ENABLED", None)
        assert daemon_sessions_enabled(heartbeat_enabled=True) is True
        assert daemon_sessions_enabled(heartbeat_enabled=False) is False


def test_daemon_sessions_enabled_explicit_override():
    with patch.dict(os.environ, {"UA_DAEMON_SESSIONS_ENABLED": "0"}):
        assert daemon_sessions_enabled(heartbeat_enabled=True) is False
    with patch.dict(os.environ, {"UA_DAEMON_SESSIONS_ENABLED": "1"}):
        assert daemon_sessions_enabled(heartbeat_enabled=False) is True


# ── configured_daemon_agents ─────────────────────────────────────────────────


def test_configured_daemon_agents_default():
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("UA_DAEMON_SESSION_AGENTS", None)
        agents = configured_daemon_agents()
        assert agents == ["simone"]


def test_configured_daemon_agents_override():
    with patch.dict(os.environ, {"UA_DAEMON_SESSION_AGENTS": "simone,custom_bot"}):
        agents = configured_daemon_agents()
        assert agents == ["simone", "custom_bot"]


# ── DaemonSessionManager ────────────────────────────────────────────────────


@pytest.fixture
def workspaces_dir(tmp_path):
    ws = tmp_path / "workspaces"
    ws.mkdir()
    return ws


@pytest.fixture
def mock_heartbeat():
    hb = MagicMock()
    hb.register_session = MagicMock()
    hb.unregister_session = MagicMock()
    return hb


@pytest.fixture
def manager(workspaces_dir, mock_heartbeat):
    return DaemonSessionManager(
        workspaces_dir=workspaces_dir,
        heartbeat_service=mock_heartbeat,
        agent_names=["simone", "atlas"],
    )


class TestDaemonSessionManager:
    def test_ensure_daemon_sessions_creates_sessions(self, manager, mock_heartbeat):
        created = manager.ensure_daemon_sessions()
        assert len(created) == 3
        assert "daemon_simone_heartbeat" in created
        assert "daemon_simone_todo" in created
        assert "daemon_atlas_heartbeat" in created
        assert mock_heartbeat.register_session.call_count == 2

    def test_sessions_have_workspaces(self, manager):
        manager.ensure_daemon_sessions()
        for session_id in ["daemon_simone_heartbeat", "daemon_simone_todo", "daemon_atlas_heartbeat"]:
            session = manager.get_session(session_id)
            assert session is not None
            ws_path = Path(session.workspace_dir)
            assert ws_path.exists()
            assert ws_path.name.startswith("run_daemon_")
            assert (ws_path / "work_products").exists()

    def test_session_metadata(self, manager):
        manager.ensure_daemon_sessions()
        session = manager.get_session("daemon_simone_todo")
        assert session.user_id == "daemon"
        assert session.metadata["source"] == "daemon"
        assert session.metadata["daemon_agent"] == "simone"
        assert session.metadata["daemon_role"] == DAEMON_ROLE_TODO
        assert session.metadata["session_role"] == "todo_execution"

    def test_get_session_for_agent(self, manager):
        manager.ensure_daemon_sessions()
        session = manager.get_session_for_agent("simone")
        assert session is not None
        assert session.session_id == "daemon_simone_todo"

    def test_get_session_for_agent_role(self, manager):
        manager.ensure_daemon_sessions()
        session = manager.get_session_for_agent("simone", role=DAEMON_ROLE_HEARTBEAT)
        assert session is not None
        assert session.session_id == "daemon_simone_heartbeat"

    def test_get_session_for_agent_case_insensitive(self, manager):
        manager.ensure_daemon_sessions()
        session = manager.get_session_for_agent("SIMONE")
        assert session is not None

    def test_get_session_for_unknown_agent(self, manager):
        manager.ensure_daemon_sessions()
        assert manager.get_session_for_agent("nonexistent") is None

    def test_recycle_session(self, manager):
        manager.ensure_daemon_sessions()
        session = manager.get_session("daemon_simone_todo")
        old_workspace = session.workspace_dir

        new_workspace = manager.recycle_session("daemon_simone_todo")
        assert new_workspace is not None
        assert new_workspace != old_workspace
        # Old workspace should be archived (moved)
        assert not Path(old_workspace).exists()
        # New workspace exists
        assert Path(new_workspace).exists()
        assert (Path(new_workspace) / "work_products").exists()

    def test_recycle_unknown_session(self, manager):
        manager.ensure_daemon_sessions()
        assert manager.recycle_session("daemon_unknown") is None

    def test_session_ids_property(self, manager):
        manager.ensure_daemon_sessions()
        sids = manager.session_ids
        assert "daemon_simone_heartbeat" in sids
        assert "daemon_simone_todo" in sids
        assert "daemon_atlas_heartbeat" in sids

    def test_sessions_property(self, manager):
        manager.ensure_daemon_sessions()
        sessions = manager.sessions
        assert "daemon_simone_heartbeat" in sessions
        assert "daemon_simone_todo" in sessions
        assert "daemon_atlas_heartbeat" in sessions

    def test_shutdown(self, manager, mock_heartbeat):
        manager.ensure_daemon_sessions()
        manager.shutdown()
        assert mock_heartbeat.unregister_session.call_count == 3
        assert len(manager.sessions) == 0

    def test_cleanup_old_archives(self, manager, workspaces_dir):
        manager.ensure_daemon_sessions()
        # Create a fake old archive
        archive_dir = workspaces_dir / "_daemon_archives"
        archive_dir.mkdir(exist_ok=True)
        old_archive = archive_dir / "run_daemon_simone_todo_20200101_000000_abc12345"
        old_archive.mkdir()
        # Set modification time to 72 hours ago
        old_mtime = time.time() - (72 * 3600)
        os.utime(str(old_archive), (old_mtime, old_mtime))

        removed = manager.cleanup_old_archives(max_age_hours=48)
        assert removed == 1
        assert not old_archive.exists()

    def test_cleanup_stale_workspaces_archives_run_daemon_workspaces(self, manager, workspaces_dir):
        stale_workspace = workspaces_dir / "run_daemon_simone_todo_20200101_000000_abc12345"
        stale_workspace.mkdir()

        archived = manager._cleanup_stale_workspaces()

        assert archived == 1
        assert not stale_workspace.exists()
        archived_copy = workspaces_dir / "_daemon_archives" / stale_workspace.name
        assert archived_copy.exists()


# ── Heartbeat idle timeout ───────────────────────────────────────────────────


class TestHeartbeatDaemonIdleTimeout:
    """Verify that daemon sessions remain registered until runtime activity becomes stale."""

    def test_daemon_session_without_runtime_activity_is_not_reaped(self):
        """Daemon sessions without runtime activity are left alone for other lifecycle checks."""
        from universal_agent.gateway import GatewaySession
        from universal_agent.heartbeat_service import HeartbeatService

        mock_gateway = MagicMock()
        mock_manager = MagicMock()
        mock_manager.session_connections = {}

        hb = HeartbeatService(mock_gateway, mock_manager)

        daemon_session = GatewaySession(
            session_id="daemon_simone_heartbeat",
            user_id="daemon",
            workspace_dir="/tmp/daemon_ws",
            metadata={},
        )

        result = hb._check_session_idle(daemon_session)
        assert result is False
