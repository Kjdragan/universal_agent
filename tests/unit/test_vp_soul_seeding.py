"""Tests for VP soul seeding and mission briefing in worker_loop.

Covers: _seed_vp_soul(), _write_mission_briefing(), _resolve_mission_workspace()
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import queue_vp_mission
from universal_agent.vp.worker_loop import VpWorkerLoop


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    ensure_schema(conn)
    return conn


def _make_loop(
    conn: sqlite3.Connection,
    vp_id: str,
    workspace_base: Path,
) -> VpWorkerLoop:
    return VpWorkerLoop(
        conn=conn,
        vp_id=vp_id,
        workspace_base=workspace_base,
        poll_interval_seconds=1,
        lease_ttl_seconds=60,
    )


def _make_mission(mission_id: str, payload: dict | None = None) -> dict:
    """Build a dict with the fields _seed_vp_soul / _write_mission_briefing expect."""
    return {
        "mission_id": mission_id,
        "payload_json": json.dumps(payload or {}),
    }


def test_seed_vp_soul_copies_correct_file(tmp_path: Path):
    """_seed_vp_soul copies CODIE_SOUL.md into workspace as SOUL.md."""
    conn = _conn()
    loop = _make_loop(conn, "vp.coder.primary", tmp_path)

    mission = _make_mission("soul-test-1")
    loop._seed_vp_soul(mission)

    workspace = loop._resolve_mission_workspace(mission)
    soul_path = workspace / "SOUL.md"
    assert soul_path.exists(), "SOUL.md should be seeded into workspace"
    content = soul_path.read_text(encoding="utf-8")
    assert "CODIE" in content, "Seeded SOUL.md should contain CODIE identity"


def test_seed_vp_soul_atlas_profile(tmp_path: Path):
    """_seed_vp_soul copies ATLAS_SOUL.md for general VP lane."""
    conn = _conn()
    loop = _make_loop(conn, "vp.general.primary", tmp_path)

    mission = _make_mission("soul-test-atlas")
    loop._seed_vp_soul(mission)

    workspace = loop._resolve_mission_workspace(mission)
    soul_path = workspace / "SOUL.md"
    assert soul_path.exists(), "SOUL.md should be seeded for ATLAS"
    content = soul_path.read_text(encoding="utf-8")
    assert "ATLAS" in content, "Seeded SOUL.md should contain ATLAS identity"


def test_seed_vp_soul_no_overwrite_existing(tmp_path: Path):
    """Existing SOUL.md in workspace should NOT be overwritten."""
    conn = _conn()
    loop = _make_loop(conn, "vp.coder.primary", tmp_path)

    mission = _make_mission("soul-no-overwrite")
    workspace = loop._resolve_mission_workspace(mission)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "SOUL.md").write_text("ORIGINAL SOUL CONTENT\n", encoding="utf-8")

    loop._seed_vp_soul(mission)

    content = (workspace / "SOUL.md").read_text(encoding="utf-8")
    assert "ORIGINAL SOUL CONTENT" in content, "Existing SOUL.md should not be overwritten"
    assert "CODIE" not in content


def test_seed_vp_soul_missing_source_logs_warning(tmp_path: Path, monkeypatch):
    """Missing soul file doesn't crash, logs warning."""
    conn = _conn()
    loop = _make_loop(conn, "vp.coder.primary", tmp_path)
    # VpProfile is frozen; use object.__setattr__ to bypass
    object.__setattr__(loop.profile, "soul_file", "NONEXISTENT_SOUL.md")

    mission = _make_mission("soul-missing")
    # Should not raise
    loop._seed_vp_soul(mission)

    workspace = loop._resolve_mission_workspace(mission)
    soul_path = workspace / "SOUL.md"
    assert not soul_path.exists(), "SOUL.md should NOT exist when source is missing"


def test_write_mission_briefing_from_payload(tmp_path: Path):
    """Payload system_prompt_injection is written to MISSION_BRIEFING.md."""
    conn = _conn()
    injection_text = "## Custom Mission\nDo the special thing."
    payload = {"system_prompt_injection": injection_text}

    loop = _make_loop(conn, "vp.coder.primary", tmp_path)
    mission = _make_mission("briefing-test", payload)
    loop._write_mission_briefing(mission)

    workspace = loop._resolve_mission_workspace(mission)
    briefing_path = workspace / "MISSION_BRIEFING.md"
    assert briefing_path.exists(), "MISSION_BRIEFING.md should be written"
    content = briefing_path.read_text(encoding="utf-8")
    assert "Custom Mission" in content
    assert "special thing" in content


def test_write_mission_briefing_missing_key(tmp_path: Path):
    """No system_prompt_injection key → no file written."""
    conn = _conn()
    loop = _make_loop(conn, "vp.coder.primary", tmp_path)
    mission = _make_mission("briefing-absent", {"other_key": "value"})
    loop._write_mission_briefing(mission)

    workspace = loop._resolve_mission_workspace(mission)
    briefing_path = workspace / "MISSION_BRIEFING.md"
    assert not briefing_path.exists(), "MISSION_BRIEFING.md should NOT be created"
