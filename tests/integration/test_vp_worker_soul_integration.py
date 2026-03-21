"""Integration test: VP worker loop soul seeding + mission briefing during _tick().

Verifies that when a mission is dispatched with system_prompt_injection,
the worker loop seeds the VP soul and writes the mission briefing before execution.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from universal_agent.durable.db import connect_runtime_db, get_vp_db_path
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import queue_vp_mission
from universal_agent.vp.clients.base import MissionOutcome, VpClient
from universal_agent.vp.worker_loop import VpWorkerLoop


class _StubClient(VpClient):
    """Minimal client that completes immediately and records workspace state."""

    def __init__(self) -> None:
        self.soul_content: str | None = None
        self.briefing_content: str | None = None
        self.workspace_path: Path | None = None

    async def run_mission(self, *, mission, workspace_root):
        mission_id = str(mission.get("mission_id") or mission["mission_id"])
        mission_dir = workspace_root / mission_id
        self.workspace_path = mission_dir

        # Capture what the soul seeding + briefing produced
        soul_path = mission_dir / "SOUL.md"
        if soul_path.exists():
            self.soul_content = soul_path.read_text(encoding="utf-8")

        briefing_path = mission_dir / "MISSION_BRIEFING.md"
        if briefing_path.exists():
            self.briefing_content = briefing_path.read_text(encoding="utf-8")

        # Create a minimal work product
        wp = mission_dir / "work_products"
        wp.mkdir(parents=True, exist_ok=True)
        (wp / "result.md").write_text("# Done\n", encoding="utf-8")

        return MissionOutcome(
            status="completed",
            result_ref=f"workspace://{mission_dir}",
        )


def test_tick_seeds_codie_soul_and_writes_briefing(monkeypatch, tmp_path: Path):
    """Full worker_loop._tick() integration: soul seeded + briefing written."""
    monkeypatch.setenv("UA_VP_DB_PATH", str((tmp_path / "vp_state.db").resolve()))

    conn = connect_runtime_db(get_vp_db_path())
    try:
        ensure_schema(conn)

        injection = "## Doc Fix Mission\nFix docs/README.md section 3."
        payload = {"system_prompt_injection": injection}

        queue_vp_mission(
            conn=conn,
            mission_id="soul-integration-1",
            vp_id="vp.coder.primary",
            mission_type="doc-maintenance",
            objective="Fix documentation drift",
            payload=payload,
        )

        stub = _StubClient()
        loop = VpWorkerLoop(
            conn=conn,
            vp_id="vp.coder.primary",
            workspace_base=tmp_path,
            poll_interval_seconds=1,
            lease_ttl_seconds=60,
        )
        # Inject stub client to bypass real SDK initialization
        loop._default_client = stub  # type: ignore[assignment]

        asyncio.run(loop._tick())

        # Verify soul was seeded
        assert stub.soul_content is not None, "SOUL.md should exist in workspace after _tick"
        assert "CODIE" in stub.soul_content, "Seeded SOUL.md should contain CODIE identity"

        # Verify mission briefing was written
        assert stub.briefing_content is not None, "MISSION_BRIEFING.md should exist"
        assert "Doc Fix Mission" in stub.briefing_content
        assert "README.md" in stub.briefing_content

    finally:
        conn.close()


def test_tick_with_atlas_soul_seeds_correctly(monkeypatch, tmp_path: Path):
    """Full _tick() integration for ATLAS (general VP lane)."""
    monkeypatch.setenv("UA_VP_DB_PATH", str((tmp_path / "vp_state.db").resolve()))

    conn = connect_runtime_db(get_vp_db_path())
    try:
        ensure_schema(conn)

        queue_vp_mission(
            conn=conn,
            mission_id="atlas-integration-1",
            vp_id="vp.general.primary",
            mission_type="research",
            objective="Research market trends",
            payload={},
        )

        stub = _StubClient()
        loop = VpWorkerLoop(
            conn=conn,
            vp_id="vp.general.primary",
            workspace_base=tmp_path,
            poll_interval_seconds=1,
            lease_ttl_seconds=60,
        )
        # Inject stub client to bypass real SDK initialization
        loop._default_client = stub  # type: ignore[assignment]

        asyncio.run(loop._tick())

        # Verify ATLAS soul was seeded
        assert stub.soul_content is not None, "SOUL.md should exist for ATLAS"
        assert "ATLAS" in stub.soul_content, "Seeded SOUL.md should contain ATLAS identity"

        # No briefing expected (no system_prompt_injection in payload)
        assert stub.briefing_content is None, "No MISSION_BRIEFING.md expected without injection"

    finally:
        conn.close()
