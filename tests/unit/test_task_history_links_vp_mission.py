"""Tests for VP-mission workspace resolution in the Task Hub completed-card links.

Two regressions to lock in:

1. ``_task_history_links_for_session`` must produce the full sub-path under
   ``WORKSPACES_DIR`` for deeply-nested VP-mission workspaces — using only
   ``Path(wdir).name`` truncates parents and breaks the storage-explorer URL.

2. ``dashboard_todolist_completed`` must prefer ``metadata.dispatch.cody_*``
   (workspace_dir / mission_id) over the orchestrator's assignment row when
   building Workspace-button links. The orchestrator's assignment points at
   Simone's daemon directory (where she logged the redirect_to), not at the
   VP mission workspace where the artifacts actually live.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any
from unittest.mock import patch


def _load_gateway_module(workspaces_dir: Path):
    """Import gateway_server with WORKSPACES_DIR pointing at a test root.

    Re-imported per-test so the module-level WORKSPACES_DIR captures the
    patched env var (the module resolves it at import time).
    """
    import os

    os.environ["UA_WORKSPACES_DIR"] = str(workspaces_dir)
    import universal_agent.gateway_server as gs  # noqa: E402
    importlib.reload(gs)
    return gs


def test_link_builder_resolves_nested_vp_mission_workspace(tmp_path: Path) -> None:
    """Deeply-nested VP workspaces must expose their full relative path.

    Cody's mission workspace lives at
    ``WORKSPACES_DIR/vp_coder_primary_external/vp-mission-<id>/vp-mission-<id>``.
    The pre-fix builder produced ``vp-mission-<id>`` only, so the storage
    explorer href pointed at the wrong directory.
    """
    workspaces = tmp_path / "AGENT_RUN_WORKSPACES"
    mission_id = "vp-mission-30840cc1438bd8553cc02083"
    nested = workspaces / "vp_coder_primary_external" / mission_id / mission_id
    nested.mkdir(parents=True)
    (nested / "run.log").write_text("hello cody")

    gs = _load_gateway_module(workspaces)

    links = gs._task_history_links_for_session(mission_id, workspace_dir=str(nested))

    expected_rel = f"vp_coder_primary_external/{mission_id}/{mission_id}"
    assert links["workspace_name"] == expected_rel
    assert links["workspace_dir"] == str(nested)
    # The href URL-encodes path separators; compare against the encoded form.
    from urllib.parse import quote
    assert quote(expected_rel, safe="") in links["run_log_href"]
    assert links["run_log_path"].endswith(f"{expected_rel}/run.log")


def test_link_builder_leaves_top_level_daemon_workspaces_unchanged(tmp_path: Path) -> None:
    """Regression guard: top-level daemon runs must still resolve to their leaf name."""
    workspaces = tmp_path / "AGENT_RUN_WORKSPACES"
    workspaces.mkdir()
    run_dir = workspaces / "run_d1640d53fa1c"
    run_dir.mkdir()
    (run_dir / "run.log").write_text("simone")

    gs = _load_gateway_module(workspaces)

    links = gs._task_history_links_for_session(
        "daemon_simone_todo", workspace_dir=str(run_dir)
    )

    assert links["workspace_name"] == "run_d1640d53fa1c"
    assert links["session_id"] == "daemon_simone_todo"


def test_link_builder_falls_back_to_relative_path_for_missing_nested_dir(tmp_path: Path) -> None:
    """When a nested workspace doesn't exist on disk yet, still surface the
    full sub-path so the storage explorer URL points where the workspace
    *will* materialize, not at a truncated leaf name."""
    workspaces = tmp_path / "AGENT_RUN_WORKSPACES"
    workspaces.mkdir()
    mission_id = "vp-mission-deadbeef"
    nested = workspaces / "vp_coder_primary_external" / mission_id / mission_id
    # Intentionally do NOT mkdir; the resolver must still compute the rel path.

    gs = _load_gateway_module(workspaces)

    links = gs._task_history_links_for_session(mission_id, workspace_dir=str(nested))

    expected_rel = f"vp_coder_primary_external/{mission_id}/{mission_id}"
    assert links["workspace_name"] == expected_rel


def test_completed_enrichment_prefers_cody_workspace_over_assignment(tmp_path: Path) -> None:
    """When ``metadata.dispatch.cody_workspace_dir`` is present, the
    completed payload's ``links`` must deep-link to Cody's mission
    workspace, not Simone's daemon assignment dir."""
    workspaces = tmp_path / "AGENT_RUN_WORKSPACES"
    mission_id = "vp-mission-30840cc1438bd8553cc02083"
    cody_ws = workspaces / "vp_coder_primary_external" / mission_id / mission_id
    cody_ws.mkdir(parents=True)
    (cody_ws / "run.log").write_text("cody")

    simone_ws = workspaces / "run_d1640d53fa1c"
    simone_ws.mkdir()
    (simone_ws / "run.log").write_text("simone")

    gs = _load_gateway_module(workspaces)

    fake_rows = [
        {
            "task_id": "qa-635576484bc1",
            "status": "completed",
            "metadata": {
                "workflow_manifest": {"target_agent": "vp.coder.primary"},
                "use_goal_loop": True,
                "linked_mission_id": mission_id,
                "result_ref": f"workspace://{cody_ws}",
                "dispatch": {
                    "cody_mission_id": mission_id,
                    "cody_session_id": "2fea13af-e479-4a85-b2e4-d62da00a4d7a",
                    "cody_workspace_dir": str(cody_ws),
                    "cody_worker_pid": 3355907,
                    "last_disposition": "completed",
                },
            },
            "last_assignment": {
                "session_id": "daemon_simone_todo",
                "workspace_dir": str(simone_ws),
                "workflow_run_id": "run_d1640d53fa1c",
                "agent_id": "todo:daemon_simone_todo",
                "state": "completed",
            },
        }
    ]

    captured: dict[str, Any] = {}

    def _fake_list_completed(_conn: Any, *, limit: int = 80) -> list[dict[str, Any]]:
        captured["limit"] = limit
        return list(fake_rows)

    with patch.object(gs.task_hub, "list_completed_tasks", _fake_list_completed), \
         patch.object(gs.task_hub, "list_completed_cron_runs", lambda *_a, **_k: []):
        class _FakeConn:
            def close(self) -> None:
                return None

        with patch.object(gs, "_task_hub_open_conn", lambda: _FakeConn()):
            with patch.object(gs, "_activity_store_lock") as lock:
                lock.__enter__ = lambda self: None
                lock.__exit__ = lambda self, *_a: None
                import asyncio

                payload = asyncio.run(gs.dashboard_todolist_completed(limit=10))

    assert payload["status"] == "ok"
    item = payload["items"][0]
    expected_rel = f"vp_coder_primary_external/{mission_id}/{mission_id}"
    # The Workspace button's deep-link target must point at Cody's workspace.
    assert item["links"]["workspace_dir"] == str(cody_ws)
    assert item["links"]["workspace_name"] == expected_rel
    # vp-mission-* session_id triggers the frontend VP-mission special case.
    assert item["links"]["session_id"] == mission_id
    # Canonical execution fields propagated so the frontend resolver picks them up.
    assert item["canonical_execution_session_id"] == mission_id
    assert item["canonical_execution_workspace"] == str(cody_ws)


