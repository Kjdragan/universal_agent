"""Unit tests for _task_hub_board_projection (PR #488b).

The projection is the bridge between the raw task_hub_items row and the
dashboard kanban card. The PR #488b additions:

- ``assigned_session_id`` prefers ``metadata.dispatch.cody_session_id``
  over the orchestrator's (Simone's) active_assignment.provider_session_id
  so the Workspace button deep-links into the Cody CLI session.

- The returned dict exposes the full ``cody_*`` set + ``delegation_target``
  so the frontend's DelegationTracePanel can render a progressive trace
  on the card.

These tests don't need the gateway running — the projection is a pure
function over dicts.
"""

from __future__ import annotations

from typing import Any

import pytest

from universal_agent.gateway_server import (
    _delegated_mission_is_running,
    _task_hub_board_projection,
)


def _item(**overrides: Any) -> dict[str, Any]:
    """Default Task Hub row shape with overrides."""
    base = {
        "task_id": "qa-test",
        "status": "delegated",
        "metadata": {},
    }
    base.update(overrides)
    return base


def _active_assignment(provider_session_id: str = "", agent_id: str = "") -> dict[str, Any]:
    return {
        "agent_id": agent_id or "todo:daemon_simone_todo",
        "provider_session_id": provider_session_id or "daemon_simone_todo",
        "state": "seized",
    }


def test_projection_prefers_cody_session_id_over_simone_provider_session_id():
    """The Workspace button must deep-link to Cody's CLI session, not Simone's."""
    cody_sid = "c18d4626-8ede-4d1b-a498-f243b05d5996"
    item = _item(
        metadata={
            "dispatch": {
                "cody_session_id": cody_sid,
            }
        }
    )
    projection = _task_hub_board_projection(
        item=item,
        active_assignment=_active_assignment(provider_session_id="daemon_simone_todo"),
    )
    assert projection["assigned_session_id"] == cody_sid
    assert projection["cody_session_id"] == cody_sid


def test_projection_falls_back_to_simone_session_when_no_cody_session_yet():
    """In-flight tasks (before Cody emits session_id) still show Simone."""
    item = _item(metadata={"dispatch": {}})
    projection = _task_hub_board_projection(
        item=item,
        active_assignment=_active_assignment(provider_session_id="daemon_simone_todo"),
    )
    assert projection["assigned_session_id"] == "daemon_simone_todo"
    assert projection["cody_session_id"] is None


def test_projection_exposes_full_cody_trace_fields():
    """All accumulated Cody identifiers are surfaced for the DelegationTracePanel."""
    item = _item(
        metadata={
            "dispatch": {
                "cody_session_id": "sess-1",
                "cody_mission_id": "vp-mission-abc",
                "cody_workspace_dir": "/opt/ua/AGENT_RUN_WORKSPACES/.../vp-mission-abc",
                "cody_worker_pid": 12345,
                "cody_dispatched_at": "2026-05-27T03:09:55Z",
            },
            "delegation": {"delegate_target": "vp.coder.primary"},
        }
    )
    projection = _task_hub_board_projection(item=item, active_assignment=None)
    assert projection["cody_mission_id"] == "vp-mission-abc"
    assert projection["cody_session_id"] == "sess-1"
    assert projection["cody_workspace_dir"].endswith("vp-mission-abc")
    assert projection["cody_worker_pid"] == 12345
    assert projection["cody_dispatched_at"] == "2026-05-27T03:09:55Z"
    assert projection["delegation_target"] == "vp.coder.primary"


def test_projection_returns_none_for_missing_cody_fields():
    """Empty dispatch metadata → all cody_* fields are None, not empty strings."""
    item = _item(metadata={})
    projection = _task_hub_board_projection(item=item, active_assignment=None)
    assert projection["cody_session_id"] is None
    assert projection["cody_mission_id"] is None
    assert projection["cody_workspace_dir"] is None
    assert projection["cody_worker_pid"] is None
    assert projection["cody_dispatched_at"] is None
    assert projection["delegation_target"] is None


def test_projection_handles_garbage_cody_worker_pid_gracefully():
    """Bogus pid types (string, negative) don't crash — just yield None."""
    item = _item(metadata={"dispatch": {"cody_worker_pid": "not-a-number"}})
    projection = _task_hub_board_projection(item=item, active_assignment=None)
    assert projection["cody_worker_pid"] is None

    item2 = _item(metadata={"dispatch": {"cody_worker_pid": -5}})
    projection2 = _task_hub_board_projection(item=item2, active_assignment=None)
    assert projection2["cody_worker_pid"] is None


