"""Terminal-mirror status map regression (top-9 handoff, task 7).

A failed VP mission's Kanban mirror row (task_id == mission_id) previously
mapped to TASK_STATUS_OPEN — but mirror rows are unclaimable by design
(agent_ready=False + forbidden_source_kinds), so nothing could ever act on
that "open" and every failed mission left a zombie row forever (79 of the
104 orphans triaged 2026-07-23). Every terminal mission event must map the
mirror to a TERMINAL Task Hub status.
"""

from __future__ import annotations

from universal_agent import task_hub
from universal_agent.vp.worker_loop import TERMINAL_MIRROR_STATUS_MAP


def test_every_terminal_event_maps_to_a_terminal_status():
    assert set(TERMINAL_MIRROR_STATUS_MAP) == {
        "vp.mission.completed",
        "vp.mission.failed",
        "vp.mission.cancelled",
    }
    for event, status in TERMINAL_MIRROR_STATUS_MAP.items():
        assert status in task_hub.TERMINAL_STATUSES, (
            f"{event} maps to non-terminal status {status!r} — this recreates "
            "the zombie-open-mirror class (unclaimable rows stuck open forever)"
        )


def test_failed_maps_to_parked_not_open():
    assert TERMINAL_MIRROR_STATUS_MAP["vp.mission.failed"] == task_hub.TASK_STATUS_PARKED
    assert TERMINAL_MIRROR_STATUS_MAP["vp.mission.failed"] != task_hub.TASK_STATUS_OPEN
