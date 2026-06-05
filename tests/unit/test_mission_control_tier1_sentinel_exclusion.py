"""Tier-1 evidence must NOT leak the `__tierN_meta__` sentinel rows.

`mission_control_tile_states` holds the canonical traffic-light tiles AND the
sweeper's own `__tier1_meta__` / `__tier2_meta__` cadence-bookkeeping sentinel
rows (written by mission_control_intelligence_sweeper.py). `collect_tier1_evidence`
used to `SELECT *` the whole table into `evidence["tier0_tiles"]`, so the two
sentinels leaked into the tier-1 LLM prompt (and the evidence signature). They
are not real tiles and carry no operator signal — this guards their exclusion.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from universal_agent.services.mission_control_db import open_store
from universal_agent.services.mission_control_tier1 import collect_tier1_evidence


def _seed_tile(conn, tile_id: str, state: str = "green") -> None:
    ts = "2026-06-05T00:00:00+00:00"
    conn.execute(
        "INSERT INTO mission_control_tile_states "
        "(tile_id, current_state, state_since, last_signature, last_checked_at, current_annotation) "
        "VALUES (?, ?, ?, '', ?, '')",
        (tile_id, state, ts, ts),
    )


def test_collect_tier1_evidence_excludes_meta_sentinels(tmp_path: Path):
    mc = open_store(tmp_path / "mc.db")
    try:
        for tid in ("gateway", "csi_ingester", "__tier1_meta__", "__tier2_meta__"):
            _seed_tile(mc, tid)
        mc.commit()

        # Empty in-memory activity DB: the task/event queries raise
        # OperationalError and fall back to [], which is all this test needs.
        activity = sqlite3.connect(":memory:")
        try:
            evidence = collect_tier1_evidence(activity, mc)
        finally:
            activity.close()

        tile_ids = {t["tile_id"] for t in evidence["tier0_tiles"]}
        assert tile_ids == {"gateway", "csi_ingester"}, tile_ids
        assert "__tier1_meta__" not in tile_ids
        assert "__tier2_meta__" not in tile_ids
        # counts must reflect the filtered snapshot
        assert evidence["counts"]["tier0_tiles"] == 2
    finally:
        mc.close()


def test_collect_tier1_evidence_keeps_real_tiles_when_no_sentinels(tmp_path: Path):
    """Sanity: the filter does not drop legitimate tiles."""
    mc = open_store(tmp_path / "mc.db")
    try:
        for tid in ("gateway", "database", "heartbeat_daemon"):
            _seed_tile(mc, tid, state="yellow")
        mc.commit()
        activity = sqlite3.connect(":memory:")
        try:
            evidence = collect_tier1_evidence(activity, mc)
        finally:
            activity.close()
        assert {t["tile_id"] for t in evidence["tier0_tiles"]} == {
            "gateway",
            "database",
            "heartbeat_daemon",
        }
    finally:
        mc.close()
