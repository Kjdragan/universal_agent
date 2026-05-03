"""Phase 2 — tier-1 LLM-discovered narrative cards + operator feedback.

These tests exercise:
  - Evidence collection (no truncation contract)
  - Bundle signature determinism + change detection
  - LLM-payload parsing into CardUpsert (with fault tolerance)
  - apply_tier1_discovery: upsert + retire-unmarked + tier-0-protection
  - Operator feedback mutations (thumbs/snooze/comment/view)
  - Sweeper skip logic for tier-1 (signature unchanged + ceiling)

LLM calls themselves are mocked; we don't hit the real glm-4.7 lane.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from universal_agent.services.mission_control_cards import (
    CardUpsert,
    add_card_comment,
    list_live_cards,
    make_card_id,
    mark_card_viewed,
    set_card_thumbs,
    snooze_card,
    upsert_card,
)
from universal_agent.services.mission_control_db import open_store
from universal_agent.services.mission_control_intelligence_sweeper import (
    MissionControlSweeper,
    SweeperConfig,
    SweepResult,
)
from universal_agent.services.mission_control_tier1 import (
    _card_upsert_from_llm,
    apply_tier1_discovery,
    collect_tier1_evidence,
    evidence_signature,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def activity_db(tmp_path: Path) -> sqlite3.Connection:
    path = tmp_path / "activity.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE activity_events (
            id TEXT PRIMARY KEY,
            event_class TEXT NOT NULL DEFAULT 'notification',
            source_domain TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            full_message TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            status TEXT NOT NULL DEFAULT 'new',
            requires_action INTEGER NOT NULL DEFAULT 0,
            session_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            entity_ref_json TEXT NOT NULL DEFAULT '{}',
            actions_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE task_hub_items (
            task_id TEXT PRIMARY KEY,
            source_kind TEXT NOT NULL,
            source_ref TEXT,
            title TEXT NOT NULL,
            description TEXT,
            project_key TEXT,
            priority INTEGER DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            agent_ready INTEGER DEFAULT 0,
            labels_json TEXT NOT NULL DEFAULT '[]'
        );
        """
    )
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ── Evidence collection ────────────────────────────────────────────────


def test_evidence_collection_returns_full_text(activity_db, tmp_path):
    big_description = "lorem ipsum " * 5000  # 60KB
    activity_db.execute(
        """
        INSERT INTO task_hub_items
            (task_id, source_kind, title, description, status, created_at, updated_at)
        VALUES ('big', 'manual', 'Big task', ?, 'in_progress', ?, ?)
        """,
        (big_description, _now_iso(), _now_iso()),
    )
    mc_conn = open_store(tmp_path / "mc.db")
    try:
        evidence = collect_tier1_evidence(activity_db, mc_conn)
    finally:
        mc_conn.close()
    tasks = evidence["active_or_attention_tasks"]
    assert len(tasks) == 1
    # No-truncation contract: full text round-trips
    assert tasks[0]["description"] == big_description
    assert evidence["counts"]["active_or_attention_tasks"] == 1


def test_evidence_collection_handles_missing_tables(tmp_path):
    """Sweeper boots in environments where activity DB hasn't been
    initialized yet. Evidence collection must degrade gracefully."""
    empty_path = tmp_path / "empty.db"
    empty_conn = sqlite3.connect(str(empty_path))
    empty_conn.row_factory = sqlite3.Row
    mc_conn = open_store(tmp_path / "mc.db")
    try:
        evidence = collect_tier1_evidence(empty_conn, mc_conn)
    finally:
        mc_conn.close()
        empty_conn.close()
    # Missing tables -> empty lists, NOT exceptions
    assert evidence["active_or_attention_tasks"] == []
    assert evidence["recent_events"] == []
    assert "task_hub_unavailable" in evidence


# ── Signature stability ────────────────────────────────────────────────


def test_evidence_signature_is_deterministic(tmp_path):
    e1 = {
        "active_or_attention_tasks": [{"task_id": "t1", "status": "in_progress", "updated_at": "2026-05-03T01:00:00"}],
        "recent_completed_tasks": [],
        "recent_events": [],
        "tier0_tiles": [{"tile_id": "gateway", "current_state": "yellow", "last_signature": "abc"}],
        "prior_live_cards": [],
    }
    e2 = {**e1, "generated_at_utc": "different timestamp"}
    # Different volatile fields, same identifying set -> same signature
    assert evidence_signature(e1) == evidence_signature(e2)


def test_evidence_signature_changes_on_task_state_change(tmp_path):
    e1 = {
        "active_or_attention_tasks": [{"task_id": "t1", "status": "in_progress", "updated_at": "1"}],
        "recent_completed_tasks": [], "recent_events": [], "tier0_tiles": [], "prior_live_cards": [],
    }
    e2 = {
        "active_or_attention_tasks": [{"task_id": "t1", "status": "blocked", "updated_at": "1"}],
        "recent_completed_tasks": [], "recent_events": [], "tier0_tiles": [], "prior_live_cards": [],
    }
    assert evidence_signature(e1) != evidence_signature(e2)


# ── LLM payload parsing ────────────────────────────────────────────────


