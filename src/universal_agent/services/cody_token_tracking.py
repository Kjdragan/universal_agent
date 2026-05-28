"""Hermes Phase E.2b — Cody token-usage tracking.

Records per-mission token usage emitted by the Claude Code CLI's
``stream-json`` ``result`` event into ``cody_token_usage``, and serves
the dashboard's "Cody Anthropic Token Usage" tile via a window-based
accumulator pattern:

* Capture: ``record_token_usage(...)`` writes one row per completed
  mission. Hooked from ``vp/clients/claude_cli_client.py`` after the
  CLI subprocess closes.
* Aggregate: ``summarize_window(...)`` sums every row where
  ``recorded_at >= reset_at``. The dashboard tile calls this on each
  poll and renders the totals.
* Reset: ``reset_window(...)`` bumps the ``reset_at`` cursor in
  ``task_hub_settings.cody_token_tracking_window``. History under the
  cursor is preserved for forensics — the tile just shows the new
  window.

The capture function is best-effort and never raises; if the DB write
fails (lock, schema drift, etc.) we log and move on. Token tracking is
operator telemetry, not a correctness gate.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import sqlite3
from typing import Any, Optional

logger = logging.getLogger(__name__)

_WINDOW_SETTING_KEY = "cody_token_tracking_window"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return default


def record_token_usage(
    conn: sqlite3.Connection,
    *,
    cody_mode: str,
    mission_id: Optional[str] = None,
    task_id: Optional[str] = None,
    model: Optional[str] = None,
    cost_info: Optional[dict[str, Any]] = None,
) -> Optional[int]:
    """Persist one row of token usage for a completed mission.

    Best-effort: returns the new row id on success, ``None`` on any
    failure (logged at WARNING). Never raises — callers in the CLI
    client should not have their happy path coupled to telemetry.

    Args:
        conn: SQLite connection with task_hub schema applied.
        cody_mode: "zai" or "anthropic" — required so the tile can
            filter to Anthropic-only totals.
        mission_id: provenance for audit / per-mission drill-in.
        task_id: provenance for audit / per-mission drill-in.
        model: model identifier from the CLI's result event (e.g.
            ``"claude-opus-4-8"``, ``"glm-5.1"``).
        cost_info: the dict captured from the CLI's ``result`` event.
            Expected keys (any subset; missing => 0):
                * ``input_tokens``
                * ``output_tokens``
                * ``cache_creation_input_tokens``
                * ``cache_read_input_tokens``
                * ``cost_usd`` / ``total_cost_usd``
                * ``duration_ms``

    """
    try:
        normalized_mode = str(cody_mode or "").strip().lower()
        if normalized_mode not in {"zai", "anthropic"}:
            # Defensive — schema doesn't constrain it but tile depends on
            # this being one of the two known values for filtering.
            logger.warning(
                "record_token_usage: unrecognized cody_mode=%r — skipping",
                cody_mode,
            )
            return None
        ci = dict(cost_info or {})
        cost_usd = _safe_float(ci.get("total_cost_usd") or ci.get("cost_usd"))

        cursor = conn.execute(
            """
            INSERT INTO cody_token_usage (
                mission_id, task_id, cody_mode, model,
                input_tokens, output_tokens,
                cache_creation_input_tokens, cache_read_input_tokens,
                total_cost_usd, duration_ms, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(mission_id or "").strip() or None,
                str(task_id or "").strip() or None,
                normalized_mode,
                str(model or "").strip() or None,
                _safe_int(ci.get("input_tokens")),
                _safe_int(ci.get("output_tokens")),
                _safe_int(ci.get("cache_creation_input_tokens")),
                _safe_int(ci.get("cache_read_input_tokens")),
                cost_usd,
                _safe_int(ci.get("duration_ms")),
                _now_iso(),
            ),
        )
        conn.commit()
        return cursor.lastrowid
    except Exception as exc:
        logger.warning("record_token_usage failed: %s", exc, exc_info=False)
        return None


