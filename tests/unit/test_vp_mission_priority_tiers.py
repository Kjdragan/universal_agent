"""Tests for the VP mission priority-tier ranking system.

Pins the four behaviours that prevent the 2026-05-27 morning-briefing
starvation from recurring:

1. The constants module maps known operator-facing mission types to
   the operator_daily tier.
2. The default tier is 'background' — the SAFE default, NOT the most
   urgent. Forgotten work runs LAST, doesn't starve briefings.
3. `claim_next_vp_mission` orders by tier rank first, numeric priority
   second, created_at third. An operator_daily mission with default
   priority always wins against a background mission even if the
   background mission set an explicit lower numeric priority.
4. The migration backfills existing rows from mission_type so historical
   data immediately gets the right tier assignment.
"""
from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

import pytest

from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import (
    claim_next_vp_mission,
    queue_vp_mission,
    upsert_vp_session,
)
from universal_agent.vp.mission_priority import (
    DEFAULT_TIER,
    MISSION_TYPE_TIER,
    TIERS,
    is_valid_tier,
    resolve_tier,
    tier_rank,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    """In-memory SQLite with full UA schema applied."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_schema(c)
    # Two VP sessions so we can queue against either.
    upsert_vp_session(
        c, vp_id="vp.general.primary", runtime_id="rt.test",
        status="idle", session_id="vp.general.primary.test",
    )
    upsert_vp_session(
        c, vp_id="vp.coder.primary", runtime_id="rt.test",
        status="idle", session_id="vp.coder.primary.test",
    )
    return c


# ─── Constants module ──────────────────────────────────────────────────────


def test_known_operator_facing_mission_types_in_operator_daily_tier():
    for mt in ("briefing", "morning_briefing", "evening_briefing", "youtube_daily_digest"):
        assert MISSION_TYPE_TIER.get(mt) == "operator_daily", (
            f"{mt} must be operator_daily so it wins the queue"
        )


def test_known_proactive_pipeline_mission_types_in_operator_signal_tier():
    for mt in (
        "insight_brief",
        "convergence_brief",
        "convergence_evaluation",
        "research",
        "research_report_email",
    ):
        assert MISSION_TYPE_TIER.get(mt) == "operator_signal"


def test_convergence_evaluation_outranks_curation(conn):
    # Regression: convergence_evaluation (Atlas's ship/skip/defer + author
    # pass that feeds Simone's hourly digest) was missing from the tier map,
    # resolved to 'background', and got starved behind every curation mission
    # — so the digest never received a brief. It must outrank maintenance.
    _queue(conn, "vp.general.primary", "m-curation-x", "curation")  # maintenance
    _queue(conn, "vp.general.primary", "m-conv-eval-x", "convergence_evaluation")

    claimed = claim_next_vp_mission(
        conn=conn,
        vp_id="vp.general.primary",
        worker_id="worker.test",
        lease_ttl_seconds=60,
    )
    assert claimed is not None
    assert dict(claimed)["mission_id"] == "m-conv-eval-x"


def test_resolve_tier_defaults_to_background_for_unknown_mission_type():
    # The whole point of the redesign — unknown types fall to the LOWEST
    # urgency, not the highest. Default should never starve anyone.
    assert resolve_tier("totally_made_up_mission_type") == "background"
    assert resolve_tier("") == "background"
    assert resolve_tier(None) == "background"
    assert DEFAULT_TIER == "background"


def test_tier_rank_orders_operator_daily_first_background_last():
    assert tier_rank("operator_daily") < tier_rank("operator_signal")
    assert tier_rank("operator_signal") < tier_rank("maintenance")
    assert tier_rank("maintenance") < tier_rank("background")


def test_is_valid_tier_accepts_known_tiers_rejects_typos():
    for t in TIERS:
        assert is_valid_tier(t)
    assert not is_valid_tier("operator")
    assert not is_valid_tier("urgent")
    assert not is_valid_tier("")


# ─── Schema migration ──────────────────────────────────────────────────────


def test_schema_adds_priority_tier_column_with_safe_default(conn):
    cols = {r[1]: r for r in conn.execute("PRAGMA table_info(vp_missions)").fetchall()}
    assert "priority_tier" in cols
    # The default in the schema must be 'background' — anything else
    # (operator_daily, etc) would silently mark forgotten missions
    # urgent and starve everything else.
    default_value = cols["priority_tier"][4]  # PRAGMA columns: cid, name, type, notnull, dflt_value, pk
    assert "'background'" in str(default_value)


def test_backlog_history_table_exists(conn):
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "vp_mission_backlog_history" in tables


# ─── Claim ordering ───────────────────────────────────────────────────────


def _queue(conn, vp_id, mission_id, mission_type, priority=100, priority_tier=None):
    queue_vp_mission(
        conn=conn,
        mission_id=mission_id,
        vp_id=vp_id,
        mission_type=mission_type,
        objective=f"test objective for {mission_id}",
        payload={},
        priority=priority,
        priority_tier=priority_tier,
    )


def test_operator_daily_wins_against_background_even_with_lower_priority(conn):
    # Background mission with explicit low numeric priority (which would
    # have won under the old ASC ordering): created FIRST so created_at
    # tiebreak also favors it.
    _queue(conn, "vp.general.primary", "m-bg-1", "background_task", priority=1)
    # Operator-daily mission with DEFAULT numeric priority of 100 (the
    # old default that lost everything): created SECOND.
    _queue(conn, "vp.general.primary", "m-briefing-1", "briefing")  # default tier resolves to operator_daily

    claimed = claim_next_vp_mission(
        conn=conn,
        vp_id="vp.general.primary",
        worker_id="worker.test",
        lease_ttl_seconds=60,
    )
    assert claimed is not None
    assert dict(claimed)["mission_id"] == "m-briefing-1", (
        "operator_daily must be claimed before background regardless of numeric priority"
    )


def test_operator_signal_wins_against_maintenance(conn):
    _queue(conn, "vp.general.primary", "m-curation-1", "curation")  # maintenance
    _queue(conn, "vp.general.primary", "m-insight-1", "insight_brief")  # operator_signal

    claimed = claim_next_vp_mission(
        conn=conn,
        vp_id="vp.general.primary",
        worker_id="worker.test",
        lease_ttl_seconds=60,
    )
    assert claimed is not None
    assert dict(claimed)["mission_id"] == "m-insight-1"


def test_within_tier_numeric_priority_is_tiebreaker(conn):
    # Two operator_daily missions: priority=5 should win over priority=10.
    _queue(conn, "vp.general.primary", "m-brief-late", "briefing", priority=10)
    _queue(conn, "vp.general.primary", "m-brief-urgent", "briefing", priority=5)

    claimed = claim_next_vp_mission(
        conn=conn,
        vp_id="vp.general.primary",
        worker_id="worker.test",
        lease_ttl_seconds=60,
    )
    assert dict(claimed)["mission_id"] == "m-brief-urgent"


def test_within_tier_and_priority_oldest_wins(conn):
    # Same tier + same priority: oldest created_at wins.
    # Sleep is unnecessary because INSERT order maps to created_at order
    # via _now() being called twice in sequence.
    _queue(conn, "vp.general.primary", "m-brief-old", "briefing")
    _queue(conn, "vp.general.primary", "m-brief-new", "briefing")

    claimed = claim_next_vp_mission(
        conn=conn,
        vp_id="vp.general.primary",
        worker_id="worker.test",
        lease_ttl_seconds=60,
    )
    assert dict(claimed)["mission_id"] == "m-brief-old"


def test_explicit_priority_tier_overrides_mission_type_resolution(conn):
    # If a caller explicitly demands a tier, that wins over the
    # mission_type mapping (escape hatch for one-offs).
    _queue(
        conn, "vp.general.primary", "m-forced", "curation",
        priority_tier="operator_daily",
    )
    _queue(conn, "vp.general.primary", "m-insight", "insight_brief")

    claimed = claim_next_vp_mission(
        conn=conn,
        vp_id="vp.general.primary",
        worker_id="worker.test",
        lease_ttl_seconds=60,
    )
    assert dict(claimed)["mission_id"] == "m-forced"


def test_unknown_mission_type_lands_in_background_tier(conn):
    # The "trap" the redesign removes: an unknown mission_type with no
    # explicit tier should NOT outrank operator_daily work.
    _queue(conn, "vp.general.primary", "m-unknown", "some_brand_new_type")
    _queue(conn, "vp.general.primary", "m-brief", "briefing")

    claimed = claim_next_vp_mission(
        conn=conn,
        vp_id="vp.general.primary",
        worker_id="worker.test",
        lease_ttl_seconds=60,
    )
    assert dict(claimed)["mission_id"] == "m-brief"


# ─── last_error cleanup on successful claim ───────────────────────────────


def test_successful_claim_clears_stale_last_error(conn):
    # Simulate the 2026-05-27 condition: stale last_error stamped on
    # vp_sessions from days ago, current worker is healthy.
    conn.execute(
        "UPDATE vp_sessions SET last_error = ? WHERE vp_id = ?",
        ("stale 401 from May 23", "vp.general.primary"),
    )
    conn.commit()

    _queue(conn, "vp.general.primary", "m-briefing-clear", "briefing")
    claimed = claim_next_vp_mission(
        conn=conn,
        vp_id="vp.general.primary",
        worker_id="worker.test",
        lease_ttl_seconds=60,
    )
    assert claimed is not None

    row = conn.execute(
        "SELECT last_error FROM vp_sessions WHERE vp_id = ?",
        ("vp.general.primary",),
    ).fetchone()
    assert row["last_error"] is None, (
        "Successful claim must clear stale last_error so triage isn't "
        "misled by ancient transient failures."
    )