def test_card_upsert_from_llm_accepts_well_formed_payload():
    raw = {
        "subject_kind": "task",
        "subject_id": "t-123",
        "severity": "warning",
        "title": "Stuck task X",
        "narrative": "long narrative",
        "why_it_matters": "operator should care because Y",
        "recommended_next_step": "manually intervene",
        "tags": ["stuck", "task-hub"],
        "evidence_refs": [{"kind": "task", "id": "t-123", "uri": "/x", "label": "task"}],
    }
    upsert = _card_upsert_from_llm(raw, model="glm-4.7")
    assert upsert.subject_kind == "task"
    assert upsert.subject_id == "t-123"
    assert upsert.severity == "warning"
    assert upsert.synthesis_model == "glm-4.7"


def test_card_upsert_from_llm_rejects_invalid_subject_kind():
    raw = {"subject_kind": "bogus", "subject_id": "x", "severity": "warning",
           "title": "t", "narrative": "n", "why_it_matters": "w"}
    with pytest.raises(ValueError, match="subject_kind"):
        _card_upsert_from_llm(raw, model="glm-4.7")


def test_card_upsert_from_llm_rejects_invalid_severity():
    raw = {"subject_kind": "task", "subject_id": "x", "severity": "puce",
           "title": "t", "narrative": "n", "why_it_matters": "w"}
    with pytest.raises(ValueError, match="severity"):
        _card_upsert_from_llm(raw, model="glm-4.7")


def test_card_upsert_from_llm_supplies_default_why_it_matters_when_blank():
    """Defensive: LLM occasionally returns empty why_it_matters. Don't
    crash the entire pass — flag the quality gap and keep going."""
    raw = {"subject_kind": "task", "subject_id": "x", "severity": "warning",
           "title": "t", "narrative": "n", "why_it_matters": ""}
    upsert = _card_upsert_from_llm(raw, model="glm-4.7")
    assert "quality gap" in upsert.why_it_matters.lower()


# ── apply_tier1_discovery ──────────────────────────────────────────────


def test_apply_discovery_creates_new_cards_and_retires_unmarked(tmp_path):
    """Cards present in prior pass but NOT re-emitted should be retired,
    EXCEPT infrastructure cards (those are owned by the tier-0 invariant)."""
    conn = open_store(tmp_path / "mc.db")
    try:
        # Pre-populate two cards from "prior pass":
        #   - task:old_task (tier-1; LLM doesn't re-emit -> should retire)
        #   - infrastructure:csi_ingester (tier-0 owned -> NOT retired)
        upsert_card(conn, CardUpsert(
            subject_kind="task", subject_id="old_task", severity="warning",
            title="t1", narrative="n1", why_it_matters="w1",
        ))
        upsert_card(conn, CardUpsert(
            subject_kind="infrastructure", subject_id="infra:csi_ingester",
            severity="warning", title="t-infra", narrative="n", why_it_matters="w",
        ))

        # New LLM pass emits a task we haven't seen but doesn't re-emit
        # the prior task or the infrastructure card.
        new_upserts = [CardUpsert(
            subject_kind="task", subject_id="new_task", severity="critical",
            title="new", narrative="n-new", why_it_matters="w-new",
        )]
        summary = apply_tier1_discovery(conn, new_upserts)

        live = {c["subject_id"]: c for c in list_live_cards(conn)}
        # task:new_task created
        assert "new_task" in live
        # task:old_task retired (not in live anymore)
        assert "old_task" not in live
        # infra:csi_ingester preserved (tier-0 owned)
        assert "infra:csi_ingester" in live
        assert summary["created_or_updated"] == ["task:new_task"]
        assert summary["retired"] == ["task:old_task"]
    finally:
        conn.close()


def test_apply_discovery_with_empty_upserts_retires_all_non_infrastructure(tmp_path):
    """LLM returns no cards (system is calm) — all prior LLM-discovered
    cards retire, infrastructure cards stay."""
    conn = open_store(tmp_path / "mc.db")
    try:
        upsert_card(conn, CardUpsert(
            subject_kind="task", subject_id="t1", severity="warning",
            title="t", narrative="n", why_it_matters="w",
        ))
        upsert_card(conn, CardUpsert(
            subject_kind="infrastructure", subject_id="infra:gw", severity="warning",
            title="t", narrative="n", why_it_matters="w",
        ))
        summary = apply_tier1_discovery(conn, [])
        live = {c["subject_id"] for c in list_live_cards(conn)}
        assert live == {"infra:gw"}
        assert summary["retired"] == ["task:t1"]
    finally:
        conn.close()


# ── Operator feedback ──────────────────────────────────────────────────


def _seed_card(conn) -> str:
    upsert_card(conn, CardUpsert(
        subject_kind="task", subject_id="feedback_target", severity="warning",
        title="t", narrative="n", why_it_matters="w",
    ))
    return make_card_id("task", "feedback_target")


