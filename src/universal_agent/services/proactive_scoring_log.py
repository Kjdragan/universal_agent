"""Scoring log for proactive insight/convergence brief delivery decisions.

Every time the hourly email composer (or the briefing recap path) considers a
batch of briefs, it persists a row per brief here recording the inputs to the
composite score, the score itself, whether the brief cleared the floor, and
which delivery slot (if any) it filled. This gives the weekly health-check cron
real data to tune the floor / weights against and lets the operator audit
"why did this brief surface but that one didn't" after the fact.

Schema lives in :func:`ensure_schema`. Writes are idempotent on
``(artifact_id, delivery_slot, logged_at)`` so re-running the composer against
the same hour-window doesn't create duplicate rows.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Iterable, Optional

# Delivery slot constants — keep aligned with hourly_insight_email composer.
SLOT_INSIGHT_1 = "insight_1"
SLOT_INSIGHT_2 = "insight_2"
SLOT_HONORABLE_MENTION = "honorable_mention"
SLOT_SUB_THRESHOLD_FILLER = "sub_threshold_filler"
SLOT_NOT_DELIVERED = ""

VALID_SLOTS = {
    SLOT_INSIGHT_1,
    SLOT_INSIGHT_2,
    SLOT_HONORABLE_MENTION,
    SLOT_SUB_THRESHOLD_FILLER,
    SLOT_NOT_DELIVERED,
}


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the scoring-log table + indexes if they do not exist."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS proactive_brief_scoring_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artifact_id TEXT NOT NULL,
            logged_at TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.0,
            channel_breadth INTEGER NOT NULL DEFAULT 0,
            novelty REAL NOT NULL DEFAULT 0.0,
            preference_bonus REAL NOT NULL DEFAULT 0.0,
            composite_score REAL NOT NULL DEFAULT 0.0,
            met_floor INTEGER NOT NULL DEFAULT 0,
            delivered_hourly INTEGER NOT NULL DEFAULT 0,
            delivered_briefing INTEGER NOT NULL DEFAULT 0,
            delivery_slot TEXT NOT NULL DEFAULT '',
            operator_rating INTEGER,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(artifact_id, delivery_slot, logged_at)
        );
        CREATE INDEX IF NOT EXISTS idx_proactive_brief_scoring_log_artifact
            ON proactive_brief_scoring_log(artifact_id, logged_at DESC);
        CREATE INDEX IF NOT EXISTS idx_proactive_brief_scoring_log_delivered
            ON proactive_brief_scoring_log(delivered_hourly, delivered_briefing, logged_at DESC);
        """
    )
    conn.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_score(
    conn: sqlite3.Connection,
    *,
    artifact_id: str,
    confidence: float,
    channel_breadth: int,
    novelty: float,
    preference_bonus: float,
    composite_score: float,
    met_floor: bool,
    delivered_hourly: bool = False,
    delivered_briefing: bool = False,
    delivery_slot: str = SLOT_NOT_DELIVERED,
    metadata: Optional[dict[str, Any]] = None,
    logged_at: Optional[str] = None,
) -> dict[str, Any]:
    """Insert a scoring-log row. Idempotent on ``(artifact_id, slot, logged_at)``.

    Returns the persisted row as a dict (or the existing row when the unique
    constraint fires).
    """
    ensure_schema(conn)
    clean_id = str(artifact_id or "").strip()
    if not clean_id:
        raise ValueError("artifact_id is required")
    slot = delivery_slot if delivery_slot in VALID_SLOTS else SLOT_NOT_DELIVERED
    stamp = logged_at or _now_iso()
    payload = (
        clean_id,
        stamp,
        float(confidence or 0.0),
        int(channel_breadth or 0),
        float(novelty or 0.0),
        float(preference_bonus or 0.0),
        float(composite_score or 0.0),
        1 if met_floor else 0,
        1 if delivered_hourly else 0,
        1 if delivered_briefing else 0,
        slot,
        json.dumps(metadata or {}, ensure_ascii=True),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO proactive_brief_scoring_log (
            artifact_id, logged_at, confidence, channel_breadth, novelty,
            preference_bonus, composite_score, met_floor, delivered_hourly,
            delivered_briefing, delivery_slot, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    conn.commit()
    return get_row(conn, artifact_id=clean_id, delivery_slot=slot, logged_at=stamp) or {}


def mark_delivered_briefing(
    conn: sqlite3.Connection,
    *,
    artifact_ids: Iterable[str],
) -> int:
    """Flip ``delivered_briefing=1`` on rows for the given artifacts.

    Used by the morning/evening briefing recap pass after surfacing the
    artifact so the weekly health-check distinguishes "hourly only" vs "also
    recapped in briefing".
    """
    ensure_schema(conn)
    ids = [str(a or "").strip() for a in artifact_ids if str(a or "").strip()]
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    cur = conn.execute(
        f"""
        UPDATE proactive_brief_scoring_log
        SET delivered_briefing = 1
        WHERE artifact_id IN ({placeholders})
        """,
        tuple(ids),
    )
    conn.commit()
    return int(cur.rowcount or 0)


def get_row(
    conn: sqlite3.Connection,
    *,
    artifact_id: str,
    delivery_slot: str,
    logged_at: str,
) -> Optional[dict[str, Any]]:
    """Fetch a single scoring-log row by its unique key."""
    ensure_schema(conn)
    row = conn.execute(
        """
        SELECT * FROM proactive_brief_scoring_log
        WHERE artifact_id = ? AND delivery_slot = ? AND logged_at = ?
        LIMIT 1
        """,
        (str(artifact_id or "").strip(), delivery_slot, logged_at),
    ).fetchone()
    return dict(row) if row else None


def list_recent(
    conn: sqlite3.Connection,
    *,
    since_iso: str,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Return scoring rows logged at or after ``since_iso``, newest first."""
    ensure_schema(conn)
    rows = conn.execute(
        """
        SELECT * FROM proactive_brief_scoring_log
        WHERE logged_at >= ?
        ORDER BY logged_at DESC
        LIMIT ?
        """,
        (since_iso, max(1, int(limit))),
    ).fetchall()
    return [dict(r) for r in rows]
