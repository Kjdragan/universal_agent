#!/usr/bin/env python3
"""Operational snapshot for CSI parallel-run validation."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def _query_int(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return 0
    return int(row[0] or 0)


def _query_rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def main() -> int:
    parser = argparse.ArgumentParser(description="Show CSI event/delivery counters for parallel-run validation.")
    parser.add_argument("--db-path", required=True, help="Path to CSI sqlite db")
    parser.add_argument("--since-minutes", type=int, default=60, help="Lookback window in minutes")
    args = parser.parse_args()

    db_path = Path(args.db_path).expanduser()
    if not db_path.exists():
        print(f"DB_NOT_FOUND path={db_path}")
        return 2

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    since_expr = f"-{max(1, int(args.since_minutes))} minutes"
    total = _query_int(conn, "SELECT COUNT(*) FROM events")
    recent = _query_int(
        conn,
        "SELECT COUNT(*) FROM events WHERE created_at >= datetime('now', ?)",
        (since_expr,),
    )
    recent_delivered = _query_int(
        conn,
        "SELECT COUNT(*) FROM events WHERE delivered = 1 AND created_at >= datetime('now', ?)",
        (since_expr,),
    )
    recent_undelivered = _query_int(
        conn,
        "SELECT COUNT(*) FROM events WHERE delivered = 0 AND created_at >= datetime('now', ?)",
        (since_expr,),
    )
    dlq_total = _query_int(conn, "SELECT COUNT(*) FROM dead_letter")
    dlq_recent = _query_int(
        conn,
        "SELECT COUNT(*) FROM dead_letter WHERE created_at >= datetime('now', ?)",
        (since_expr,),
    )
    source_rows = _query_rows(
        conn,
        """
        SELECT source, COUNT(*) AS total, SUM(CASE WHEN delivered = 1 THEN 1 ELSE 0 END) AS delivered
        FROM events
        WHERE created_at >= datetime('now', ?)
        GROUP BY source
        ORDER BY source
        """,
        (since_expr,),
    )
    oldest_undelivered_minutes = _query_int(
        conn,
        """
        SELECT COALESCE(CAST((julianday('now') - julianday(MIN(created_at))) * 24 * 60 AS INTEGER), 0)
        FROM events
        WHERE delivered = 0
        """,
    )
    conn.close()

    print(f"DB_PATH={db_path}")
    print(f"WINDOW_MINUTES={max(1, int(args.since_minutes))}")
    print(f"EVENTS_TOTAL={total}")
    print(f"EVENTS_RECENT={recent}")
    print(f"EVENTS_RECENT_DELIVERED={recent_delivered}")
    print(f"EVENTS_RECENT_UNDELIVERED={recent_undelivered}")
    print(f"DLQ_TOTAL={dlq_total}")
    print(f"DLQ_RECENT={dlq_recent}")
    print(f"OLDEST_UNDELIVERED_MINUTES={oldest_undelivered_minutes}")
    for row in source_rows:
        if isinstance(row, sqlite3.Row):
            source = str(row["source"] or "unknown")
            total = int(row["total"] or 0)
            delivered = int(row["delivered"] or 0)
        else:
            source = str(row[0] or "unknown")
            total = int(row[1] or 0)
            delivered = int(row[2] or 0)
        print(f"SOURCE_{source}_RECENT_TOTAL={total}")
        print(f"SOURCE_{source}_RECENT_DELIVERED={delivered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
