"""Tests for the CSI demo-triage auto-dismiss policy engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from universal_agent.services.csi_demo_triage import (
    STATE_DISMISSED,
    STATE_PENDING,
    ensure_schema,
)
from universal_agent.services.csi_demo_triage_policy import (
    DEFAULT_POLICIES,
    StaleTierPolicy,
    apply_policies,
    policy_auto_apply_enabled,
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _insert(
    conn: sqlite3.Connection,
    *,
    post_id: str,
    tier: int,
    age_days: float,
    ranking_score: float | None = None,
    state: str = STATE_PENDING,
    action_type: str = "demo_task",
    now: datetime,
) -> None:
    first_seen = (now - timedelta(days=age_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """
        INSERT INTO demo_triage_candidates (
          post_id, handle, tier, action_type, packet_dir,
          first_seen_at, state, ranking_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (post_id, "bcherny", tier, action_type, "/tmp", first_seen, state, ranking_score),
    )
    conn.commit()


# ── default policy semantics ──────────────────────────────────────────────


def test_default_policies_only_target_tier_3():
    """Tier 4 is never in the default auto-dismiss set."""
    targeted = {p.tier for p in DEFAULT_POLICIES}
    assert 4 not in targeted, "tier 4 must never be auto-dismissed by default"
    assert targeted == {3}


def test_default_policy_threshold_floor_is_14_days():
    """If the operator wants to tune, the policy field is exposed; default is 14."""
    policy = next(p for p in DEFAULT_POLICIES if p.name == "stale-tier-3")
    assert policy.max_age_days == 14
    assert policy.max_ranking_score == 5.0


# ── dry-run preserves state ───────────────────────────────────────────────


def test_dry_run_makes_no_mutations(tmp_path, monkeypatch):
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    conn = _make_conn()
    _insert(conn, post_id="stale1", tier=3, age_days=30, ranking_score=2.0, now=now)
    _insert(conn, post_id="stale2", tier=3, age_days=20, ranking_score=None, now=now)

    report = apply_policies(conn=conn, dry_run=True, now=now)
    assert report["dry_run"] is True
    assert report["actions_total"] == 2
    assert report["actions_applied"] == 0

    rows = conn.execute(
        "SELECT state FROM demo_triage_candidates ORDER BY post_id"
    ).fetchall()
    assert [r["state"] for r in rows] == [STATE_PENDING, STATE_PENDING]


# ── apply mutates state ───────────────────────────────────────────────────


def test_apply_dismisses_stale_tier_3(tmp_path):
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    conn = _make_conn()
    _insert(conn, post_id="stale_a", tier=3, age_days=30, ranking_score=2.0, now=now)
    _insert(conn, post_id="stale_b", tier=3, age_days=20, ranking_score=None, now=now)

    report = apply_policies(conn=conn, dry_run=False, now=now)
    assert report["actions_applied"] == 2
    for action in report["actions"]:
        assert action["state_after"] == STATE_DISMISSED
        assert action["reason"] == "auto_dismissed"

    states = {
        row["post_id"]: row["state"]
        for row in conn.execute("SELECT post_id, state FROM demo_triage_candidates")
    }
    assert states == {"stale_a": STATE_DISMISSED, "stale_b": STATE_DISMISSED}


def test_decided_by_field_records_policy_name(tmp_path):
    """Every auto-dismiss must stamp `decided_by` for audit/restore."""
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    conn = _make_conn()
    _insert(conn, post_id="stale_c", tier=3, age_days=30, ranking_score=2.0, now=now)

    apply_policies(conn=conn, dry_run=False, now=now)
    row = conn.execute(
        "SELECT decided_by FROM demo_triage_candidates WHERE post_id = ?",
        ("stale_c",),
    ).fetchone()
    assert row["decided_by"] == "auto-policy:stale-tier-3"


# ── tier-4 never dismissed ────────────────────────────────────────────────


