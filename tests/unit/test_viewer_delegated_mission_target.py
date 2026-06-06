"""Unit tests for viewer.resolver.delegated_vp_mission_target.

A Simone-todo Task Hub card that DELEGATED its work to a VP mission (Atlas /
general VP) keeps an assignment row pointing at the daemon's seconds-long
delegation run (``run_<id>``) — but the real work + ``trace.json`` /
``transcript.md`` live in the VP mission workspace recorded on
``metadata.result_ref``. The "📂 Workspace" button must deep-link to the
mission, not the empty delegation run. This helper extracts the mission target
``(session_id, workspace_dir)`` from the card metadata; the gateway enrichment
(``dashboard_todolist_completed`` + ``_serialize_task_hub_queue_item``) uses it
to override ``canonical_execution_*`` / ``links``.

Regression guard for the 2026-06-06 lineage bug: a completed Simone→Atlas card
opened the daemon delegation run (run.log only, no trace.json) instead of the
Atlas mission workspace where the brief was authored.
"""

from __future__ import annotations

from universal_agent.viewer.resolver import delegated_vp_mission_target

# The exact metadata shape observed live on the repro card
# (convergence-candidate:ceb5756f3e796f4b → vp-mission-ba4a9716ee7791ceec492808).
_MISSION_ID = "vp-mission-ba4a9716ee7791ceec492808"
_MISSION_WS = (
    "/opt/universal_agent/AGENT_RUN_WORKSPACES/vp_general_primary_external/"
    f"{_MISSION_ID}/{_MISSION_ID}"
)


def test_returns_mission_target_for_delegated_card():
    metadata = {
        "linked_mission_id": _MISSION_ID,
        "result_ref": f"workspace://{_MISSION_WS}",
        "delegation": {"delegate_reason": f"mission={_MISSION_ID}"},
        "vp_terminal_status": "completed",
    }
    assert delegated_vp_mission_target(metadata) == (_MISSION_ID, _MISSION_WS)


def test_strips_only_first_workspace_scheme_and_trims():
    metadata = {
        "linked_mission_id": _MISSION_ID,
        "result_ref": f"  workspace://{_MISSION_WS}  ",
    }
    assert delegated_vp_mission_target(metadata) == (_MISSION_ID, _MISSION_WS)


def test_none_when_no_result_ref():
    # Mission still running / result not yet recorded → fall back to the daemon
    # assignment workspace (prior behavior), don't fabricate a path.
    assert delegated_vp_mission_target({"linked_mission_id": _MISSION_ID}) is None


def test_none_when_result_ref_not_a_workspace_ref():
    metadata = {
        "linked_mission_id": _MISSION_ID,
        "result_ref": "https://example.com/not-a-workspace",
    }
    assert delegated_vp_mission_target(metadata) is None


def test_none_when_no_linked_mission_id():
    # A directly-executed task carries a result_ref but no linked_mission_id;
    # it isn't a delegation and must not be rerouted.
    assert delegated_vp_mission_target({"result_ref": f"workspace://{_MISSION_WS}"}) is None


def test_none_when_linked_mission_id_not_vp_mission():
    metadata = {
        "linked_mission_id": "daemon_simone_todo",
        "result_ref": f"workspace://{_MISSION_WS}",
    }
    assert delegated_vp_mission_target(metadata) is None


def test_none_when_workspace_path_empty():
    metadata = {"linked_mission_id": _MISSION_ID, "result_ref": "workspace://"}
    assert delegated_vp_mission_target(metadata) is None


def test_none_for_non_dict_input():
    assert delegated_vp_mission_target(None) is None
    assert delegated_vp_mission_target("not a dict") is None  # type: ignore[arg-type]
    assert delegated_vp_mission_target({}) is None
