"""proactive_budget.py — Shared daily budget for the autonomous proactive pipeline.

Both the Signal Curator (Track 1) and the Reflection Engine (Track 2) share
a single daily budget counter.  Only tasks with source_kind in
('proactive_signal', 'reflection') count against this budget.
Cron/system_command tasks are NEVER counted.

The budget resets at midnight (UTC date boundary).
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
import random
import sqlite3

from universal_agent import task_hub

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_DAILY_BUDGET = 10
_DAILY_BUDGET_KEY = "proactive_daily_budget_counter"

# Ideation pacing — spread the daily budget across the overnight window instead
# of letting every idle heartbeat tick fire ideation back-to-back right after
# the budget reset (the surge that would spike ZAI rate limits). At the default
# ~1h base interval, a 10-task budget naturally drips across ~10h.
DEFAULT_IDEATION_MIN_INTERVAL_SECONDS = 3600
DEFAULT_IDEATION_JITTER_FRAC = 0.25
_LAST_IDEATION_KEY = "proactive_last_ideation_at"


def _parse_int_env(key: str, default: int) -> int:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def _parse_float_env(key: str, default: float) -> float:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_daily_proactive_count(conn: sqlite3.Connection) -> int:
    """How many proactive tasks have been created today across both tracks.

    Only counts source_kind in ('proactive_signal', 'reflection').
    Excludes system_command/cron jobs — those are managed separately.
    """
    task_hub.ensure_schema(conn)
    setting = task_hub._get_setting(conn, _DAILY_BUDGET_KEY)
    if not setting:
        return 0
    counter_date = str(setting.get("date") or "")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if counter_date != today:
        return 0  # Reset for new day
    return int(setting.get("count") or 0)


def increment_daily_proactive_count(conn: sqlite3.Connection, increment: int = 1) -> int:
    """Increment and return the updated daily proactive count."""
    task_hub.ensure_schema(conn)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    setting = task_hub._get_setting(conn, _DAILY_BUDGET_KEY)
    if not setting or str(setting.get("date") or "") != today:
        new_count = increment
    else:
        new_count = int(setting.get("count") or 0) + increment
    task_hub._set_setting(conn, _DAILY_BUDGET_KEY, {
        "date": today,
        "count": new_count,
    })
    return new_count


def has_daily_budget(conn: sqlite3.Connection) -> bool:
    """Check if there's remaining budget for proactive task creation.

    Checks against UA_PROACTIVE_DAILY_BUDGET (default: 10).
    """
    max_tasks = _parse_int_env("UA_PROACTIVE_DAILY_BUDGET", DEFAULT_DAILY_BUDGET)
    current = get_daily_proactive_count(conn)
    return current < max_tasks


def get_budget_remaining(conn: sqlite3.Connection) -> int:
    """Return how many more proactive tasks can be created today."""
    max_tasks = _parse_int_env("UA_PROACTIVE_DAILY_BUDGET", DEFAULT_DAILY_BUDGET)
    current = get_daily_proactive_count(conn)
    return max(0, max_tasks - current)


def should_ideate_now(conn: sqlite3.Connection, *, now: float | None = None) -> bool:
    """Whether ideation may fire on this idle tick, given pacing.

    Spreads the daily budget across the window by requiring a jittered minimum
    interval since the last ideation fired. The heartbeat only consults this
    inside its reflection branch (queue empty, no other actionable work), so a
    False simply lets the idle tick fall through to a cheap skip — it can never
    interrupt real work. Set UA_PROACTIVE_IDEATION_MIN_INTERVAL_SECONDS=0 to
    disable pacing.

    ponytail: min-interval + jitter only; the jitter floor already caps the
    effective rate. If a hard per-hour ceiling is ever needed, store a windowed
    timestamp list under _LAST_IDEATION_KEY instead.
    """
    base = _parse_int_env(
        "UA_PROACTIVE_IDEATION_MIN_INTERVAL_SECONDS",
        DEFAULT_IDEATION_MIN_INTERVAL_SECONDS,
    )
    if base <= 0:
        return True  # pacing disabled
    task_hub.ensure_schema(conn)
    setting = task_hub._get_setting(conn, _LAST_IDEATION_KEY)
    last = float(setting.get("ts") or 0.0) if setting else 0.0
    now_ts = float(now) if now is not None else datetime.now(timezone.utc).timestamp()
    if last <= 0.0 or now_ts < last:
        return True  # first ideation of the window (or clock moved backwards)
    jitter = _parse_float_env("UA_PROACTIVE_IDEATION_JITTER_FRAC", DEFAULT_IDEATION_JITTER_FRAC)
    jitter = max(0.0, min(0.95, jitter))
    required = base * random.uniform(1.0 - jitter, 1.0 + jitter)
    return (now_ts - last) >= required


def record_ideation_now(conn: sqlite3.Connection, *, now: float | None = None) -> None:
    """Stamp the last-ideation time. Call whenever an ideation tick fires."""
    task_hub.ensure_schema(conn)
    now_ts = float(now) if now is not None else datetime.now(timezone.utc).timestamp()
    task_hub._set_setting(conn, _LAST_IDEATION_KEY, {"ts": now_ts})
