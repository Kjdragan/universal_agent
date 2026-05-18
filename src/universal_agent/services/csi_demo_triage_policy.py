"""CSI demo-triage auto-dismiss policy engine.

Background — 2026-05-17 trace: the demo-triage queue had accumulated 96
pending candidates because the cron explicitly never auto-queues; every
tier-3+ action waits for operator approval. Most stale tier-3 entries
were never going to be acted on, but they hide the genuinely actionable
items further down the list.

This module applies a conservative auto-dismiss policy so the operator
sees a short list of fresh, high-signal candidates instead of weeks of
backlog. **Tier 4 candidates are never auto-dismissed** — they always
require operator eyes.

Auto-approve is intentionally NOT implemented here. Approving creates
Task Hub work and burns ZAI quota; the upside of automating that does
not justify the failure mode of approving the wrong thing. If the
operator wants auto-approve, it should be a separate PR with explicit
opt-in per action_type.

The policy is fully audited: every auto-action stamps
``decided_by = "auto-policy:<name>"`` so the dashboard, ledger, and
``restore_candidate`` operator path can identify and reverse the
auto-decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any

from universal_agent.services.csi_demo_triage import (
    STATE_PENDING,
    dismiss_candidate,
    open_db,
)

logger = logging.getLogger(__name__)


# ── Policy definitions ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class StaleTierPolicy:
    """Auto-dismiss policy for stale low-signal candidates within a tier."""

    name: str
    tier: int
    max_age_days: int
    # When set, also require ``ranking_score <= max_ranking_score OR
    # ranking_score IS NULL`` to dismiss. Leave None to ignore score.
    max_ranking_score: float | None = None
    decided_by: str = "auto-policy:stale"


DEFAULT_POLICIES: tuple[StaleTierPolicy, ...] = (
    # Tier-3 candidates older than 14 days with weak or missing ranking
    # scores are almost never acted on. Default dismissal is reversible
    # via the dashboard "Restore" button.
    StaleTierPolicy(
        name="stale-tier-3",
        tier=3,
        max_age_days=14,
        max_ranking_score=5.0,
        decided_by="auto-policy:stale-tier-3",
    ),
)


# ── Application ─────────────────────────────────────────────────────────────


@dataclass
class CandidateAction:
    """Record of one auto-policy decision for reporting/audit."""

    post_id: str
    tier: int
    action_type: str
    ranking_score: float | None
    age_days: float
    policy_name: str
    state_before: str
    state_after: str
    applied: bool
    reason: str = ""


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _candidates_matching_policy(
    conn: sqlite3.Connection, policy: StaleTierPolicy, *, now: datetime
) -> list[dict[str, Any]]:
    """Return raw rows that this policy would dismiss right now."""
    cutoff = now - timedelta(days=policy.max_age_days)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    if policy.max_ranking_score is None:
        rows = conn.execute(
            """
            SELECT post_id, tier, action_type, ranking_score, first_seen_at
              FROM demo_triage_candidates
             WHERE state = ?
               AND tier = ?
               AND first_seen_at < ?
             ORDER BY first_seen_at ASC
            """,
            (STATE_PENDING, policy.tier, cutoff_iso),
        ).fetchall()
    else:
        # ranking_score must be NULL or ≤ threshold. NULL is treated as
        # "low signal" because the ranker hasn't validated the item.
        rows = conn.execute(
            """
            SELECT post_id, tier, action_type, ranking_score, first_seen_at
              FROM demo_triage_candidates
             WHERE state = ?
               AND tier = ?
               AND first_seen_at < ?
               AND (ranking_score IS NULL OR ranking_score <= ?)
             ORDER BY first_seen_at ASC
            """,
            (STATE_PENDING, policy.tier, cutoff_iso, policy.max_ranking_score),
        ).fetchall()
    return [dict(row) for row in rows]


def apply_policies(
    *,
    conn: sqlite3.Connection | None = None,
    artifacts_root: Path | None = None,
    policies: tuple[StaleTierPolicy, ...] | None = None,
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply every configured auto-policy. Returns a structured report.

    ``dry_run=True`` (the default) only reports what *would* change. Pass
    ``dry_run=False`` to actually mutate state.
    """
    own_conn = conn is None
    if conn is None:
        conn = open_db(artifacts_root)
    policies = policies or DEFAULT_POLICIES
    when = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    actions: list[CandidateAction] = []
    try:
        for policy in policies:
            matched = _candidates_matching_policy(conn, policy, now=when)
            for row in matched:
                first_seen = _parse_iso(str(row.get("first_seen_at") or ""))
                age_days = (
                    (when - first_seen).total_seconds() / 86_400.0
                    if first_seen
                    else 0.0
                )
                applied = False
                reason = "dry_run"
                state_after = STATE_PENDING
                if not dry_run:
                    result = dismiss_candidate(
                        post_id=str(row["post_id"]),
                        decided_by=policy.decided_by,
                        conn=conn,
                    )
                    if result.get("ok"):
                        applied = True
                        reason = "auto_dismissed"
                        state_after = "dismissed"
                    else:
                        reason = str(result.get("reason") or "dismiss_failed")
                actions.append(
                    CandidateAction(
                        post_id=str(row["post_id"]),
                        tier=int(row.get("tier") or 0),
                        action_type=str(row.get("action_type") or ""),
                        ranking_score=row.get("ranking_score"),
                        age_days=round(age_days, 2),
                        policy_name=policy.name,
                        state_before=STATE_PENDING,
                        state_after=state_after,
                        applied=applied,
                        reason=reason,
                    )
                )
    finally:
        if own_conn:
            conn.close()

    summary = {
        "ok": True,
        "dry_run": dry_run,
        "as_of": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "policies_applied": [p.name for p in policies],
        "actions_total": len(actions),
        "actions_applied": sum(1 for a in actions if a.applied),
        "by_policy": {
            p.name: sum(1 for a in actions if a.policy_name == p.name)
            for p in policies
        },
        "actions": [a.__dict__ for a in actions],
    }
    return summary


# ── Operator entry-point ────────────────────────────────────────────────────


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def policy_auto_apply_enabled() -> bool:
    """Operator switch — controls whether the daily cron actually mutates state.

    The script-level ``--apply`` flag overrides this; the env var only
    governs the would-be cron sweep so operators can stage the policy in
    dry-run, validate the diff, then flip the switch.
    """
    return _env_truthy("UA_CSI_TRIAGE_AUTO_POLICY_ENABLED", default=False)
