#!/usr/bin/env python3
"""Archive cleanup script — rotates old workspace archives to keep disk usage bounded.

Deletes archived session directories older than 30 days.
Runs safely as a cron job (idempotent, no side effects on active workspaces).

Usage:
    python archive_cleanup.py [--dry-run] [--max-age-days 30]

Install: Copy to universal_agent/scripts/archive_cleanup.py
Cron: 0 3 * * 0 cd /home/kjdragan/lrepos/universal_agent && uv run python scripts/archive_cleanup.py
"""

import argparse
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ARCHIVE_DIR = Path(__file__).resolve().parent.parent / "AGENT_RUN_WORKSPACES_ARCHIVE"
DEFAULT_MAX_AGE_DAYS = 30


def get_dir_age_days(path: Path) -> float:
    stat = path.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    return (datetime.now(timezone.utc) - mtime).days


def run_cleanup(max_age_days: int, dry_run: bool = False) -> dict:
    if not ARCHIVE_DIR.exists():
        return {"deleted": 0, "freed_bytes": 0, "errors": []}

    results = {"deleted": 0, "freed_bytes": 0, "errors": []}

    for entry in sorted(ARCHIVE_DIR.iterdir()):
        if not entry.is_dir():
            continue
        try:
            age = get_dir_age_days(entry)
            if age > max_age_days:
                size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
                if dry_run:
                    print(f"[DRY-RUN] Would delete: {entry.name} ({age:.0f} days old, {size / 1024:.0f} KB)")
                else:
                    shutil.rmtree(entry)
                    print(f"Deleted: {entry.name} ({age:.0f} days old, {size / 1024:.0f} KB)")
                results["deleted"] += 1
                results["freed_bytes"] += size
        except Exception as e:
            results["errors"].append(f"{entry.name}: {e}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Clean up old workspace archives")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    args = parser.parse_args()

    print(f"Archive cleanup — max age: {args.max_age_days} days, dry_run: {args.dry_run}")
    print(f"Archive dir: {ARCHIVE_DIR}")
    print()

    results = run_cleanup(args.max_age_days, args.dry_run)

    print(f"\nDeleted: {results['deleted']} directories")
    print(f"Freed: {results['freed_bytes'] / 1024:.0f} KB")
    if results["errors"]:
        print(f"Errors: {len(results['errors'])}")
        for err in results["errors"]:
            print(f"  - {err}")


if __name__ == "__main__":
    main()
