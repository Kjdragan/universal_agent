"""Tests for the system load guard — blocks dispatch when system is overloaded.

The guard checks process count and swap usage before allowing the idle
dispatch loop to wake sessions. When thresholds are exceeded, it returns
a structured reason for notification.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from universal_agent.services.system_load_guard import (
    SystemHealthStatus,
    is_system_healthy,
    DEFAULT_MAX_PROCESS_COUNT,
    DEFAULT_MAX_SWAP_PCT,
)


# ── Process Count Guard ──────────────────────────────────────────────────────


class TestProcessCountGuard:
    """Test process-count-based dispatch blocking."""

    @patch("universal_agent.services.system_load_guard._count_user_processes")
    def test_allows_dispatch_under_threshold(self, mock_count):
        mock_count.return_value = 30
        status = is_system_healthy()
        assert status.healthy is True
        assert "ok" in status.reason.lower() or "healthy" in status.reason.lower()

    @patch("universal_agent.services.system_load_guard._count_user_processes")
    def test_blocks_when_process_count_exceeds_limit(self, mock_count):
        mock_count.return_value = 120
        status = is_system_healthy(max_process_count=80)
        assert status.healthy is False
        assert "process" in status.reason.lower()
        assert status.process_count == 120

    @patch("universal_agent.services.system_load_guard._count_user_processes")
    def test_exact_threshold_allows_dispatch(self, mock_count):
        """At exactly the threshold — should still allow (not strictly greater)."""
        mock_count.return_value = 80
        status = is_system_healthy(max_process_count=80)
        assert status.healthy is True

    @patch("universal_agent.services.system_load_guard._count_user_processes")
    def test_env_override_for_process_threshold(self, mock_count):
        mock_count.return_value = 50
        with patch.dict(os.environ, {"UA_MAX_PROCESS_COUNT": "40"}):
            status = is_system_healthy()
        assert status.healthy is False
        assert status.process_count == 50


# ── Swap Usage Guard ─────────────────────────────────────────────────────────


class TestSwapUsageGuard:
    """Test swap-usage-based dispatch blocking."""

    @patch("universal_agent.services.system_load_guard._get_swap_percent")
    @patch("universal_agent.services.system_load_guard._count_user_processes")
    def test_allows_dispatch_with_low_swap(self, mock_procs, mock_swap):
        mock_procs.return_value = 20
        mock_swap.return_value = 30.0
        status = is_system_healthy()
        assert status.healthy is True

    @patch("universal_agent.services.system_load_guard._get_swap_percent")
    @patch("universal_agent.services.system_load_guard._count_user_processes")
    def test_blocks_when_swap_exceeds_threshold(self, mock_procs, mock_swap):
        mock_procs.return_value = 20
        mock_swap.return_value = 92.0
        status = is_system_healthy(max_swap_pct=85.0)
        assert status.healthy is False
        assert "swap" in status.reason.lower()
        assert status.swap_pct == 92.0

    @patch("universal_agent.services.system_load_guard._get_swap_percent")
    @patch("universal_agent.services.system_load_guard._count_user_processes")
    def test_env_override_for_swap_threshold(self, mock_procs, mock_swap):
        mock_procs.return_value = 20
        mock_swap.return_value = 60.0
        with patch.dict(os.environ, {"UA_MAX_SWAP_PCT": "50"}):
            status = is_system_healthy()
        assert status.healthy is False


# ── Combined Checks ──────────────────────────────────────────────────────────


class TestCombinedChecks:
    """Test that both checks run and the worst one wins."""

    @patch("universal_agent.services.system_load_guard._get_swap_percent")
    @patch("universal_agent.services.system_load_guard._count_user_processes")
    def test_process_count_fails_first(self, mock_procs, mock_swap):
        """If process count exceeds but swap is fine, still blocked."""
        mock_procs.return_value = 200
        mock_swap.return_value = 10.0
        status = is_system_healthy(max_process_count=80)
        assert status.healthy is False
        assert "process" in status.reason.lower()

    @patch("universal_agent.services.system_load_guard._get_swap_percent")
    @patch("universal_agent.services.system_load_guard._count_user_processes")
    def test_swap_fails_when_procs_ok(self, mock_procs, mock_swap):
        """If swap exceeds but process count is fine, still blocked."""
        mock_procs.return_value = 20
        mock_swap.return_value = 99.0
        status = is_system_healthy(max_swap_pct=85.0)
        assert status.healthy is False
        assert "swap" in status.reason.lower()

    @patch("universal_agent.services.system_load_guard._get_swap_percent")
    @patch("universal_agent.services.system_load_guard._count_user_processes")
    def test_both_ok(self, mock_procs, mock_swap):
        mock_procs.return_value = 30
        mock_swap.return_value = 20.0
        status = is_system_healthy()
        assert status.healthy is True


# ── Notification Content ─────────────────────────────────────────────────────


class TestNotificationContent:
    """Verify the guard produces actionable notification info."""

    @patch("universal_agent.services.system_load_guard._get_swap_percent")
    @patch("universal_agent.services.system_load_guard._count_user_processes")
    def test_status_includes_notification_message(self, mock_procs, mock_swap):
        mock_procs.return_value = 150
        mock_swap.return_value = 95.0
        status = is_system_healthy(max_process_count=80, max_swap_pct=85.0)
        assert status.healthy is False
        assert status.notification_message is not None
        assert "investigate" in status.notification_message.lower() or "150" in status.notification_message

    @patch("universal_agent.services.system_load_guard._get_swap_percent")
    @patch("universal_agent.services.system_load_guard._count_user_processes")
    def test_healthy_status_has_no_notification(self, mock_procs, mock_swap):
        mock_procs.return_value = 20
        mock_swap.return_value = 10.0
        status = is_system_healthy()
        assert status.healthy is True
        assert status.notification_message is None or status.notification_message == ""


# ── Defaults ─────────────────────────────────────────────────────────────────


class TestDefaults:
    def test_default_constants_are_reasonable(self):
        assert DEFAULT_MAX_PROCESS_COUNT >= 50
        assert DEFAULT_MAX_PROCESS_COUNT <= 200
        assert DEFAULT_MAX_SWAP_PCT >= 50.0
        assert DEFAULT_MAX_SWAP_PCT <= 95.0
