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

from universal_agent.gateway_server import _task_hub_board_projection


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