def test_completed_enrichment_falls_through_for_non_delegated_tasks(tmp_path: Path) -> None:
    """When the task has no ``dispatch.cody_*`` metadata (Simone executed
    the task herself), preserve the existing behavior of pointing at her
    assignment workspace."""
    workspaces = tmp_path / "AGENT_RUN_WORKSPACES"
    workspaces.mkdir()
    simone_ws = workspaces / "run_d1640d53fa1c"
    simone_ws.mkdir()
    (simone_ws / "run.log").write_text("simone")

    gs = _load_gateway_module(workspaces)

    fake_rows = [
        {
            "task_id": "qa-self-exec",
            "status": "completed",
            "metadata": {},
            "last_assignment": {
                "session_id": "daemon_simone_todo",
                "workspace_dir": str(simone_ws),
                "workflow_run_id": "run_d1640d53fa1c",
                "agent_id": "todo:daemon_simone_todo",
                "state": "completed",
            },
        }
    ]

    def _fake_list_completed(_conn: Any, *, limit: int = 80) -> list[dict[str, Any]]:
        return list(fake_rows)

    with patch.object(gs.task_hub, "list_completed_tasks", _fake_list_completed), \
         patch.object(gs.task_hub, "list_completed_cron_runs", lambda *_a, **_k: []):
        class _FakeConn:
            def close(self) -> None:
                return None

        with patch.object(gs, "_task_hub_open_conn", lambda: _FakeConn()):
            with patch.object(gs, "_activity_store_lock") as lock:
                lock.__enter__ = lambda self: None
                lock.__exit__ = lambda self, *_a: None
                import asyncio

                payload = asyncio.run(gs.dashboard_todolist_completed(limit=10))

    item = payload["items"][0]
    assert item["links"]["session_id"] == "daemon_simone_todo"
    assert item["links"]["workspace_dir"] == str(simone_ws)
    assert item["canonical_execution_session_id"] == "daemon_simone_todo"


