"""Auto-promote top-scored CSI demo-triage candidates into Task Hub.

Today every tier-3 ClaudeDevs intel signal lands in `demo_triage_candidates`
with `state='pending'`. The `csi_demo_triage_ranker` cron scores each one
0-10 via LLM. The only path from a scored candidate to a `cody_scaffold_request`
Task Hub row (and downstream Cody demo build) is the operator clicking
"Approve" in the dashboard. With 7-9 high-confidence signals accumulating
per day and no operator clicks happening overnight, the pipeline stalls.

This service runs as a cron AFTER the ranker. For each pending candidate
above `min_score`, it calls the canonical `csi_demo_triage.approve_candidate`
helper — the same one the dashboard button uses — so the resulting Task Hub
row is byte-identical to operator-approved promotions. Gating:

  - `UA_INTEL_AUTO_PROMOTE_ENABLED`        — kill switch (default "1")
  - `UA_INTEL_AUTO_PROMOTE_MIN_SCORE`      — score threshold 0-10 (default 7.5)
  - `UA_INTEL_AUTO_PROMOTE_DAILY_CAP`      — max promotions per UTC day (default 2)
  - `UA_INTEL_AUTO_PROMOTE_DRY_RUN`        — report-only mode (default "0")

The daily cap is enforced by counting `state='approved'` rows whose
`decided_by` starts with `auto_promoter:` and whose `decided_at` falls in
the current UTC day. That bound naturally rolls forward at midnight.

`decided_by` carries the score + run-id stamp so any auto-promotion is
traceable end-to-end: `auto_promoter:score=8.4:run=2026-05-22`.

Cody's natural 1-concurrent VP cap and the persistent-queue Task Hub
together make this safe to fire continuously: a backlog of high-scored
candidates drains at Cody's pace, not the promoter's.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import os
import sqlite3
from typing import Any

from universal_agent.services import csi_demo_triage

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}

_DEFAULT_MIN_SCORE = 7.5
_DEFAULT_DAILY_CAP = 2


def _env_enabled(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() in _TRUTHY


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _utc_today_start_iso() -> str:
    """Start of the current UTC day, as ISO timestamp matching `decided_at` format."""
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _count_auto_promotions_today(conn: sqlite3.Connection) -> int:
    """Return how many `auto_promoter:` approvals have fired this UTC day."""
    start = _utc_today_start_iso()
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM demo_triage_candidates
        WHERE state = ?
          AND decided_by LIKE 'auto_promoter:%'
          AND decided_at >= ?
        """,
        (csi_demo_triage.STATE_APPROVED, start),
    ).fetchone()
    return int(row["n"] if row is not None else 0)


@dataclass
class PromoterResult:
    started_at: str
    finished_at: str = ""
    candidates_eligible: int = 0
    daily_cap: int = 0
    promoted_today_before_run: int = 0
    promoted_this_run: int = 0
    promoted_post_ids: list[str] = field(default_factory=list)
    skipped_below_score: int = 0
    skipped_cap_reached: int = 0
    skipped_already_decided: int = 0
    dry_run: bool = False
    error: str | None = None


def promote_top_candidates(
    *,
    conn: sqlite3.Connection | None = None,
    task_hub_conn: sqlite3.Connection | None = None,
    artifacts_root: Any = None,
    min_score: float | None = None,
    daily_cap: int | None = None,
    dry_run: bool | None = None,
) -> PromoterResult:
    """Promote pending candidates whose ranking_score >= min_score, capped per day.

    Iteration order: highest score first so the most confidently-good
    candidates land before the cap binds.
    """
    started_at = datetime.now(timezone.utc).isoformat()
    if min_score is None:
        min_score = _env_float("UA_INTEL_AUTO_PROMOTE_MIN_SCORE", _DEFAULT_MIN_SCORE)
    if daily_cap is None:
        daily_cap = _env_int("UA_INTEL_AUTO_PROMOTE_DAILY_CAP", _DEFAULT_DAILY_CAP)
    if dry_run is None:
        dry_run = _env_enabled("UA_INTEL_AUTO_PROMOTE_DRY_RUN", default="0")

    result = PromoterResult(
        started_at=started_at,
        daily_cap=int(daily_cap),
        dry_run=bool(dry_run),
    )

    own_conn = conn is None
    if conn is None:
        conn = csi_demo_triage.open_db(artifacts_root)
    else:
        csi_demo_triage.ensure_schema(conn)

    try:
        already = _count_auto_promotions_today(conn)
        result.promoted_today_before_run = already
        if already >= daily_cap and not dry_run:
            logger.info(
                "intel_auto_promoter: daily cap already met (%d/%d) — nothing to do",
                already, daily_cap,
            )
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result

        rows = conn.execute(
            """
            SELECT *
            FROM demo_triage_candidates
            WHERE state = ?
              AND ranking_score IS NOT NULL
            ORDER BY ranking_score DESC, first_seen_at ASC
            """,
            (csi_demo_triage.STATE_PENDING,),
        ).fetchall()
        eligible = [r for r in rows if (r["ranking_score"] or 0) >= min_score]
        result.candidates_eligible = len(eligible)
        result.skipped_below_score = len(rows) - len(eligible)

        run_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        promoted_remaining = max(daily_cap - already, 0)

        for row in eligible:
            if promoted_remaining <= 0:
                result.skipped_cap_reached += 1
                continue

            post_id = row["post_id"]
            score = float(row["ranking_score"])
            decided_by = f"auto_promoter:score={score:.1f}:run={run_tag}"

            if dry_run:
                logger.info(
                    "intel_auto_promoter[dry-run]: would promote post_id=%s score=%.1f",
                    post_id, score,
                )
                result.promoted_this_run += 1
                result.promoted_post_ids.append(str(post_id))
                promoted_remaining -= 1
                continue

            try:
                approve = csi_demo_triage.approve_candidate(
                    post_id=str(post_id),
                    decided_by=decided_by,
                    conn=conn,
                    task_hub_conn=task_hub_conn,
                )
            except Exception:
                logger.exception(
                    "intel_auto_promoter: approve_candidate raised for post_id=%s",
                    post_id,
                )
                continue

            if not approve.get("ok"):
                reason = str(approve.get("reason") or "")
                if reason.startswith("already_"):
                    result.skipped_already_decided += 1
                else:
                    logger.warning(
                        "intel_auto_promoter: approve refused for post_id=%s reason=%s",
                        post_id, reason,
                    )
                continue

            result.promoted_this_run += 1
            result.promoted_post_ids.append(str(post_id))
            promoted_remaining -= 1
            logger.info(
                "intel_auto_promoter: promoted post_id=%s score=%.1f task_id=%s",
                post_id, score, approve.get("task_id"),
            )

        result.finished_at = datetime.now(timezone.utc).isoformat()
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("intel_auto_promoter: top-level failure")
        result.error = f"{type(exc).__name__}: {exc}"
        result.finished_at = datetime.now(timezone.utc).isoformat()
        return result
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass
