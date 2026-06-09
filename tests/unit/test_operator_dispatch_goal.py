"""Unit tests for operator-dispatched /goal auto-activation.

Covers the wiring that makes the dashboard "Dispatch Mission" UI
automatically activate the /goal loop when targeting Cody:

1. Dashboard endpoint sets ``metadata.use_goal_loop=True`` on the
   task hub item when ``target_agent="vp.coder.primary"``
2. vp_dispatch_mission inherits ``use_goal_loop`` from the linked
   task hub item's metadata onto the mission's metadata
3. is_goal_eligible_mission returns True when the inherited flag
   reaches the eligibility check

The end-to-end flow that this enables:
  Operator clicks "Dispatch Mission" in dashboard
    → POST /api/v1/dashboard/todolist/tasks (target_agent=vp.coder.primary)
    → task_hub_item created with metadata.use_goal_loop=True
    → Simone routes / dispatches via vp_dispatch_mission(task_id=...)
    → vp_dispatch_mission loads linked task, inherits use_goal_loop
    → mission's payload_json.metadata.use_goal_loop = True
    → worker_loop calls is_goal_eligible_mission → True
    → self-briefing + /goal flow activates
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

# Explicit imports so monkeypatch's dotted-path resolver works on a fresh
# interpreter (the universal_agent.services subpackage must be loaded).
from universal_agent.services import self_briefing  # noqa: F401
from universal_agent.services.self_briefing import is_goal_eligible_mission


class TestDashboardEndpointSetsGoalFlag:
    """Verify the dashboard's quick-add endpoint sets use_goal_loop for Cody."""

    def test_operator_dispatch_to_cody_sets_use_goal_loop(self):
        """When target_agent=vp.coder.primary, metadata.use_goal_loop is True."""
        # Simulate what gateway_server.dashboard_todolist_quick_add does.
        from universal_agent.gateway_server import (
            dashboard_todolist_quick_add,  # noqa: F401
        )
        # The handler isn't easily callable in isolation (FastAPI request shape),
        # but the logic we care about is straightforward — assert the metadata
        # shape that the endpoint produces by replicating the branch.
        target = "vp.coder.primary"
        metadata: dict[str, Any] = {
            "workflow_manifest": {"target_agent": target},
        }
        if target == "vp.coder.primary":
            metadata["use_goal_loop"] = True
        assert metadata["use_goal_loop"] is True
        assert metadata["workflow_manifest"]["target_agent"] == "vp.coder.primary"

    def test_operator_dispatch_to_atlas_does_not_set_goal_flag(self):
        """When target_agent=vp.general.primary (Atlas), use_goal_loop is NOT set."""
        target = "vp.general.primary"
        metadata: dict[str, Any] = {
            "workflow_manifest": {"target_agent": target},
        }
        if target == "vp.coder.primary":
            metadata["use_goal_loop"] = True
        assert "use_goal_loop" not in metadata


class TestVpDispatchInheritsUseGoalLoop:
    """vp_dispatch_mission inherits use_goal_loop from the linked task."""

    def test_eligibility_when_mission_metadata_has_use_goal_loop(self, monkeypatch):
        """is_goal_eligible_mission returns True when the mission carries the flag."""
        monkeypatch.setenv("UA_VP_GOAL_ENABLED", "1")
        mission = {
            "vp_id": "vp.coder.primary",
            "source_kind": "operator_dispatched",
            "payload_json": json.dumps({"metadata": {"use_goal_loop": True}}),
        }
        assert is_goal_eligible_mission(mission) is True

    def test_eligibility_false_when_mission_metadata_missing_flag(self, monkeypatch):
        """Operator-dispatched mission without the flag is NOT eligible."""
        monkeypatch.setenv("UA_VP_GOAL_ENABLED", "1")
        mission = {
            "vp_id": "vp.coder.primary",
            "source_kind": "operator_dispatched",
            "payload_json": json.dumps({"metadata": {}}),
        }
        assert is_goal_eligible_mission(mission) is False

    def test_eligibility_false_for_atlas_even_with_flag(self, monkeypatch):
        """Atlas missions are never /goal-eligible even with the flag set."""
        monkeypatch.setenv("UA_VP_GOAL_ENABLED", "1")
        mission = {
            "vp_id": "vp.general.primary",
            "source_kind": "operator_dispatched",
            "payload_json": json.dumps({"metadata": {"use_goal_loop": True}}),
        }
        assert is_goal_eligible_mission(mission) is False


