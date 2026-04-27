"""
proactive_budget.py — Shared daily budget for the autonomous proactive pipeline.

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
import sqlite3

from universal_agent import task_hub

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_DAILY_BUDGET = 10
_DAILY_BUDGET_KEY = "proactive_daily_budget_counter"


def _parse_int_env(key: str, default: int) -> int:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
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