def test_tier_4_candidates_are_never_auto_dismissed(tmp_path):
    """The operator always reviews tier-4 even if it's old + unscored."""
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    conn = _make_conn()
    _insert(conn, post_id="t4_old", tier=4, age_days=90, ranking_score=None, now=now)

    report = apply_policies(conn=conn, dry_run=False, now=now)
    assert report["actions_total"] == 0
    state = conn.execute(
        "SELECT state FROM demo_triage_candidates WHERE post_id = ?", ("t4_old",)
    ).fetchone()["state"]
    assert state == STATE_PENDING


# ── age + score gating ────────────────────────────────────────────────────


def test_recent_tier_3_is_not_dismissed_regardless_of_score(tmp_path):
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    conn = _make_conn()
    _insert(conn, post_id="fresh", tier=3, age_days=2, ranking_score=2.0, now=now)
    report = apply_policies(conn=conn, dry_run=False, now=now)
    assert report["actions_total"] == 0


def test_high_score_tier_3_is_not_dismissed_even_if_stale(tmp_path):
    """A high score signals operator interest; do not auto-dismiss."""
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    conn = _make_conn()
    _insert(conn, post_id="hi_score", tier=3, age_days=30, ranking_score=8.5, now=now)
    report = apply_policies(conn=conn, dry_run=False, now=now)
    assert report["actions_total"] == 0


def test_unscored_tier_3_is_dismissed_when_old(tmp_path):
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    conn = _make_conn()
    _insert(conn, post_id="unscored_old", tier=3, age_days=30, ranking_score=None, now=now)
    report = apply_policies(conn=conn, dry_run=False, now=now)
    assert report["actions_applied"] == 1


# ── states other than pending are ignored ─────────────────────────────────


def test_already_dismissed_candidates_are_not_revisited(tmp_path):
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    conn = _make_conn()
    _insert(
        conn,
        post_id="already_dismissed",
        tier=3,
        age_days=30,
        ranking_score=2.0,
        state=STATE_DISMISSED,
        now=now,
    )
    report = apply_policies(conn=conn, dry_run=False, now=now)
    assert report["actions_total"] == 0


def test_approved_candidates_are_not_revisited(tmp_path):
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    conn = _make_conn()
    _insert(
        conn,
        post_id="already_approved",
        tier=3,
        age_days=30,
        ranking_score=2.0,
        state="approved",
        now=now,
    )
    report = apply_policies(conn=conn, dry_run=False, now=now)
    assert report["actions_total"] == 0


# ── custom policies ───────────────────────────────────────────────────────


def test_custom_policy_can_dismiss_tier_4(tmp_path):
    """Operators with strong defaults can opt-in to broader auto-dismiss."""
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    conn = _make_conn()
    _insert(conn, post_id="t4_stale", tier=4, age_days=60, ranking_score=1.0, now=now)
    aggressive = (
        StaleTierPolicy(
            name="aggressive-t4",
            tier=4,
            max_age_days=30,
            max_ranking_score=2.0,
            decided_by="auto-policy:aggressive-t4",
        ),
    )
    report = apply_policies(conn=conn, dry_run=False, policies=aggressive, now=now)
    assert report["actions_applied"] == 1


# ── env switch ────────────────────────────────────────────────────────────


def test_policy_auto_apply_enabled_default_off(monkeypatch):
    monkeypatch.delenv("UA_CSI_TRIAGE_AUTO_POLICY_ENABLED", raising=False)
    assert policy_auto_apply_enabled() is False


def test_policy_auto_apply_enabled_truthy_values(monkeypatch):
    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("UA_CSI_TRIAGE_AUTO_POLICY_ENABLED", value)
        assert policy_auto_apply_enabled() is True, value


def test_policy_auto_apply_enabled_falsy_values(monkeypatch):
    for value in ("0", "false", "off", "no"):
        monkeypatch.setenv("UA_CSI_TRIAGE_AUTO_POLICY_ENABLED", value)
        assert policy_auto_apply_enabled() is False, value
