"""Gateway startup pre-warm — primes SQLite page cache for dashboard hot paths.

After a merge-to-main deploy, the first dashboard request pays the cold-page
+ connection-setup cost (observed: 12.9s for ``/api/v1/dashboard/proactive-
signals`` first hit after restart, vs <100ms steady state). The helper here
runs representative SELECT queries against the activity DB at lifespan
startup so the cache is warm before the operator's browser arrives.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Callable

# SQLite tables the dashboard's slowest endpoints touch on cold start. Each
# gets a single bounded SELECT to load its index + first data pages into the
# OS page cache. Order is "narrowest filter first" so the SQLite planner has
# its statistics primed for the queries the dashboard endpoints actually run.
_HOT_TABLES: tuple[str, ...] = (
    "task_hub_items",
    "proactive_signal_cards",
)


def prewarm_dashboard_db(connect: Callable[[], sqlite3.Connection]) -> dict[str, Any]:
    """Open a connection and run representative SELECTs to prime SQLite caches.

    Best-effort: any failure is captured in the summary rather than raised.
    A broken DB is bad at startup but strictly worse if the gateway itself
    fails to come up. Returns a summary with:

      - ``db_warmed``: ``True`` only when at least one table was successfully
        queried. ``False`` if ``connect()`` itself raised.
      - ``duration_ms``: wall-clock time spent on the whole attempt.
      - ``tables_warmed``: list of tables successfully primed.
      - ``tables_failed``: mapping of ``table -> error_message`` for tables
        whose SELECT raised (missing table, locked, etc.).
      - ``error``: only present when ``connect()`` itself raised.
    """
    started = time.perf_counter()
    tables_warmed: list[str] = []
    tables_failed: dict[str, str] = {}
    try:
        conn = connect()
    except Exception as exc:
        return {
            "db_warmed": False,
            "duration_ms": (time.perf_counter() - started) * 1000.0,
            "tables_warmed": tables_warmed,
            "tables_failed": tables_failed,
            "error": str(exc),
        }
    for table in _HOT_TABLES:
        try:
            # ``LIMIT 1`` keeps the warmup bounded — we just need at least one
            # index page + one data page touched so subsequent queries hit
            # warm cache. A larger scan would risk slow startup on busy
            # production DBs.
            conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
            tables_warmed.append(table)
        except Exception as exc:
            tables_failed[table] = str(exc)
    duration_ms = (time.perf_counter() - started) * 1000.0
    return {
        "db_warmed": bool(tables_warmed),
        "duration_ms": duration_ms,
        "tables_warmed": tables_warmed,
        "tables_failed": tables_failed,
    }
