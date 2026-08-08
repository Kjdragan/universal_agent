"""proactive_budget.py — Shared daily budget for the autonomous proactive pipeline.

Both the Signal Curator (Track 1) and the Reflection Engine (Track 2) share
a single daily budget counter. Only *actual task creations* with source_kind
in ('proactive_signal', 'reflection') count against this budget -- the
counter is incremented exactly once, from task_hub.upsert_item, on the first
INSERT of a task_id with one of those source_kinds. It is NEVER incremented
from a re-upsert of an existing task_id, and NEVER incremented merely
because a reflection/ideation prompt was built and injected into a heartbeat
turn (an LLM turn that gets the prompt is not guaranteed to actually create a
task -- it may propose nothing, error out, or get interrupted). Cron/
system_command tasks are NEVER counted.

This module tracks two SEPARATE counters, both date-scoped (reset at
midnight UTC):
  - proactive_daily_budget_counter -- the real budget: true task creations,
    incremented only from task_hub.upsert_item's insert path. Gates
    ``has_daily_budget``/``get_budget_remaining``, which callers must check
    before spending inference on an ideation turn.
  - proactive_ideation_ticks -- how many times ideation actually FIRED
    (a reflection prompt was built and injected), independent of whether
    that turn went on to create a task. This is a pacing/observability
    signal, not the budget: an LLM that keeps declining to propose anything
    would never advance the real budget counter, so without a separate tick
    ceiling (``has_ideation_tick_budget``) nothing would stop it from
    consuming an ideation-context-build + inference call on every eligible
    heartbeat tick all day. Callers increment this via
    ``increment_ideation_ticks`` at the point a reflection prompt is
    actually injected (heartbeat_service.py's reflection branch).

The budget resets at midnight (UTC date boundary).
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
import random
import sqlite3

from universal_agent import task_hub
from universal_agent.services.dormancy import is_active_window

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_DAILY_BUDGET = 10
_DAILY_BUDGET_KEY = "proactive_daily_budget_counter"

# Ideation ticks: a separate, more generous daily ceiling on how many times
# ideation may FIRE (prompt built + injected), regardless of whether the turn
# goes on to create a task. Backstops the case where the real budget counter
# never advances (the model keeps declining to propose anything) -- without
# this a "never proposes" run would fire ideation on every eligible tick all
# day, burning inference for zero task output. Generous by design: it is a
# safety ceiling, not the primary pacing knob (that's the min-interval below
# plus the active-window gate).
DEFAULT_IDEATION_TICK_BUDGET = 20
_IDEATION_TICKS_KEY = "proactive_ideation_ticks"

# Ideation pacing — spread the daily budget across the day's ACTIVE window
# instead of letting every idle heartbeat tick fire ideation back-to-back
# right after the budget reset (the surge that would spike ZAI rate limits).
# should_ideate_now additionally refuses outside the Houston 06:00-22:00
# active window (services.dormancy) -- ideation is exactly the class of
# content-generation work that window exists for: proposals only sit in the
# morning ideation report anyway, so firing overnight just burns quota to
# produce intelligence nobody reads until morning. At the default ~2h base
# interval, ideation fires ~8x across the ~16h active window — raised from
# ~1h on 2026-08-08 after the ZAI usage audit measured idle ideation turns
# as the dominant weekly-quota consumer (~1.4M cache-inclusive tokens per
# heartbeat turn); halving the cadence halves that burn while the morning
# report still gets a full slate of proposals.
DEFAULT_IDEATION_MIN_INTERVAL_SECONDS = 7200
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
# Shared date-scoped counter (used by both the real budget and the ticks key)
# ---------------------------------------------------------------------------

def _get_daily_counter(conn: sqlite3.Connection, key: str) -> int:
    """Read a date-scoped counter under ``key``; 0 if unset or from a prior day."""
    task_hub.ensure_schema(conn)
    setting = task_hub._get_setting(conn, key)
    if not setting:
        return 0
    counter_date = str(setting.get("date") or "")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if counter_date != today:
        return 0  # Reset for new day
    return int(setting.get("count") or 0)


def _increment_daily_counter(conn: sqlite3.Connection, key: str, increment: int = 1) -> int:
    """Increment a date-scoped counter under ``key``, resetting across a day boundary."""
    task_hub.ensure_schema(conn)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    setting = task_hub._get_setting(conn, key)
    if not setting or str(setting.get("date") or "") != today:
        new_count = increment
    else:
        new_count = int(setting.get("count") or 0) + increment
    task_hub._set_setting(conn, key, {
        "date": today,
        "count": new_count,
    })
    return new_count


# ---------------------------------------------------------------------------
# Public API — the real budget (true task creations)
# ---------------------------------------------------------------------------

def get_daily_proactive_count(conn: sqlite3.Connection) -> int:
    """How many proactive TASKS have been created today across both tracks.

    Only counts source_kind in ('proactive_signal', 'reflection'), and only
    real task_hub.upsert_item INSERTs (see that function) — never a
    re-upsert of an existing task_id, never a mere ideation prompt
    injection. Excludes system_command/cron jobs — those are managed
    separately.
    """
    return _get_daily_counter(conn, _DAILY_BUDGET_KEY)


def increment_daily_proactive_count(conn: sqlite3.Connection, increment: int = 1) -> int:
    """Increment and return the updated daily proactive task count.

    Callers: task_hub.upsert_item, on the first INSERT of a task_id with
    source_kind in ('proactive_signal', 'reflection'). Do not call this
    from a prompt-composition path — it must track real task creations
    only, not attempts.
    """
    return _increment_daily_counter(conn, _DAILY_BUDGET_KEY, increment)


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


# ---------------------------------------------------------------------------
# Public API — ideation ticks (pacing/observability, NOT the budget)
# ---------------------------------------------------------------------------

def get_ideation_ticks(conn: sqlite3.Connection) -> int:
    """How many times ideation has fired today (prompt built + injected).

    Independent of whether that turn went on to create a task — see the
    module docstring for why this must be tracked separately from the real
    budget counter.
    """
    return _get_daily_counter(conn, _IDEATION_TICKS_KEY)


def increment_ideation_ticks(conn: sqlite3.Connection, increment: int = 1) -> int:
    """Increment and return the updated daily ideation-tick count.

    Callers: heartbeat_service.py's reflection branch, at the point a
    reflection prompt is actually built and injected into the turn.
    """
    return _increment_daily_counter(conn, _IDEATION_TICKS_KEY, increment)


def has_ideation_tick_budget(conn: sqlite3.Connection) -> bool:
    """Whether ideation may still fire today, independent of the task budget.

    Checks against UA_PROACTIVE_IDEATION_TICK_BUDGET (default: 20) — a
    generous safety ceiling on total daily ideation ATTEMPTS, so a run where
    the model keeps declining to propose anything (the real budget counter
    never advances) still stops firing eventually instead of consuming
    inference on every eligible tick all day.
    """
    max_ticks = _parse_int_env("UA_PROACTIVE_IDEATION_TICK_BUDGET", DEFAULT_IDEATION_TICK_BUDGET)
    return get_ideation_ticks(conn) < max_ticks


# ---------------------------------------------------------------------------
# Public API — pacing (when, not how many)
# ---------------------------------------------------------------------------

def should_ideate_now(conn: sqlite3.Connection, *, now: float | None = None) -> bool:
    """Whether ideation may fire on this idle tick, given pacing.

    Two gates, both must pass:
      1. Active window — ideation only fires inside the Houston 06:00-22:00
         active window (services.dormancy.is_active_window). Ideation is
         exactly the content-generation work that window exists for:
         proposals only surface in the next morning's ideation report, so
         firing overnight just burns quota to produce intelligence nobody
         reads until morning.
      2. Jittered minimum interval since the last ideation fired, spreading
         the daily budget across that window instead of bursting right after
         a reset. Set UA_PROACTIVE_IDEATION_MIN_INTERVAL_SECONDS=0 to
         disable this second gate (the window gate still applies).

    The heartbeat only consults this inside its reflection branch (queue
    empty, no other actionable work), so a False simply lets the idle tick
    fall through to a cheap skip — it can never interrupt real work.
    """
    now_ts = float(now) if now is not None else datetime.now(timezone.utc).timestamp()
    if not is_active_window(now_ts):
        return False  # outside the Houston 06:00-22:00 active window
    base = _parse_int_env(
        "UA_PROACTIVE_IDEATION_MIN_INTERVAL_SECONDS",
        DEFAULT_IDEATION_MIN_INTERVAL_SECONDS,
    )
    if base <= 0:
        return True  # interval pacing disabled (window gate above still applies)
    task_hub.ensure_schema(conn)
    setting = task_hub._get_setting(conn, _LAST_IDEATION_KEY)
    last = float(setting.get("ts") or 0.0) if setting else 0.0
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