def test_projection_board_lane_unchanged_by_new_fields():
    """The new fields don't perturb existing board_lane derivation."""
    item = _item(
        status="in_progress",
        metadata={"dispatch": {"cody_session_id": "x"}},
    )
    projection = _task_hub_board_projection(
        item=item,
        active_assignment=_active_assignment(),
    )
    assert projection["board_lane"] == "in_progress"


# ── M3 §4.4: running delegated mission renders in_progress (display-only) ──


def _delegated_item() -> dict[str, Any]:
    return _item(
        status="delegated",
        metadata={"delegation": {"delegate_target": "vp.general.primary"}},
    )


def test_delegated_running_mission_renders_in_progress():
    """A delegated task whose VP mission is RUNNING shows in the in_progress
    lane with the delegate (ATLAS) named — display-only, status stays delegated."""
    projection = _task_hub_board_projection(
        item=_delegated_item(), active_assignment=None, mission_running=True
    )
    assert projection["board_lane"] == "in_progress"
    # The delegate target is still surfaced as the assignee on the card.
    assert projection["assigned_agent_id"] == "vp.general.primary"
    # assignment_state stays 'delegated' (no fake active-assignment promotion).
    assert projection["assignment_state"] == "delegated"


def test_delegated_queued_mission_stays_not_assigned():
    """A delegated task whose mission has NOT been picked up (no live lease)
    stays in not_assigned — the prior behaviour."""
    projection = _task_hub_board_projection(
        item=_delegated_item(), active_assignment=None, mission_running=False
    )
    assert projection["board_lane"] == "not_assigned"
    assert projection["assigned_agent_id"] == "vp.general.primary"


def test_delegated_mission_running_defaults_false():
    """mission_running defaults False, so existing callers keep not_assigned."""
    projection = _task_hub_board_projection(item=_delegated_item(), active_assignment=None)
    assert projection["board_lane"] == "not_assigned"


def test_pending_review_never_promoted_to_in_progress():
    """pending_review (VP done, awaiting Simone) must stay needs_review even if
    a (stale) running flag is passed — only status='delegated' is promoted."""
    item = _item(status="pending_review", metadata={"delegation": {"delegate_target": "vp.general.primary"}})
    projection = _task_hub_board_projection(item=item, active_assignment=None, mission_running=True)
    assert projection["board_lane"] == "needs_review"


def test_open_task_never_promoted_by_mission_running():
    """A non-delegated open task is unaffected by mission_running."""
    item = _item(status="open", metadata={})
    projection = _task_hub_board_projection(item=item, active_assignment=None, mission_running=True)
    assert projection["board_lane"] == "not_assigned"


# ── _delegated_mission_is_running: mission-id fallback chain + gates ──


def test_delegated_running_resolves_via_delegation_mission_id():
    """Primary key — delegation.mission_id (same key the completion bridge uses)."""
    item = _item(status="delegated", metadata={"delegation": {"mission_id": "vp-mission-A"}})
    assert _delegated_mission_is_running(item, {"vp-mission-A"}) is True
    assert _delegated_mission_is_running(item, {"vp-mission-OTHER"}) is False


def test_delegated_running_falls_back_to_linked_then_dispatch_mission_id():
    """Fallbacks: top-level linked_mission_id, then dispatch.vp_mission_id."""
    linked = _item(status="delegated", metadata={"linked_mission_id": "vp-mission-B"})
    assert _delegated_mission_is_running(linked, {"vp-mission-B"}) is True
    dispatch = _item(status="delegated", metadata={"dispatch": {"vp_mission_id": "vp-mission-C"}})
    assert _delegated_mission_is_running(dispatch, {"vp-mission-C"}) is True


def test_delegated_no_resolvable_mission_id_is_false():
    """Delegated card with no mission id anywhere → not running (stays not_assigned)."""
    item = _item(status="delegated", metadata={"delegation": {"delegate_target": "vp.general.primary"}})
    assert _delegated_mission_is_running(item, {"vp-mission-A"}) is False


def test_delegated_mission_running_guards():
    """None/empty live set → False; non-delegated status → False even if id is live."""
    item = _item(status="delegated", metadata={"delegation": {"mission_id": "vp-mission-A"}})
    assert _delegated_mission_is_running(item, None) is False
    assert _delegated_mission_is_running(item, set()) is False
    open_item = _item(status="open", metadata={"delegation": {"mission_id": "vp-mission-A"}})
    assert _delegated_mission_is_running(open_item, {"vp-mission-A"}) is False
