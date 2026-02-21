from __future__ import annotations

import asyncio
import json
from pathlib import Path

from universal_agent.durable.db import connect_runtime_db, get_vp_db_path
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import upsert_vp_mission, upsert_vp_session
from universal_agent.tools.vp_orchestration import (
    _vp_cancel_mission_impl,
    _vp_dispatch_mission_impl,
    _vp_get_mission_impl,
    _vp_list_missions_impl,
    _vp_read_result_artifacts_impl,
    _vp_wait_mission_impl,
)


def _unwrap(result: dict) -> dict:
    assert "content" in result
    payload = result["content"][0]["text"]
    return json.loads(payload)


def test_vp_tools_dispatch_lookup_list_cancel_flow(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("UA_VP_DB_PATH", str((tmp_path / "vp_state.db").resolve()))

    dispatched = _unwrap(
        asyncio.run(
            _vp_dispatch_mission_impl(
                {
                    "vp_id": "vp.general.primary",
                    "objective": "Create an outline",
                    "mission_type": "general_task",
                    "constraints": {"topic": "ops"},
                }
            )
        )
    )
    assert dispatched["ok"] is True
    mission_id = dispatched["mission_id"]
    assert mission_id

    looked_up = _unwrap(asyncio.run(_vp_get_mission_impl({"mission_id": mission_id})))
    assert looked_up["ok"] is True
    assert looked_up["mission"]["mission_id"] == mission_id
    assert looked_up["mission"]["status"] == "queued"

    listed = _unwrap(
        asyncio.run(
            _vp_list_missions_impl(
                {"vp_id": "vp.general.primary", "status": "queued", "limit": 20}
            )
        )
    )
    mission_ids = {item["mission_id"] for item in listed["missions"]}
    assert mission_id in mission_ids

    wait_result = _unwrap(
        asyncio.run(
            _vp_wait_mission_impl(
                {"mission_id": mission_id, "timeout_seconds": 1, "poll_seconds": 1}
            )
        )
    )
    assert wait_result["ok"] is True
    assert wait_result["timed_out"] is True

    cancelled = _unwrap(
        asyncio.run(_vp_cancel_mission_impl({"mission_id": mission_id, "reason": "unit_test"}))
    )
    assert cancelled["ok"] is True
    assert cancelled["status"] == "cancel_requested"


def test_vp_read_result_artifacts_wrapper_returns_file_index(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("UA_VP_DB_PATH", str((tmp_path / "vp_state.db").resolve()))

    workspace_root = (tmp_path / "mission_workspace").resolve()
    (workspace_root / "work_products").mkdir(parents=True, exist_ok=True)
    artifact = workspace_root / "work_products" / "summary.md"
    artifact.write_text("# Summary\n\nArtifact body\n", encoding="utf-8")

    conn = connect_runtime_db(get_vp_db_path())
    try:
        ensure_schema(conn)
        upsert_vp_session(
            conn=conn,
            vp_id="vp.general.primary",
            runtime_id="runtime.general.external",
            status="active",
            session_id="vp.general.primary.external",
            workspace_dir=str(workspace_root),
        )
        upsert_vp_mission(
            conn=conn,
            mission_id="vp-mission-artifacts",
            vp_id="vp.general.primary",
            status="completed",
            objective="artifact readback",
            result_ref=f"workspace://{workspace_root}",
        )
    finally:
        conn.close()

    payload = _unwrap(
        asyncio.run(
            _vp_read_result_artifacts_impl(
                {
                    "mission_id": "vp-mission-artifacts",
                    "max_files": 10,
                    "max_bytes": 50000,
                }
            )
        )
    )
    assert payload["ok"] is True
    assert payload["files_indexed"] >= 1
    assert any(item["path"] == "work_products/summary.md" for item in payload["artifacts"])
