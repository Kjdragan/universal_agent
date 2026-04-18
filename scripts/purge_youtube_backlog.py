#!/usr/bin/env python3
"""Purge YouTube RSS backlog before switching to DataImpulse proxy.

This script is meant to run on the VPS BEFORE restarting the gateway with
PROXY_PROVIDER=dataimpulse. It performs three cleanup actions:

1. Reset the YouTube playlist watcher state file to force a fresh seed.
   On next gateway boot the watcher will mark all current playlist items
   as "seen" and only dispatch truly *new* videos.

2. Finalize (mark failed) any stale YouTube tutorial runs stuck in
   running/queued/blocked status in the durable DB.

3. Delete pending YouTube proactive signal cards from the activity DB
   so the dashboard doesn't show outdated "missing transcript" entries.

Usage (on VPS):
    cd /opt/universal_agent
    uv run python scripts/purge_youtube_backlog.py [--dry-run]
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Paths (default to VPS layout) ──────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
WORKSPACES = REPO_ROOT / "AGENT_RUN_WORKSPACES"

WATCHER_STATE_FILE = WORKSPACES / "youtube_playlist_watcher_state.json"
RUNTIME_DB = WORKSPACES / "runtime_state.db"
ACTIVITY_DB = WORKSPACES / "activity_state.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Step 1: Reset playlist watcher state ───────────────────────────────
def reset_watcher_state(dry_run: bool) -> dict:
    result = {"action": "reset_watcher_state", "file": str(WATCHER_STATE_FILE)}
    if not WATCHER_STATE_FILE.exists():
        result["status"] = "skipped"
        result["reason"] = "state file does not exist — watcher will seed on first boot"
        return result

    # Read current state for reporting
    try:
        current = json.loads(WATCHER_STATE_FILE.read_text(encoding="utf-8"))
        seen_count = len(current.get("seen_ids", []))
        pending_count = len(current.get("pending_dispatch_items", {}))
        retry_count = len(current.get("run_retry_counts", {}))
        failed_count = len(current.get("permanently_failed_video_ids", []))
    except Exception:
        seen_count = pending_count = retry_count = failed_count = -1

    result["before"] = {
        "seen_ids": seen_count,
        "pending_dispatch_items": pending_count,
        "run_retry_counts": retry_count,
        "permanently_failed_video_ids": failed_count,
    }

    if dry_run:
        result["status"] = "dry_run"
        result["would_do"] = "backup and delete state file"
        return result

    # Backup then delete
    backup_path = WATCHER_STATE_FILE.with_suffix(f".pre_purge_{_now_iso().replace(':', '-')}.bak")
    shutil.copy2(WATCHER_STATE_FILE, backup_path)
    WATCHER_STATE_FILE.unlink()
    result["status"] = "deleted"
    result["backup"] = str(backup_path)
    return result


# ── Step 2: Finalize stale YouTube runs in runtime_state.db ────────────
def finalize_stale_runs(dry_run: bool) -> dict:
    result = {"action": "finalize_stale_youtube_runs", "db": str(RUNTIME_DB)}
    if not RUNTIME_DB.exists():
        result["status"] = "skipped"
        result["reason"] = "runtime_state.db does not exist"
        return result

    conn = sqlite3.connect(str(RUNTIME_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        # Check if the runs table exists
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='runs'"
            ).fetchall()
        ]
        if "runs" not in tables:
            result["status"] = "skipped"
            result["reason"] = "runs table does not exist"
            return result

        # Find stale YouTube runs
        rows = conn.execute(
            """
            SELECT r.run_id, r.run_kind,
                   r.latest_attempt_id,
                   a.status AS attempt_status
            FROM runs r
            LEFT JOIN run_attempts a ON a.attempt_id = r.latest_attempt_id
            WHERE r.run_kind = 'youtube_tutorial_hook'
            """
        ).fetchall()

        stale = []
        for row in rows:
            status = str(row["attempt_status"] or "").strip().lower()
            if status in ("running", "queued", "blocked"):
                stale.append({
                    "run_id": row["run_id"],
                    "attempt_id": row["latest_attempt_id"],
                    "attempt_status": status,
                })

        result["total_youtube_runs"] = len(rows)
        result["stale_runs"] = len(stale)

        if not stale:
            result["status"] = "clean"
            result["message"] = "no stale YouTube runs found"
            return result

        if dry_run:
            result["status"] = "dry_run"
            result["would_finalize"] = stale
            return result

        # Mark stale attempts as failed
        # Determine available columns to avoid schema mismatches
        now = _now_iso()
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(run_attempts)").fetchall()
        }
        finalized = 0
        for s in stale:
            if s["attempt_id"]:
                if "error_message" in cols:
                    conn.execute(
                        """
                        UPDATE run_attempts
                        SET status = 'failed',
                            error_message = 'Purged: stale run from proxy downtime',
                            ended_at = ?
                        WHERE attempt_id = ?
                          AND status IN ('running', 'queued', 'blocked')
                        """,
                        (now, s["attempt_id"]),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE run_attempts
                        SET status = 'failed',
                            ended_at = ?
                        WHERE attempt_id = ?
                          AND status IN ('running', 'queued', 'blocked')
                        """,
                        (now, s["attempt_id"]),
                    )
                finalized += 1
        conn.commit()
        result["status"] = "finalized"
        result["finalized_count"] = finalized
    finally:
        conn.close()

    return result