def test_set_thumbs_persists_direction(tmp_path):
    conn = open_store(tmp_path / "mc.db")
    try:
        cid = _seed_card(conn)
        set_card_thumbs(conn, cid, "up")
        row = conn.execute("SELECT operator_feedback_json FROM mission_control_cards WHERE card_id = ?", (cid,)).fetchone()
        assert json.loads(row[0])["thumbs"] == "up"
        set_card_thumbs(conn, cid, None)
        row = conn.execute("SELECT operator_feedback_json FROM mission_control_cards WHERE card_id = ?", (cid,)).fetchone()
        assert json.loads(row[0])["thumbs"] is None
    finally:
        conn.close()


def test_set_thumbs_rejects_invalid_direction(tmp_path):
    conn = open_store(tmp_path / "mc.db")
    try:
        cid = _seed_card(conn)
        with pytest.raises(ValueError):
            set_card_thumbs(conn, cid, "sideways")
    finally:
        conn.close()


def test_snooze_card_sets_future_expiry(tmp_path):
    conn = open_store(tmp_path / "mc.db")
    try:
        cid = _seed_card(conn)
        feedback = snooze_card(conn, cid, "1h")
        snoozed_until = datetime.fromisoformat(feedback["snoozed_until"])
        assert snoozed_until > datetime.now(timezone.utc)
        # And less than 1h+1min in the future (sanity check on duration)
        assert snoozed_until < datetime.now(timezone.utc) + timedelta(hours=1, minutes=1)
    finally:
        conn.close()


def test_snooze_card_rejects_unknown_duration(tmp_path):
    conn = open_store(tmp_path / "mc.db")
    try:
        cid = _seed_card(conn)
        with pytest.raises(ValueError):
            snooze_card(conn, cid, "30m")  # not in the allowed set
    finally:
        conn.close()


def test_add_comment_appends_timestamped_entry(tmp_path):
    conn = open_store(tmp_path / "mc.db")
    try:
        cid = _seed_card(conn)
        feedback = add_card_comment(conn, cid, "first comment")
        feedback = add_card_comment(conn, cid, "second comment")
        assert len(feedback["comments"]) == 2
        assert feedback["comments"][0]["text"] == "first comment"
        assert feedback["comments"][1]["text"] == "second comment"
        # Each entry has a timestamp
        for entry in feedback["comments"]:
            assert "ts" in entry
    finally:
        conn.close()


def test_add_comment_rejects_blank_text(tmp_path):
    conn = open_store(tmp_path / "mc.db")
    try:
        cid = _seed_card(conn)
        with pytest.raises(ValueError):
            add_card_comment(conn, cid, "   ")
    finally:
        conn.close()


def test_mark_card_viewed_records_per_user_timestamp(tmp_path):
    conn = open_store(tmp_path / "mc.db")
    try:
        cid = _seed_card(conn)
        viewed = mark_card_viewed(conn, cid, viewer="kevin")
        viewed = mark_card_viewed(conn, cid, viewer="other")
        assert "kevin" in viewed and "other" in viewed
        # second mark for same viewer overwrites
        first_kevin_ts = viewed["kevin"]
        viewed = mark_card_viewed(conn, cid, viewer="kevin")
        assert viewed["kevin"] >= first_kevin_ts
    finally:
        conn.close()


# ── Sweeper tier-1 skip logic ──────────────────────────────────────────


def test_tier1_skip_signature_unchanged_within_ceiling():
    reason = MissionControlSweeper._tier1_skip_reason(
        prior_sig="abc",
        new_sig="abc",
        prior_synth_iso=(datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat(),
        floor_seconds=180.0,
        ceiling_seconds=1800.0,
    )
    assert reason and "signature_unchanged" in reason


def test_tier1_skip_signature_unchanged_past_ceiling_reruns():
    """Past the ceiling we re-run even when nothing changed (forces a
    refresh on truly-idle systems so the readout doesn't stale out)."""
    reason = MissionControlSweeper._tier1_skip_reason(
        prior_sig="abc",
        new_sig="abc",
        prior_synth_iso=(datetime.now(timezone.utc) - timedelta(seconds=2000)).isoformat(),
        floor_seconds=180.0,
        ceiling_seconds=1800.0,
    )
    assert reason is None  # ceiling exceeded, re-run


def test_tier1_skip_signature_changed_within_floor_rate_limits():
    reason = MissionControlSweeper._tier1_skip_reason(
        prior_sig="abc",
        new_sig="def",
        prior_synth_iso=(datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat(),
        floor_seconds=180.0,
        ceiling_seconds=1800.0,
    )
    assert reason and "rate_limited" in reason


def test_tier1_skip_signature_changed_past_floor_runs():
    reason = MissionControlSweeper._tier1_skip_reason(
        prior_sig="abc",
        new_sig="def",
        prior_synth_iso=(datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat(),
        floor_seconds=180.0,
        ceiling_seconds=1800.0,
    )
    assert reason is None  # floor exceeded, signature changed, run


def test_tier1_skip_no_prior_synthesis_runs():
    """Initial pass (no prior synthesis recorded) always runs."""
    reason = MissionControlSweeper._tier1_skip_reason(
        prior_sig=None,
        new_sig="abc",
        prior_synth_iso=None,
        floor_seconds=180.0,
        ceiling_seconds=1800.0,
    )
    assert reason is None