def test_completed_enrichment_stamps_vp_mission_id_for_direct_missions(tmp_path: Path) -> None:
    """Direct VP missions (dispatch_channel=agent_tool, e.g. the daily
    autonomous briefing) have no assignment row and no dispatch.cody_*
    metadata — only a ``result_ref`` workspace pointer and
    ``source_kind=vp_mission``. The completed enrichment must stamp the
    mission's own ``vp-mission-<id>`` task_id as the canonical session id so
    the frontend resolver returns a *session* target and the three-panel
    Workspace view populates. Pre-fix this yielded ``session_id=None`` →
    a run-only target → empty three-panel view.
    """
    workspaces = tmp_path / "AGENT_RUN_WORKSPACES"
    mission_id = "vp-mission-f17cd18dac2f708c1a4a9e2c"
    mission_ws = workspaces / "vp_general_primary_external" / mission_id / mission_id
    mission_ws.mkdir(parents=True)
    (mission_ws / "run.log").write_text("briefing run")

    gs = _load_gateway_module(workspaces)

    fake_rows = [
        {
            "task_id": mission_id,
            "source_kind": "vp_mission",
            "status": "completed",
            "metadata": {
                "vp_id": "vp.general.primary",
                "mission_type": "briefing",
                "dispatch_channel": "agent_tool",
                "vp_terminal_status": "completed",
                "result_ref": f"workspace://{mission_ws}",
                "terminal_disposition": "completed_without_pr",
            },
            # Direct missions complete without a Task Hub assignment row.
            "last_assignment": None,
        }
    ]

    def _fake_list_completed(_conn: Any, *, limit: int = 80) -> list[dict[str, Any]]:
        return list(fake_rows)

    with patch.object(gs.task_hub, "list_completed_tasks", _fake_list_completed), \
         patch.object(gs.task_hub, "list_completed_cron_runs", lambda *_a, **_k: []):
        class _FakeConn:
            def close(self) -> None:
                return None

        with patch.object(gs, "_task_hub_open_conn", lambda: _FakeConn()):
            with patch.object(gs, "_activity_store_lock") as lock:
                lock.__enter__ = lambda self: None
                lock.__exit__ = lambda self, *_a: None
                import asyncio

                payload = asyncio.run(gs.dashboard_todolist_completed(limit=10))

    item = payload["items"][0]
    expected_rel = f"vp_general_primary_external/{mission_id}/{mission_id}"
    # The mission's task_id is stamped as the canonical/link session id.
    assert item["canonical_execution_session_id"] == mission_id
    assert item["links"]["session_id"] == mission_id
    # Workspace deep-link resolves from the result_ref pointer.
    assert item["canonical_execution_workspace"] == str(mission_ws)
    assert item["links"]["workspace_dir"] == str(mission_ws)
    assert item["links"]["workspace_name"] == expected_rel