# ── Step 3: Purge pending YouTube proactive signal cards ───────────────
def purge_signal_cards(dry_run: bool) -> dict:
    result = {"action": "purge_youtube_signal_cards", "db": str(ACTIVITY_DB)}
    if not ACTIVITY_DB.exists():
        result["status"] = "skipped"
        result["reason"] = "activity_state.db does not exist"
        return result

    conn = sqlite3.connect(str(ACTIVITY_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        # Check if table exists
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='proactive_signal_cards'"
            ).fetchall()
        ]
        if "proactive_signal_cards" not in tables:
            result["status"] = "skipped"
            result["reason"] = "proactive_signal_cards table does not exist"
            return result

        # Count YouTube pending cards
        pending = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM proactive_signal_cards
            WHERE source = 'youtube'
              AND status = 'pending'
            """
        ).fetchone()
        pending_count = int(pending["cnt"]) if pending else 0

        # Count total YouTube cards of any status
        total = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM proactive_signal_cards
            WHERE source = 'youtube'
            """
        ).fetchone()
        total_count = int(total["cnt"]) if total else 0

        result["total_youtube_cards"] = total_count
        result["pending_youtube_cards"] = pending_count

        if pending_count == 0:
            result["status"] = "clean"
            result["message"] = "no pending YouTube signal cards"
            return result

        if dry_run:
            result["status"] = "dry_run"
            result["would_delete"] = pending_count
            return result

        # Soft-delete by setting status to 'deleted'
        now = _now_iso()
        cursor = conn.execute(
            """
            UPDATE proactive_signal_cards
            SET status = 'deleted',
                updated_at = ?
            WHERE source = 'youtube'
              AND status = 'pending'
            """,
            (now,),
        )
        conn.commit()
        result["status"] = "purged"
        result["deleted_count"] = cursor.rowcount
    finally:
        conn.close()

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Purge YouTube RSS backlog before DataImpulse go-live"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be done without making changes",
    )
    args = parser.parse_args()

    print(f"{'=' * 60}")
    print(f"YouTube Backlog Purge — {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"Time: {_now_iso()}")
    print(f"Repo: {REPO_ROOT}")
    print(f"{'=' * 60}")

    results = []

    print("\n[1/3] Resetting playlist watcher state...")
    r1 = reset_watcher_state(args.dry_run)
    results.append(r1)
    print(f"  → {json.dumps(r1, indent=2)}")

    print("\n[2/3] Finalizing stale YouTube runs...")
    r2 = finalize_stale_runs(args.dry_run)
    results.append(r2)
    print(f"  → {json.dumps(r2, indent=2)}")

    print("\n[3/3] Purging pending YouTube signal cards...")
    r3 = purge_signal_cards(args.dry_run)
    results.append(r3)
    print(f"  → {json.dumps(r3, indent=2)}")

    print(f"\n{'=' * 60}")
    print("Summary:")
    for r in results:
        print(f"  {r['action']}: {r['status']}")
    print(f"{'=' * 60}")

    if args.dry_run:
        print("\n⚠️  This was a DRY RUN. Re-run without --dry-run to execute.")


if __name__ == "__main__":
    main()
