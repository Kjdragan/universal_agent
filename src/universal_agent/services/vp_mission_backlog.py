"""VP mission backlog tracking — informational telemetry for Simone.

Captures a per-tier snapshot of the vp_missions queue on every heartbeat
tick, records it to vp_mission_backlog_history, and computes a trend
(increasing / decreasing / stable) against the last 30-minute and 6-hour
windows.

This is intentionally **informational, not alerting**. The output goes
into Simone's heartbeat context block; she decides whether to surface a
notification to the operator based on her own judgment about whether
the trend warrants attention.

The 2026-05-27 morning briefing miss showed that without continuous
backlog visibility, we can't tell whether the work-generation rate has
exceeded our throughput. If insight_brief production speed > Atlas
processing speed, the queue grows unboundedly. This service makes that
visible without forcing a tripwire.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import sqlite3
from typing import Any, Optional

from universal_agent.vp.mission_priority import TIERS

logger = logging.getLogger(__name__)


# Trend classification thresholds. Chosen so a normal cron cycle that
# briefly bumps the queue by a couple of items doesn't get flagged as
# "increasing." Operator-tunable via env if needed later.
_TREND_INCREASE_THRESHOLD = 3   # +N items vs comparison window = "increasing"
_TREND_DECREASE_THRESHOLD = 3   # -N items vs comparison window = "decreasing"


@dataclass(frozen=True)
class TierSnapshot:
    """Point-in-time count for one (vp_id, priority_tier) bucket."""
    vp_id: str
    priority_tier: str
    queued: int
    running: int


@dataclass(frozen=True)
class TrendDelta:
    """Change in queued count vs a comparison window."""
    vp_id: str
    priority_tier: str
    current_queued: int
    prev_queued_30m: Optional[int]
    prev_queued_6h: Optional[int]
    trend_30m: str   # "increasing" | "decreasing" | "stable" | "no_history"
    trend_6h: str


@dataclass(frozen=True)
class BacklogSnapshot:
    """Complete backlog picture: per-tier counts + trends."""
    measured_at: str
    tiers: tuple[TierSnapshot, ...]
    trends: tuple[TrendDelta, ...]

    def total_queued(self) -> int:
        return sum(t.queued for t in self.tiers)

    def total_running(self) -> int:
        return sum(t.running for t in self.tiers)

    def queued_by_tier(self) -> dict[str, int]:
        out: dict[str, int] = {tier: 0 for tier in TIERS}
        for t in self.tiers:
            out[t.priority_tier] = out.get(t.priority_tier, 0) + t.queued
        return out


def _classify(current: int, comparison: Optional[int]) -> str:
    if comparison is None:
        return "no_history"
    delta = current - comparison
    if delta >= _TREND_INCREASE_THRESHOLD:
        return "increasing"
    if delta <= -_TREND_DECREASE_THRESHOLD:
        return "decreasing"
    return "stable"


def compute_backlog_snapshot(conn: sqlite3.Connection) -> BacklogSnapshot:
    """Read the current backlog from vp_missions and compute trends.

    Read-only. Safe to call frequently from heartbeat ticks. Falls back
    to an empty snapshot if the schema isn't ready (e.g. during a fresh
    bootstrap before ensure_schema has run).
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    try:
        rows = conn.execute(
            """
            SELECT
              vp_id,
              priority_tier,
              SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued,
              SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running
            FROM vp_missions
            WHERE status IN ('queued', 'running')
              AND cancel_requested = 0
            GROUP BY vp_id, priority_tier
            ORDER BY vp_id, priority_tier
            """
        ).fetchall()
    except sqlite3.OperationalError as exc:
        # Schema not ready yet, or older DB without priority_tier.
        logger.debug("compute_backlog_snapshot: query failed (%s)", exc)
        return BacklogSnapshot(measured_at=now_iso, tiers=(), trends=())

    tiers: list[TierSnapshot] = []
    for row in rows:
        tiers.append(
            TierSnapshot(
                vp_id=str(row["vp_id"]),
                priority_tier=str(row["priority_tier"] or "background"),
                queued=int(row["queued"] or 0),
                running=int(row["running"] or 0),
            )
        )

    trends: list[TrendDelta] = []
    cutoff_30m = (now - timedelta(minutes=30)).isoformat()
    cutoff_6h = (now - timedelta(hours=6)).isoformat()
    for t in tiers:
        prev_30m = _lookup_historical_queued(conn, t.vp_id, t.priority_tier, cutoff_30m)
        prev_6h = _lookup_historical_queued(conn, t.vp_id, t.priority_tier, cutoff_6h)
        trends.append(
            TrendDelta(
                vp_id=t.vp_id,
                priority_tier=t.priority_tier,
                current_queued=t.queued,
                prev_queued_30m=prev_30m,
                prev_queued_6h=prev_6h,
                trend_30m=_classify(t.queued, prev_30m),
                trend_6h=_classify(t.queued, prev_6h),
            )
        )

    return BacklogSnapshot(
        measured_at=now_iso,
        tiers=tuple(tiers),
        trends=tuple(trends),
    )


