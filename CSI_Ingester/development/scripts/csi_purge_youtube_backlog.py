#!/usr/bin/env python3
"""Purge stale YouTube backlog from csi.db.

Removes:
  1. rss_event_analysis rows where transcript_status='failed' and transcript_ref
     points at the local gateway (these come from the broken-auth era and carry
     no real signal).
  2. youtube_channel_rss events that have no surviving rss_event_analysis row
     (the actual backlog the enrichment timer would otherwise chew through).

Prints before/after counts. --dry-run shows what would be deleted without
touching the DB. --confirm is required to actually delete.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys

STALE_REF_PATTERN = "%@127.0.0.1:8002"


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    cursor = conn.cursor()
    out: dict[str, int] = {}
    out["events_youtube_total"] = cursor.execute(
        "SELECT COUNT(*) FROM events WHERE source='youtube_channel_rss'"
    ).fetchone()[0]
    out["rss_event_analysis_total"] = cursor.execute(
        "SELECT COUNT(*) FROM rss_event_analysis"
    ).fetchone()[0]
    out["events_pending_analysis"] = cursor.execute(
        """
        SELECT COUNT(*) FROM events e
        LEFT JOIN rss_event_analysis a ON a.event_id = e.event_id
        WHERE e.source='youtube_channel_rss' AND a.event_id IS NULL
        """
    ).fetchone()[0]
    out["analysis_stale_failed"] = cursor.execute(
        """
        SELECT COUNT(*) FROM rss_event_analysis
        WHERE transcript_status='failed' AND transcript_ref LIKE ?
        """,
        (STALE_REF_PATTERN,),
    ).fetchone()[0]
    return out


def _print_counts(label: str, counts: dict[str, int]) -> None:
    print(f"--- {label} ---")
    for key, value in counts.items():
        print(f"{key}={value}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default="/var/lib/universal-agent/csi/csi.db")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show counts and proposed deletes without modifying the DB.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually perform the deletes. Required unless --dry-run.",
    )
    args = parser.parse_args()

    if not args.dry_run and not args.confirm:
        print("Refusing to delete without --confirm. Re-run with --dry-run or --confirm.")
        return 2

    db_path = Path(args.db_path).expanduser()
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return 2

    conn = sqlite3.connect(str(db_path))
    try:
        before = _counts(conn)
        _print_counts("before", before)

        if args.dry_run:
            print("--- dry-run: no changes applied ---")
            return 0

        cursor = conn.cursor()
        cursor.execute(
            """
            DELETE FROM rss_event_analysis
            WHERE transcript_status='failed' AND transcript_ref LIKE ?
            """,
            (STALE_REF_PATTERN,),
        )
        deleted_analysis = cursor.rowcount
        cursor.execute(
            """
            DELETE FROM events
            WHERE source='youtube_channel_rss'
              AND event_id NOT IN (SELECT event_id FROM rss_event_analysis)
            """
        )
        deleted_events = cursor.rowcount
        conn.commit()

        print(f"deleted_analysis_rows={deleted_analysis}")
        print(f"deleted_event_rows={deleted_events}")
        after = _counts(conn)
        _print_counts("after", after)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
