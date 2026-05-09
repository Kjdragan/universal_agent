"""Tests for the CSI demo-triage candidate store.

The triage DB is the only path from a tier 3+ Claude Code intel action to
a Task Hub item now; auto-queue is gone. These tests pin the schema, the
discovery sync, and the approve/dismiss/restore lifecycle so that path
can't silently regress.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from universal_agent import task_hub
from universal_agent.services import csi_demo_triage as triage


# ── Helpers ─────────────────────────────────────────────────────────────


def _open(tmp_path: Path) -> sqlite3.Connection:
    """Open a triage DB rooted under a fresh tmp artifacts dir."""
    return triage.open_db(artifacts_root=tmp_path)


def _open_task_hub(tmp_path: Path) -> sqlite3.Connection:
    """Fresh on-disk task-hub DB for approve round-trip checks."""
    conn = sqlite3.connect(str(tmp_path / "task_hub.db"))
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _write_packet(
    tmp_path: Path,
    *,
    actions: list[dict[str, Any]],
    handle: str = "ClaudeDevs",
    use_refined: bool = False,
) -> Path:
    packet_dir = tmp_path / "packets" / "2026-05-09" / "fake_packet__120000"
    packet_dir.mkdir(parents=True, exist_ok=True)
    (packet_dir / "manifest.json").write_text(
        json.dumps({"handle": handle, "lane": "claude_code_intel"}),
        encoding="utf-8",
    )
    name = "actions_refined.json" if use_refined else "actions.json"
    (packet_dir / name).write_text(json.dumps(actions), encoding="utf-8")
    return packet_dir


def _action(post_id: str, *, tier: int, **extra: Any) -> dict[str, Any]:
    base = {
        "post_id": post_id,
        "tier": tier,
        "action_type": "demo_task" if tier == 3 else "kb_update",
        "url": f"https://x.com/ClaudeDevs/status/{post_id}",
        "text": f"Tier {tier} test post {post_id}",
        "links": [],
    }
    base.update(extra)
    return base


# ── Schema ──────────────────────────────────────────────────────────────


def test_schema_creation_idempotent(tmp_path: Path):
    conn = _open(tmp_path)
    triage.ensure_schema(conn)  # second call must not raise
    triage.ensure_schema(conn)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='demo_triage_candidates'"
    ).fetchall()
    assert len(rows) == 1
    conn.close()


# ── Sync ────────────────────────────────────────────────────────────────


def test_sync_idempotent(tmp_path: Path):
    actions = [
        _action("p1", tier=3),
        _action("p2", tier=1),
    ]
    packet = _write_packet(tmp_path, actions=actions, use_refined=True)
    first = triage.sync_candidates_from_packet(packet_dir=packet, artifacts_root=tmp_path)
    assert first == {"inserted": 1, "skipped": 0}
    # Second pass: same packet, nothing new.
    second = triage.sync_candidates_from_packet(packet_dir=packet, artifacts_root=tmp_path)
    assert second["inserted"] == 0
    assert second["skipped"] == 1


def test_sync_skips_below_tier_3(tmp_path: Path):
    actions = [_action(f"p{n}", tier=n) for n in (1, 2, 3, 4)]
    packet = _write_packet(tmp_path, actions=actions)
    triage.sync_candidates_from_packet(packet_dir=packet, artifacts_root=tmp_path)
    rows = triage.list_candidates(artifacts_root=tmp_path)
    tiers = sorted(c.tier for c in rows)
    assert tiers == [3, 4]


def test_sync_prefers_refined_actions(tmp_path: Path):
    refined = [_action("p1", tier=3, text="refined-text")]
    base = [_action("p1", tier=3, text="raw-text")]
    packet_dir = tmp_path / "packets" / "2026-05-09" / "pkt__120000"
    packet_dir.mkdir(parents=True, exist_ok=True)
    (packet_dir / "manifest.json").write_text(json.dumps({"handle": "bcherny"}))
    (packet_dir / "actions.json").write_text(json.dumps(base))
    (packet_dir / "actions_refined.json").write_text(json.dumps(refined))
    triage.sync_candidates_from_packet(packet_dir=packet_dir, artifacts_root=tmp_path)
    rows = triage.list_candidates(artifacts_root=tmp_path)
    assert len(rows) == 1
    assert "refined-text" in rows[0].post_text


# ── Listing / ordering ──────────────────────────────────────────────────


def test_list_candidates_newest_first(tmp_path: Path):
    conn = _open(tmp_path)
    older_iso = "2026-05-01T00:00:00Z"
    newer_iso = "2026-05-09T00:00:00Z"
    for pid, ts in (("old", older_iso), ("new", newer_iso)):
        conn.execute(
            """
            INSERT INTO demo_triage_candidates
              (post_id, handle, tier, action_type, packet_dir, first_seen_at, state)
            VALUES (?, 'ClaudeDevs', 3, 'demo_task', '/x', ?, 'pending')
            """,
            (pid, ts),
        )
    conn.commit()
    rows = triage.list_candidates(conn=conn)
    assert [r.post_id for r in rows] == ["new", "old"]
    conn.close()


def test_get_top_recommendations(tmp_path: Path):
    conn = _open(tmp_path)
    # 7 ranked candidates with monotonically rising scores.
    for i in range(7):
        conn.execute(
            """
            INSERT INTO demo_triage_candidates
              (post_id, handle, tier, action_type, packet_dir, first_seen_at,
               state, ranking_score, ranking_evaluated_at, ranking_run_id)
            VALUES (?, 'ClaudeDevs', 3, 'demo_task', '/x', ?, 'pending',
                    ?, '2026-05-09T00:00:00Z', 'run1')
            """,
            (f"r{i}", f"2026-05-0{i + 1}T00:00:00Z", float(i)),
        )
    # 3 unranked candidates.
    for i in range(3):
        conn.execute(
            """
            INSERT INTO demo_triage_candidates
              (post_id, handle, tier, action_type, packet_dir, first_seen_at, state)
            VALUES (?, 'ClaudeDevs', 3, 'demo_task', '/x', ?, 'pending')
            """,
            (f"u{i}", f"2026-04-0{i + 1}T00:00:00Z"),
        )
    conn.commit()
    top = triage.get_top_recommendations(conn=conn, n=5)
    assert [c.post_id for c in top] == ["r6", "r5", "r4", "r3", "r2"]
    assert all(c.ranking_score is not None for c in top)
    conn.close()


# ── Approve round-trip ─────────────────────────────────────────────────


def test_approve_round_trip_writes_to_task_hub(tmp_path: Path):
    actions = [_action("approve_me", tier=3)]
    packet = _write_packet(tmp_path, actions=actions)
    triage.sync_candidates_from_packet(packet_dir=packet, artifacts_root=tmp_path)

    conn = _open(tmp_path)
    th_conn = _open_task_hub(tmp_path)
    result = triage.approve_candidate(
        post_id="approve_me",
        conn=conn,
        task_hub_conn=th_conn,
    )
    assert result["ok"] is True
    assert result["state"] == "approved"
    task_id = result["task_id"]
    assert task_id.startswith("cody_scaffold_request:")

    candidate = triage._get_one(conn, "approve_me")
    assert candidate is not None
    assert candidate.state == "approved"
    assert candidate.task_id == task_id

    th_row = th_conn.execute(
        "SELECT task_id, status, source_kind FROM task_hub_items WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    assert th_row is not None
    assert th_row["source_kind"] == "cody_scaffold_request"
    conn.close()
    th_conn.close()


def test_approve_idempotent(tmp_path: Path):
    actions = [_action("once", tier=3)]
    packet = _write_packet(tmp_path, actions=actions)
    triage.sync_candidates_from_packet(packet_dir=packet, artifacts_root=tmp_path)

    th_conn = _open_task_hub(tmp_path)
    conn = _open(tmp_path)
    first = triage.approve_candidate(post_id="once", conn=conn, task_hub_conn=th_conn)
    assert first["ok"] is True
    second = triage.approve_candidate(post_id="once", conn=conn, task_hub_conn=th_conn)
    assert second["ok"] is False
    assert second["reason"] == "already_approved"
    rows = th_conn.execute("SELECT COUNT(*) AS n FROM task_hub_items").fetchone()
    assert int(rows["n"]) == 1
    conn.close()
    th_conn.close()


def test_approve_tier_4_uses_kb_update_kind(tmp_path: Path):
    actions = [_action("tier4_post", tier=4)]
    packet = _write_packet(tmp_path, actions=actions)
    triage.sync_candidates_from_packet(packet_dir=packet, artifacts_root=tmp_path)

    th_conn = _open_task_hub(tmp_path)
    conn = _open(tmp_path)
    result = triage.approve_candidate(post_id="tier4_post", conn=conn, task_hub_conn=th_conn)
    assert result["task_id"].startswith("claude_code_kb_update:")
    conn.close()
    th_conn.close()


# ── Dismiss / restore ──────────────────────────────────────────────────


def test_dismiss_then_restore(tmp_path: Path):
    actions = [_action("d1", tier=3)]
    packet = _write_packet(tmp_path, actions=actions)
    triage.sync_candidates_from_packet(packet_dir=packet, artifacts_root=tmp_path)

    conn = _open(tmp_path)
    dismissed = triage.dismiss_candidate(post_id="d1", conn=conn)
    assert dismissed["ok"] is True
    assert dismissed["state"] == "dismissed"
    cand = triage._get_one(conn, "d1")
    assert cand.state == "dismissed"
    assert cand.decided_at is not None

    restored = triage.restore_candidate(post_id="d1", conn=conn)
    assert restored["ok"] is True
    assert restored["state"] == "pending"
    cand = triage._get_one(conn, "d1")
    assert cand.state == "pending"
    assert cand.decided_at is None
    assert cand.decided_by is None
    conn.close()


def test_dismiss_refuses_after_approve(tmp_path: Path):
    actions = [_action("locked", tier=3)]
    packet = _write_packet(tmp_path, actions=actions)
    triage.sync_candidates_from_packet(packet_dir=packet, artifacts_root=tmp_path)

    th_conn = _open_task_hub(tmp_path)
    conn = _open(tmp_path)
    triage.approve_candidate(post_id="locked", conn=conn, task_hub_conn=th_conn)
    result = triage.dismiss_candidate(post_id="locked", conn=conn)
    assert result["ok"] is False
    assert result["reason"] == "already_approved"
    cand = triage._get_one(conn, "locked")
    assert cand.state == "approved"
    conn.close()
    th_conn.close()


def test_restore_refuses_when_not_dismissed(tmp_path: Path):
    actions = [_action("pending_only", tier=3)]
    packet = _write_packet(tmp_path, actions=actions)
    triage.sync_candidates_from_packet(packet_dir=packet, artifacts_root=tmp_path)

    conn = _open(tmp_path)
    result = triage.restore_candidate(post_id="pending_only", conn=conn)
    assert result["ok"] is False
    assert result["reason"] == "not_dismissed"
    conn.close()


# ── Counts ──────────────────────────────────────────────────────────────


def test_counts(tmp_path: Path):
    conn = _open(tmp_path)
    seed = [
        ("a", 3, "pending", None),
        ("b", 3, "pending", 7.5),
        ("c", 4, "pending", None),
        ("d", 4, "approved", 9.0),
        ("e", 3, "dismissed", 2.0),
    ]
    for pid, tier, state, score in seed:
        conn.execute(
            """
            INSERT INTO demo_triage_candidates
              (post_id, handle, tier, action_type, packet_dir, first_seen_at,
               state, ranking_score)
            VALUES (?, 'h', ?, 'demo_task', '/x', '2026-05-09T00:00:00Z', ?, ?)
            """,
            (pid, tier, state, score),
        )
    conn.commit()
    counts = triage.get_counts(conn=conn)
    assert counts["pending"] == 3
    assert counts["approved"] == 1
    assert counts["dismissed"] == 1
    assert counts["tier3_pending"] == 2
    assert counts["tier4_pending"] == 1
    assert counts["unranked_pending"] == 2
    conn.close()