def _lookup_historical_queued(
    conn: sqlite3.Connection,
    vp_id: str,
    priority_tier: str,
    measured_before_iso: str,
) -> Optional[int]:
    """Return queued_count from the most recent sample at or before the cutoff."""
    try:
        row = conn.execute(
            """
            SELECT queued_count
            FROM vp_mission_backlog_history
            WHERE vp_id = ?
              AND priority_tier = ?
              AND measured_at <= ?
            ORDER BY measured_at DESC
            LIMIT 1
            """,
            (vp_id, priority_tier, measured_before_iso),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    return int(row[0] or 0)


def record_backlog_sample(
    conn: sqlite3.Connection,
    snapshot: BacklogSnapshot,
) -> None:
    """Persist one tier-by-tier sample to vp_mission_backlog_history.

    Idempotent at the row level — duplicate samples at the same instant
    are harmless (sample_id is autoincrement). Best-effort: a failure
    here must NOT block the caller (we're called from heartbeat ticks).
    """
    if not snapshot.tiers:
        return
    try:
        for t in snapshot.tiers:
            conn.execute(
                """
                INSERT INTO vp_mission_backlog_history
                  (measured_at, vp_id, priority_tier, queued_count, running_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot.measured_at,
                    t.vp_id,
                    t.priority_tier,
                    t.queued,
                    t.running,
                ),
            )
        conn.commit()
    except Exception as exc:
        logger.debug("record_backlog_sample failed (non-fatal): %s", exc)


def prune_backlog_history(
    conn: sqlite3.Connection,
    older_than_days: int = 14,
) -> int:
    """Remove backlog history rows older than `older_than_days`.

    Keeps the table from growing without bound — at one heartbeat tick
    every 30 seconds across 4 tiers × 2 VPs, that's ~23k rows per day.
    14 days of retention is enough for any human-scale trend analysis.

    Returns the number of rows deleted.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=max(1, int(older_than_days)))
    ).isoformat()
    try:
        cursor = conn.execute(
            "DELETE FROM vp_mission_backlog_history WHERE measured_at < ?",
            (cutoff,),
        )
        conn.commit()
        return int(cursor.rowcount or 0)
    except Exception as exc:
        logger.debug("prune_backlog_history failed: %s", exc)
        return 0


def format_backlog_brief(snapshot: BacklogSnapshot) -> str:
    """Render a compact markdown block describing the current backlog.

    Output is shaped for inclusion in Simone's heartbeat context. She
    decides whether the situation warrants surfacing to the operator.
    """
    if not snapshot.tiers:
        return "## VP Mission Backlog\n\n_No active queue (queued + running = 0)._\n"

    lines: list[str] = ["## VP Mission Backlog"]
    lines.append("")
    lines.append(
        f"Snapshot @ {snapshot.measured_at} — "
        f"**{snapshot.total_queued()} queued**, "
        f"**{snapshot.total_running()} running**."
    )
    lines.append("")

    # Per-VP table
    by_vp: dict[str, list[TierSnapshot]] = {}
    for t in snapshot.tiers:
        by_vp.setdefault(t.vp_id, []).append(t)
    trends_index: dict[tuple[str, str], TrendDelta] = {
        (tr.vp_id, tr.priority_tier): tr for tr in snapshot.trends
    }

    for vp_id, vp_tiers in by_vp.items():
        lines.append(f"### {vp_id}")
        lines.append("")
        lines.append("| Tier | Queued | Running | 30m trend | 6h trend |")
        lines.append("|------|-------:|--------:|-----------|----------|")
        for t in sorted(vp_tiers, key=lambda x: x.priority_tier):
            tr = trends_index.get((t.vp_id, t.priority_tier))
            trend_30m = tr.trend_30m if tr else "no_history"
            trend_6h = tr.trend_6h if tr else "no_history"
            lines.append(
                f"| {t.priority_tier} | {t.queued} | {t.running} | "
                f"{trend_30m} | {trend_6h} |"
            )
        lines.append("")

    # Surfacing hints for Simone — without making the call for her.
    increasing_tiers = [
        tr for tr in snapshot.trends
        if tr.trend_30m == "increasing" or tr.trend_6h == "increasing"
    ]
    if increasing_tiers:
        lines.append("**Backlog trending up:**")
        for tr in increasing_tiers:
            delta_str = []
            if tr.prev_queued_30m is not None:
                delta_str.append(f"30m: {tr.prev_queued_30m}→{tr.current_queued}")
            if tr.prev_queued_6h is not None:
                delta_str.append(f"6h: {tr.prev_queued_6h}→{tr.current_queued}")
            lines.append(
                f"- `{tr.vp_id}` / `{tr.priority_tier}` — "
                + ", ".join(delta_str)
            )
        lines.append("")

    return "\n".join(lines) + "\n"


def snapshot_to_payload(snapshot: BacklogSnapshot) -> dict[str, Any]:
    """Convert snapshot to JSON-serializable dict for API/dashboard surfaces."""
    return {
        "measured_at": snapshot.measured_at,
        "total_queued": snapshot.total_queued(),
        "total_running": snapshot.total_running(),
        "queued_by_tier": snapshot.queued_by_tier(),
        "tiers": [
            {
                "vp_id": t.vp_id,
                "priority_tier": t.priority_tier,
                "queued": t.queued,
                "running": t.running,
            }
            for t in snapshot.tiers
        ],
        "trends": [
            {
                "vp_id": tr.vp_id,
                "priority_tier": tr.priority_tier,
                "current_queued": tr.current_queued,
                "prev_queued_30m": tr.prev_queued_30m,
                "prev_queued_6h": tr.prev_queued_6h,
                "trend_30m": tr.trend_30m,
                "trend_6h": tr.trend_6h,
            }
            for tr in snapshot.trends
        ],
    }
