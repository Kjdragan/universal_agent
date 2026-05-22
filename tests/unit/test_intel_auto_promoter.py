"""Tests for the intel auto-promoter.

Pins the contract that:
  - candidates below the score floor are skipped (not approved)
  - the daily cap binds (highest-score wins)
  - the `decided_by` stamp carries score + run-id for audit
  - the daily-cap counter ignores manual-approval decisions
  - dry-run reports but writes nothing
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from universal_agent import task_hub
from universal_agent.services import (
    csi_demo_triage as triage,
    intel_auto_promoter as promoter,
)


def _open_triage(tmp_path: Path) -> sqlite3.Connection:
    return triage.open_db(artifacts_root=tmp_path)


def _open_task_hub(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "task_hub.db"))
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _write_packet(tmp_path: Path, actions: list[dict[str, Any]]) -> Path:
    packet_dir = tmp_path / "packets" / "2026-05-22" / "auto_promoter__test"
    packet_dir.mkdir(parents=True, exist_ok=True)
    (packet_dir / "manifest.json").write_text(
        json.dumps({"handle": "ClaudeDevs", "lane": "claude_code_intel"}),
        encoding="utf-8",
    )
    (packet_dir / "actions_refined.json").write_text(json.dumps(actions), encoding="utf-8")
    return packet_dir


def _action(post_id: str, *, tier: int = 3) -> dict[str, Any]:
    return {
        "post_id": post_id,
        "tier": tier,
        "action_type": "demo_task",
        "url": f"https://x.com/ClaudeDevs/status/{post_id}",
        "text": f"Test post {post_id}",
        "links": [],
    }


def _seed_with_scores(tmp_path: Path, scores: dict[str, float]) -> tuple[sqlite3.Connection, sqlite3.Connection]:
    """Insert candidates from `scores` map and stamp their ranking_score."""
    actions = [_action(pid) for pid in scores]
    packet = _write_packet(tmp_path, actions)
    triage.sync_candidates_from_packet(packet_dir=packet, artifacts_root=tmp_path)

    triage_conn = _open_triage(tmp_path)
    th_conn = _open_task_hub(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    for post_id, score in scores.items():
        triage_conn.execute(
            """
            UPDATE demo_triage_candidates
               SET ranking_score = ?,
                   ranking_evaluated_at = ?,
                   ranking_run_id = 'test'
             WHERE post_id = ?
            """,
            (float(score), now, post_id),
        )
    triage_conn.commit()
    return triage_conn, th_conn


def test_promoter_promotes_above_threshold_in_score_order(tmp_path: Path):
    triage_conn, th_conn = _seed_with_scores(
        tmp_path,
        {"high": 8.5, "highest": 9.2, "below": 6.0},
    )

    result = promoter.promote_top_candidates(
        conn=triage_conn,
        task_hub_conn=th_conn,
        min_score=7.5,
        daily_cap=5,
    )

    assert result.error is None
    assert result.candidates_eligible == 2  # high + highest
    assert result.skipped_below_score == 1  # below
    assert result.promoted_this_run == 2
    assert result.promoted_post_ids == ["highest", "high"]  # score-desc order

    rows = th_conn.execute(
        "SELECT task_id, source_kind FROM task_hub_items ORDER BY task_id"
    ).fetchall()
    assert len(rows) == 2
    assert all(r["source_kind"] == "cody_scaffold_request" for r in rows)

    # Audit trail: decided_by stamps include score + run-id.
    decided_rows = triage_conn.execute(
        "SELECT post_id, decided_by FROM demo_triage_candidates "
        "WHERE state = 'approved' ORDER BY post_id"
    ).fetchall()
    decided_map = {r["post_id"]: r["decided_by"] for r in decided_rows}
    assert decided_map["highest"].startswith("auto_promoter:score=9.2:run=")
    assert decided_map["high"].startswith("auto_promoter:score=8.5:run=")


def test_promoter_respects_daily_cap(tmp_path: Path):
    triage_conn, th_conn = _seed_with_scores(
        tmp_path,
        {"a": 9.0, "b": 8.5, "c": 8.0},
    )

    result = promoter.promote_top_candidates(
        conn=triage_conn,
        task_hub_conn=th_conn,
        min_score=7.0,
        daily_cap=2,
    )

    assert result.promoted_this_run == 2
    assert result.skipped_cap_reached == 1
    # The two highest scores landed, the third was capped out.
    assert sorted(result.promoted_post_ids) == ["a", "b"]


def test_promoter_short_circuits_when_cap_already_met(tmp_path: Path):
    triage_conn, th_conn = _seed_with_scores(
        tmp_path,
        {"x": 9.5, "y": 9.0},
    )
    # Pre-promote one row so the cap=1 limit binds immediately.
    triage.approve_candidate(
        post_id="x",
        decided_by="auto_promoter:score=9.5:run=2026-05-22",
        conn=triage_conn,
        task_hub_conn=th_conn,
    )

    result = promoter.promote_top_candidates(
        conn=triage_conn,
        task_hub_conn=th_conn,
        min_score=7.0,
        daily_cap=1,
    )

    assert result.promoted_today_before_run == 1
    assert result.promoted_this_run == 0


def test_promoter_dry_run_writes_nothing(tmp_path: Path):
    triage_conn, th_conn = _seed_with_scores(
        tmp_path,
        {"alpha": 9.0, "beta": 8.0},
    )

    result = promoter.promote_top_candidates(
        conn=triage_conn,
        task_hub_conn=th_conn,
        min_score=7.0,
        daily_cap=5,
        dry_run=True,
    )

    assert result.promoted_this_run == 2  # counter reflects intent
    assert result.dry_run is True
    # No mutations to triage or task_hub.
    pending = triage_conn.execute(
        "SELECT COUNT(*) AS n FROM demo_triage_candidates WHERE state = 'pending'"
    ).fetchone()
    assert int(pending["n"]) == 2
    th_count = th_conn.execute(
        "SELECT COUNT(*) AS n FROM task_hub_items"
    ).fetchone()
    assert int(th_count["n"]) == 0


def test_promoter_daily_cap_counter_ignores_manual_decisions(tmp_path: Path):
    """Manual operator approvals must NOT eat into the auto-promoter cap."""
    triage_conn, th_conn = _seed_with_scores(
        tmp_path,
        {"manual_one": 9.0, "auto_one": 8.5},
    )
    # Manual approval (decided_by != auto_promoter:*) should be ignored
    # by the daily-cap counter.
    triage.approve_candidate(
        post_id="manual_one",
        decided_by="kevin",
        conn=triage_conn,
        task_hub_conn=th_conn,
    )

    result = promoter.promote_top_candidates(
        conn=triage_conn,
        task_hub_conn=th_conn,
        min_score=7.0,
        daily_cap=1,
    )

    # promoted_today_before_run counts only auto_promoter rows → 0.
    assert result.promoted_today_before_run == 0
    assert result.promoted_this_run == 1
    assert result.promoted_post_ids == ["auto_one"]