def get_window_state(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return the operator-configured tracking window cursor + audit.

    The ``reset_at`` ISO string is the lower bound; rows with
    ``recorded_at >= reset_at`` are counted in the current window.
    When no window has been initialized, returns a zero-cursor record
    so the dashboard tile shows the all-time totals as the current
    window (operator can hit Refresh to reset).
    """
    from universal_agent.task_hub import _get_setting

    record = _get_setting(conn, _WINDOW_SETTING_KEY, default={})
    if not isinstance(record, dict):
        record = {}
    reset_at = str(record.get("reset_at") or "").strip()
    if not reset_at:
        reset_at = "1970-01-01T00:00:00+00:00"
    return {
        "reset_at": reset_at,
        "reset_by": record.get("reset_by") or "",
    }


def reset_window(
    conn: sqlite3.Connection, *, reset_by: str = "operator"
) -> dict[str, Any]:
    """Bump the tracking window to start fresh from now.

    History rows are preserved; only the cursor moves. The dashboard
    tile will then show zero totals on the next poll, and the
    accumulator starts counting again as new missions complete.
    """
    from universal_agent.task_hub import _set_setting

    now_iso = _now_iso()
    record = {
        "reset_at": now_iso,
        "reset_by": str(reset_by or "operator"),
    }
    _set_setting(conn, _WINDOW_SETTING_KEY, record)
    return record


def summarize_window(
    conn: sqlite3.Connection, *, cody_mode: Optional[str] = "anthropic"
) -> dict[str, Any]:
    """Aggregate token usage in the current tracking window.

    Args:
        conn: SQLite connection with task_hub schema applied.
        cody_mode: filter to this mode. Default "anthropic" so the
            primary dashboard tile shows Anthropic Max cost. Pass
            ``None`` to aggregate across all modes.

    Returns a dict shaped for direct JSON return to the dashboard:
        {
            "reset_at": ISO,
            "reset_by": str,
            "days_in_window": float (days since reset_at),
            "mission_count": int,
            "input_tokens": int,
            "output_tokens": int,
            "cache_creation_input_tokens": int,
            "cache_read_input_tokens": int,
            "total_cost_usd": float,
            "model_breakdown": [{"model": str, "missions": int,
                                  "input_tokens": int, "output_tokens": int,
                                  "total_cost_usd": float}, ...],
            "cody_mode_filter": str | None,
        }

    """
    window = get_window_state(conn)
    reset_at = window["reset_at"]

    params: tuple = (reset_at,)
    where_clause = "WHERE recorded_at >= ?"
    if cody_mode:
        where_clause += " AND cody_mode = ?"
        params = (reset_at, cody_mode)

    totals_row = conn.execute(
        f"""
        SELECT
            COUNT(*)                                AS mission_count,
            COALESCE(SUM(input_tokens), 0)          AS input_tokens,
            COALESCE(SUM(output_tokens), 0)         AS output_tokens,
            COALESCE(SUM(cache_creation_input_tokens), 0) AS cache_creation_input_tokens,
            COALESCE(SUM(cache_read_input_tokens), 0)     AS cache_read_input_tokens,
            COALESCE(SUM(total_cost_usd), 0.0)      AS total_cost_usd
        FROM cody_token_usage
        {where_clause}
        """,
        params,
    ).fetchone()
    totals = dict(totals_row) if totals_row else {}

    model_rows = conn.execute(
        f"""
        SELECT
            COALESCE(model, '(unknown)') AS model,
            COUNT(*) AS missions,
            COALESCE(SUM(input_tokens), 0) AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(total_cost_usd), 0.0) AS total_cost_usd
        FROM cody_token_usage
        {where_clause}
        GROUP BY COALESCE(model, '(unknown)')
        ORDER BY missions DESC
        """,
        params,
    ).fetchall()
    model_breakdown = [dict(r) for r in model_rows]

    # Compute days_in_window for the tile label.
    try:
        reset_dt = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
        if reset_dt.tzinfo is None:
            reset_dt = reset_dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - reset_dt
        days_in_window = max(0.0, delta.total_seconds() / 86400.0)
    except Exception:
        days_in_window = 0.0

    return {
        "reset_at": reset_at,
        "reset_by": window.get("reset_by") or "",
        "days_in_window": round(days_in_window, 2),
        "mission_count": int(totals.get("mission_count") or 0),
        "input_tokens": int(totals.get("input_tokens") or 0),
        "output_tokens": int(totals.get("output_tokens") or 0),
        "cache_creation_input_tokens": int(totals.get("cache_creation_input_tokens") or 0),
        "cache_read_input_tokens": int(totals.get("cache_read_input_tokens") or 0),
        "total_cost_usd": float(totals.get("total_cost_usd") or 0.0),
        "model_breakdown": model_breakdown,
        "cody_mode_filter": cody_mode,
    }


__all__ = [
    "record_token_usage",
    "get_window_state",
    "reset_window",
    "summarize_window",
]
