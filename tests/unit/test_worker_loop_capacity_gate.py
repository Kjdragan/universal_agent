"""Tests for R1's VP-worker pre-tick capacity-governor gate.

VP mission dispatch spawns the `claude` CLI as a subprocess invisible to the
httpx observability hook, so `_maybe_hold_for_capacity_governor` (checked at
the top of `_tick`, before `claim_next_vp_mission`) is the only gate that can
stand VP dispatch down while the ZAI 1310 weekly/monthly quota-exhaustion
auto-pause (or any other CapacityGovernor denial) is active.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from universal_agent.services.capacity_governor import CapacityGovernor
from universal_agent.vp.profiles import VpProfile
from universal_agent.vp.worker_loop import VpWorkerLoop


@pytest.fixture(autouse=True)
def _fresh_governor():
    CapacityGovernor.reset_instance()
    yield
    CapacityGovernor.reset_instance()


def _make_loop(tmp_path: Path) -> VpWorkerLoop:
    profile = VpProfile(
        vp_id="vp.general.primary",
        display_name="GENERAL",
        runtime_id="runtime.general",
        client_kind="claude_code",
        workspace_root=tmp_path / "workspaces",
    )
    conn = MagicMock(spec=sqlite3.Connection)
    with patch("universal_agent.vp.worker_loop.get_vp_profile", return_value=profile):
        return VpWorkerLoop(
            conn=conn,
            vp_id="vp.general.primary",
            worker_id="worker-1",
            workspace_base=tmp_path,
        )


def test_holds_tick_when_governor_denies_dispatch(tmp_path):
    loop = _make_loop(tmp_path)
    with patch.object(
        CapacityGovernor, "can_dispatch",
        return_value=(False, "zai_global_pause: zai_1310_weekly_limit_exhausted"),
    ):
        assert loop._maybe_hold_for_capacity_governor() is True


def test_does_not_hold_when_governor_allows_dispatch(tmp_path):
    loop = _make_loop(tmp_path)
    with patch.object(
        CapacityGovernor, "can_dispatch", return_value=(True, "capacity_available"),
    ):
        assert loop._maybe_hold_for_capacity_governor() is False


def test_fails_open_when_governor_raises(tmp_path):
    """A broken CapacityGovernor must NOT hold the tick forever — fail open
    so a governor bug can't silently wedge all VP dispatch."""
    loop = _make_loop(tmp_path)

    def boom(self):
        raise RuntimeError("governor exploded")

    with patch.object(CapacityGovernor, "can_dispatch", boom):
        assert loop._maybe_hold_for_capacity_governor() is False