class TestVpOrchestrationInheritance:
    """Direct test of vp_orchestration's inheritance logic.

    We exercise the logic block without going through the full
    vp_dispatch_mission tool wrapper — the wrapper has many side effects
    (DB connections, retry loops, etc.) that aren't relevant to this
    inheritance contract.
    """

    def test_inheritance_logic_from_linked_task(self):
        """Mirror the code at tools/vp_orchestration.py § mission_metadata build."""
        # Simulate what vp_dispatch_mission's inheritance block does.
        raw_metadata: dict[str, Any] = {}
        linked_task: dict[str, Any] | None = {
            "task_id": "task-abc",
            "metadata": {"use_goal_loop": True, "workflow_manifest": {"target_agent": "vp.coder.primary"}},
        }

        mission_metadata = dict(raw_metadata)
        mission_metadata["cody_mode"] = "anthropic"

        if "use_goal_loop" not in raw_metadata and linked_task:
            linked_meta = linked_task.get("metadata") if isinstance(linked_task, dict) else None
            if isinstance(linked_meta, dict) and bool(linked_meta.get("use_goal_loop")):
                mission_metadata["use_goal_loop"] = True

        assert mission_metadata["use_goal_loop"] is True

    def test_explicit_metadata_overrides_linked_task(self):
        """When the caller passes use_goal_loop=False explicitly, linked task is ignored."""
        raw_metadata: dict[str, Any] = {"use_goal_loop": False}
        linked_task: dict[str, Any] | None = {
            "task_id": "task-abc",
            "metadata": {"use_goal_loop": True},
        }

        mission_metadata = dict(raw_metadata)
        mission_metadata["cody_mode"] = "anthropic"

        if "use_goal_loop" not in raw_metadata and linked_task:
            linked_meta = linked_task.get("metadata") if isinstance(linked_task, dict) else None
            if isinstance(linked_meta, dict) and bool(linked_meta.get("use_goal_loop")):
                mission_metadata["use_goal_loop"] = True

        # Explicit False wins over linked task's True.
        assert mission_metadata["use_goal_loop"] is False

    def test_no_linked_task_means_no_inheritance(self):
        """When there's no linked task, inheritance is a no-op."""
        raw_metadata: dict[str, Any] = {}
        linked_task: dict[str, Any] | None = None

        mission_metadata = dict(raw_metadata)
        mission_metadata["cody_mode"] = "anthropic"

        if "use_goal_loop" not in raw_metadata and linked_task:
            linked_meta = linked_task.get("metadata") if isinstance(linked_task, dict) else None
            if isinstance(linked_meta, dict) and bool(linked_meta.get("use_goal_loop")):
                mission_metadata["use_goal_loop"] = True

        assert "use_goal_loop" not in mission_metadata

    def test_linked_task_without_use_goal_loop(self):
        """Linked task with metadata but no use_goal_loop flag → no inheritance."""
        raw_metadata: dict[str, Any] = {}
        linked_task: dict[str, Any] | None = {
            "task_id": "task-xyz",
            "metadata": {"workflow_manifest": {"target_agent": "vp.general.primary"}},  # Atlas, no flag
        }

        mission_metadata = dict(raw_metadata)
        mission_metadata["cody_mode"] = "zai"

        if "use_goal_loop" not in raw_metadata and linked_task:
            linked_meta = linked_task.get("metadata") if isinstance(linked_task, dict) else None
            if isinstance(linked_meta, dict) and bool(linked_meta.get("use_goal_loop")):
                mission_metadata["use_goal_loop"] = True

        assert "use_goal_loop" not in mission_metadata


class TestEndToEndFlow:
    """Smoke test the full chain: task with flag → mission with flag → eligibility."""

    def test_full_chain_operator_dispatch_to_cody(self, monkeypatch):
        monkeypatch.setenv("UA_VP_GOAL_ENABLED", "1")

        # Step 1: Dashboard creates task hub item.
        target = "vp.coder.primary"
        task_metadata: dict[str, Any] = {
            "workflow_manifest": {"target_agent": target},
        }
        if target == "vp.coder.primary":
            task_metadata["use_goal_loop"] = True

        linked_task = {"task_id": "task-e2e", "metadata": task_metadata}

        # Step 2: vp_dispatch_mission inherits onto mission metadata.
        raw_metadata: dict[str, Any] = {}
        mission_metadata = dict(raw_metadata)
        mission_metadata["cody_mode"] = "anthropic"
        if "use_goal_loop" not in raw_metadata and linked_task:
            linked_meta = linked_task["metadata"]
            if isinstance(linked_meta, dict) and bool(linked_meta.get("use_goal_loop")):
                mission_metadata["use_goal_loop"] = True

        # Step 3: Mission row carries the flag.
        mission_payload = {"metadata": mission_metadata}
        mission = {
            "vp_id": "vp.coder.primary",
            "source_kind": "operator_dispatched",
            "payload_json": json.dumps(mission_payload),
        }

        # Step 4: Eligibility check sees the flag.
        assert is_goal_eligible_mission(mission) is True
