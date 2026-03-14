"""Tests for the session reaper module."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from universal_agent.session.reaper import cleanup_stale_workspaces, SKIP_PREFIXES


@pytest.fixture
def temp_workspace_setup():
    """Create a temporary workspace structure for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspaces_dir = Path(tmpdir) / "workspaces"
        archive_dir = Path(tmpdir) / "archive"
        workspaces_dir.mkdir()
        archive_dir.mkdir()

        # Create various test workspaces
        # Fresh workspace (should not be archived)
        fresh_ws = workspaces_dir / "session_fresh"
        fresh_ws.mkdir()
        (fresh_ws / "test.txt").write_text("fresh")

        # Stale workspace (should be archived)
        stale_ws = workspaces_dir / "session_stale"
        stale_ws.mkdir()
        (stale_ws / "test.txt").write_text("stale")
        # Set mtime to 25 hours ago
        import os
        stale_time = (datetime.now() - timedelta(hours=25)).timestamp()
        os.utime(stale_ws, (stale_time, stale_time))

        # Cron workspace (should be skipped even if stale)
        cron_ws = workspaces_dir / "cron_abc123"
        cron_ws.mkdir()
        (cron_ws / "test.txt").write_text("cron")
        os.utime(cron_ws, (stale_time, stale_time))

        # Another stale workspace
        stale_ws2 = workspaces_dir / "vp_coder_external"
        stale_ws2.mkdir()
        (stale_ws2 / "test.txt").write_text("stale2")
        os.utime(stale_ws2, (stale_time, stale_time))

        # File (not directory) - should be skipped
        (workspaces_dir / "some_file.txt").write_text("file")

        yield {
            "workspaces_dir": workspaces_dir,
            "archive_dir": archive_dir,
            "fresh_ws": fresh_ws,
            "stale_ws": stale_ws,
            "cron_ws": cron_ws,
            "stale_ws2": stale_ws2,
        }


@pytest.mark.asyncio
async def test_dry_run_does_not_move_files(temp_workspace_setup):
    """Test that dry-run mode does not actually move files."""
    result = await cleanup_stale_workspaces(
        max_age_hours=24,
        workspaces_dir=temp_workspace_setup["workspaces_dir"],
        archive_dir=temp_workspace_setup["archive_dir"],
        dry_run=True,
    )

    # Should report 2 stale workspaces (stale_ws and stale_ws2)
    assert len(result) == 2
    names = [r["name"] for r in result]
    assert "session_stale" in names
    assert "vp_coder_external" in names

    # Files should NOT have been moved
    assert temp_workspace_setup["stale_ws"].exists()
    assert temp_workspace_setup["stale_ws2"].exists()
    assert not (temp_workspace_setup["archive_dir"] / "session_stale").exists()


@pytest.mark.asyncio
async def test_execute_moves_stale_workspaces(temp_workspace_setup):
    """Test that execute mode actually moves stale workspaces."""
    result = await cleanup_stale_workspaces(
        max_age_hours=24,
        workspaces_dir=temp_workspace_setup["workspaces_dir"],
        archive_dir=temp_workspace_setup["archive_dir"],
        dry_run=False,
    )

    # Should have archived 2 workspaces
    assert len(result) == 2
    names = [r["name"] for r in result]
    assert "session_stale" in names
    assert "vp_coder_external" in names

    # Files should have been moved
    assert not temp_workspace_setup["stale_ws"].exists()
    assert not temp_workspace_setup["stale_ws2"].exists()
    assert (temp_workspace_setup["archive_dir"] / "session_stale").exists()
    assert (temp_workspace_setup["archive_dir"] / "vp_coder_external").exists()


@pytest.mark.asyncio
async def test_cron_workspaces_are_skipped(temp_workspace_setup):
    """Test that cron_* workspaces are never archived."""
    result = await cleanup_stale_workspaces(
        max_age_hours=24,
        workspaces_dir=temp_workspace_setup["workspaces_dir"],
        archive_dir=temp_workspace_setup["archive_dir"],
        dry_run=False,
    )

    # Cron workspace should not be in results
    names = [r["name"] for r in result]
    assert "cron_abc123" not in names

    # Cron workspace should still exist in workspaces
    assert temp_workspace_setup["cron_ws"].exists()


@pytest.mark.asyncio
async def test_fresh_workspaces_are_not_archived(temp_workspace_setup):
    """Test that fresh workspaces are not archived."""
    result = await cleanup_stale_workspaces(
        max_age_hours=24,
        workspaces_dir=temp_workspace_setup["workspaces_dir"],
        archive_dir=temp_workspace_setup["archive_dir"],
        dry_run=False,
    )

    # Fresh workspace should not be in results
    names = [r["name"] for r in result]
    assert "session_fresh" not in names

    # Fresh workspace should still exist
    assert temp_workspace_setup["fresh_ws"].exists()


@pytest.mark.asyncio
async def test_non_directory_files_are_skipped(temp_workspace_setup):
    """Test that regular files in workspaces dir are skipped."""
    result = await cleanup_stale_workspaces(
        max_age_hours=24,
        workspaces_dir=temp_workspace_setup["workspaces_dir"],
        archive_dir=temp_workspace_setup["archive_dir"],
        dry_run=False,
    )

    # File should not be in results
    names = [r["name"] for r in result]
    assert "some_file.txt" not in names

    # File should still exist
    assert (temp_workspace_setup["workspaces_dir"] / "some_file.txt").exists()


@pytest.mark.asyncio
async def test_custom_max_age(temp_workspace_setup):
    """Test that custom max_age_hours works correctly."""
    # Set up a workspace that is 12 hours old
    ws_12h = temp_workspace_setup["workspaces_dir"] / "session_12h"
    ws_12h.mkdir()
    (ws_12h / "test.txt").write_text("12h")
    import os
    time_12h = (datetime.now() - timedelta(hours=12)).timestamp()
    os.utime(ws_12h, (time_12h, time_12h))

    # With max_age=10, the 12h workspace should be archived
    result = await cleanup_stale_workspaces(
        max_age_hours=10,
        workspaces_dir=temp_workspace_setup["workspaces_dir"],
        archive_dir=temp_workspace_setup["archive_dir"],
        dry_run=True,
    )

    names = [r["name"] for r in result]
    assert "session_12h" in names

    # With max_age=24, the 12h workspace should NOT be archived
    result = await cleanup_stale_workspaces(
        max_age_hours=24,
        workspaces_dir=temp_workspace_setup["workspaces_dir"],
        archive_dir=temp_workspace_setup["archive_dir"],
        dry_run=True,
    )

    names = [r["name"] for r in result]
    assert "session_12h" not in names


@pytest.mark.asyncio
async def test_missing_workspaces_dir():
    """Test behavior when workspaces directory does not exist."""
    result = await cleanup_stale_workspaces(
        max_age_hours=24,
        workspaces_dir=Path("/nonexistent/path"),
        dry_run=True,
    )
    assert result == []


@pytest.mark.asyncio
async def test_skip_prefixes_constant():
    """Test that SKIP_PREFIXES contains expected values."""
    assert "cron_" in SKIP_PREFIXES
