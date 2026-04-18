"""
signal_curator.py — LLM-driven curator for proactive signal cards (Track 1).

Evaluates pending signal cards using agent reasoning (not scoring heuristics)
and promotes the best candidates to Task Hub items for autonomous execution.

The curator runs as part of the heartbeat cycle when conditions are met:
  - ≥10 pending cards  OR
  - ≥12h since last curation run  (and at least 1 pending card)

The curator uses a reasoning-optimized model (e.g., Gemini Flash) — it's
making judgment calls about what's interesting, not generating code.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any

from universal_agent import task_hub
from universal_agent import proactive_signals
from universal_agent.services.proactive_budget import (
    has_daily_budget,
    get_budget_remaining,
    increment_daily_proactive_count,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_MIN_CARDS = 10
DEFAULT_MIN_HOURS = 12
_LAST_RUN_KEY = "signal_curator_last_run"


def _parse_int_env(key: str, default: int) -> int:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Trigger Logic
# ---------------------------------------------------------------------------

def _get_pending_card_count(conn: sqlite3.Connection) -> int:
    """Count how many pending signal cards exist."""
    proactive_signals.ensure_schema(conn)
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM proactive_signal_cards WHERE status = ?",
        (proactive_signals.CARD_STATUS_PENDING,),
    ).fetchone()
    return int(row["cnt"]) if row else 0


def should_run_curation(conn: sqlite3.Connection) -> bool:
    """Determine if the curator should run this cycle.

    Returns True when:
      - ≥10 pending cards  (immediate trigger)
      - ≥12h since last run AND at least 1 pending card  (time-based trigger)

    Returns False when:
      - No pending cards at all (nothing to curate)
    """
    pending_count = _get_pending_card_count(conn)

    # Never run if there are zero cards
    if pending_count == 0:
        return False

    # Check card count threshold
    min_cards = _parse_int_env("UA_CURATOR_MIN_CARDS", DEFAULT_MIN_CARDS)
    if pending_count >= min_cards:
        return True

    # Check time since last run
    min_hours = _parse_int_env("UA_CURATOR_MIN_HOURS", DEFAULT_MIN_HOURS)
    task_hub.ensure_schema(conn)
    setting = task_hub._get_setting(conn, _LAST_RUN_KEY)
    if not setting:
        # Never run before — only the card-count threshold applies.
        # We need a time baseline before the time-based trigger can fire.
        return False

    last_timestamp = str(setting.get("timestamp") or "")
    if not last_timestamp:
        return False

    try:
        last_dt = datetime.fromisoformat(last_timestamp)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - last_dt
        if elapsed >= timedelta(hours=min_hours):
            return True
    except (ValueError, TypeError):
        return True  # Can't parse — assume it's been long enough

    return False


def get_pending_cards(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    """Pull all pending signal cards for curator evaluation."""
    proactive_signals.ensure_schema(conn)
    rows = conn.execute(
        """
        SELECT card_id, source, card_type, title, summary, priority,
               confidence_score, novelty_score, evidence_json, actions_json,
               metadata_json, created_at
        FROM proactive_signal_cards
        WHERE status = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (proactive_signals.CARD_STATUS_PENDING, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Promotion Logic
# ---------------------------------------------------------------------------

def promote_cards_to_tasks(
    conn: sqlite3.Connection,
    curated: list[dict[str, Any]],
) -> list[str]:
    """Promote curated cards to Task Hub items.

    Args:
        conn: Database connection
        curated: List of dicts from the LLM with:
            - card_id: the source card
            - task_title: free-form title for the task
            - task_description: free-form description
            - priority: 1-4
            - rationale: why this card was selected

    Returns:
        List of created task_ids.
    """
    task_hub.ensure_schema(conn)
    proactive_signals.ensure_schema(conn)
    created_ids: list[str] = []

    for entry in curated:
        # Check budget before each promotion
        if not has_daily_budget(conn):
            logger.info("Daily proactive budget exhausted — stopping card promotion")
            break

        card_id = str(entry.get("card_id") or "")
        task_title = str(entry.get("task_title") or "Untitled proactive task")
        task_description = str(entry.get("task_description") or "")
        priority = max(1, min(4, int(entry.get("priority") or 2)))
        rationale = str(entry.get("rationale") or "")

        # Create the task_id from card_id
        now_iso = datetime.now(timezone.utc).isoformat()
        task_id = f"proactive_signal_{card_id}_{now_iso[:10]}"

        # Build metadata with curator rationale
        import json
        metadata = {
            "curator_rationale": rationale,
            "source_card_id": card_id,
            "promoted_at": now_iso,
        }

        # Upsert into Task Hub
        task_hub.upsert_item(conn, {
            "task_id": task_id,
            "source_kind": "proactive_signal",
            "source_ref": f"card_{card_id}",
            "title": task_title,
            "description": task_description,
            "priority": priority,
            "status": "open",
            "agent_ready": True,
            "trigger_type": "manual",
            "labels": ["proactive", "auto-curated"],
            "metadata": metadata,
        })

        # Update card status to 'promoted'
        conn.execute(
            "UPDATE proactive_signal_cards SET status = ?, updated_at = ? WHERE card_id = ?",
            ("promoted", now_iso, card_id),
        )
        conn.commit()

        # Increment the shared budget counter
        increment_daily_proactive_count(conn, increment=1)

        created_ids.append(task_id)
        logger.info(
            "Promoted signal card %s → task %s: %s",
            card_id, task_id, task_title,
        )

    return created_ids


def record_curation_run(conn: sqlite3.Connection) -> None:
    """Record that a curation run happened now (for time-based trigger)."""
    task_hub.ensure_schema(conn)
    task_hub._set_setting(conn, _LAST_RUN_KEY, {
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
