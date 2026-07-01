#!/usr/bin/env python3
"""One-time retention reclaim for ``task_hub_evaluations`` in activity_state.db.

The deployed gateway enforces the per-task cap via ``_activity_prune_old`` on
its own (self-heals on the first prune after deploy, reclaiming pages via
``incremental_vacuum``). This script is for an operator who wants the bulk
reclaim NOW, before / independent of a deploy.

Modes
-----
(default)        Dry run: report row counts, top tasks, what the cap would delete,
                 freelist page count, and on-disk file size. No writes.
--apply          Delete rows beyond the newest ``--cap`` per task_id, then run
                 ``PRAGMA incremental_vacuum(<pages>)`` to return freed pages to
                 the OS lock-free (no global lock; gateway can keep serving).
--apply --full-vacuum
                 Additionally run a full ``VACUUM``. This rewrites + shrinks the
                 file in one shot AND adopts ``auto_vacuum`` for the future, but
                 it takes an EXCLUSIVE lock for the whole run (minutes on a 3GB
                 DB). Run it only during a maintenance window.

This is a DESTRUCTIVE operation on production state. Default is dry-run; you
must pass --apply to mutate. Run on the VPS as the ``ua`` user.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import sys

# Make ``universal_agent`` importable when run from a checkout.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from universal_agent import task_hub  # noqa: E402
from universal_agent.durable.db import (  # noqa: E402
    get_activity_db_path,
    get_sqlite_busy_timeout_ms,
)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=get_sqlite_busy_timeout_ms() / 1000.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(f"PRAGMA busy_timeout={get_sqlite_busy_timeout_ms()};")
    return conn


def _freelist_pages(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA freelist_count").fetchone()
    return int(row[0]) if row else 0


def _page_size(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA page_size").fetchone()
    return int(row[0]) if row else 4096


def _would_delete(conn: sqlite3.Connection, cap: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY task_id ORDER BY evaluated_at DESC) AS rn
            FROM task_hub_evaluations
        ) WHERE rn > ?
        """,
        (cap,),
    ).fetchone()
    return int(row["c"] if row else 0)


def _report(conn: sqlite3.Connection, db_path: str, cap: int, label: str) -> None:
    total = conn.execute("SELECT COUNT(*) AS c FROM task_hub_evaluations").fetchone()["c"]
    tasks = conn.execute("SELECT COUNT(DISTINCT task_id) AS c FROM task_hub_evaluations").fetchone()["c"]
    top = conn.execute(
        """
        SELECT task_id, COUNT(*) AS c
        FROM task_hub_evaluations
        GROUP BY task_id
        ORDER BY c DESC
        LIMIT 5
        """
    ).fetchall()
    fl = _freelist_pages(conn)
    psz = _page_size(conn)
    size = os.path.getsize(db_path)
    print(f"--- {label} ---")
    print(f"  DB file               : {db_path}")
    print(f"  file size             : {size/1024/1024:.1f} MB")
    print(f"  total eval rows       : {total}")
    print(f"  distinct tasks        : {tasks}")
    print(f"  freelist pages        : {fl} (~{fl*psz/1024/1024:.1f} MB reclaimable lock-free)")
    print(f"  cap (per task)        : {cap}")
    if total:
        print(f"  cap would delete      : {_would_delete(conn, cap)} rows")
    print("  top tasks by eval count:")
    for r in top:
        print(f"    {r['task_id']}: {r['c']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db-path", default=os.getenv("UA_ACTIVITY_DB_PATH") or get_activity_db_path())
    ap.add_argument("--cap", type=int, default=int(os.getenv("UA_ACTIVITY_EVALUATIONS_MAX_PER_TASK", "500") or 500),
                    help="keep newest N evals per task_id (default 500, min 10)")
    ap.add_argument("--apply", action="store_true", help="actually delete + reclaim (default: dry run)")
    ap.add_argument("--full-vacuum", action="store_true", help="with --apply, also run a full VACUUM (EXCLUSIVE lock)")
    ap.add_argument("--incremental-pages", type=int, default=250000,
                    help="incremental_vacuum page budget after the delete (default 250000 ~ 1GB)")
    args = ap.parse_args()

    cap = max(10, args.cap)
    db_path = args.db_path
    if not os.path.exists(db_path):
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        return 2

    conn = _connect(db_path)
    try:
        _report(conn, db_path, cap, "BEFORE")
        if not args.apply:
            print("\nDRY RUN. Pass --apply to delete over-cap rows and reclaim.")
            return 0

        print("\nAPPLY: deleting over-cap rows...")
        deleted = task_hub.prune_task_hub_evaluations_to_cap(conn, max_per_task=cap)
        print(f"  deleted {deleted} rows")
        if args.full_vacuum:
            print("APPLY: running full VACUUM (EXCLUSIVE lock; gateway will stall)...")
            conn.execute("PRAGMA auto_vacuum = INCREMENTAL;")
            conn.execute("VACUUM;")
        else:
            psz = _page_size(conn)
            print(f"APPLY: incremental_vacuum({args.incremental_pages}) "
                  f"(~{args.incremental_pages*psz/1024/1024:.0f} MB, lock-free)...")
            conn.execute("PRAGMA auto_vacuum = INCREMENTAL;")
            conn.execute(f"PRAGMA incremental_vacuum({int(args.incremental_pages)});")
        _report(conn, db_path, cap, "AFTER")
        print("\nDONE.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
