#!/usr/bin/env python3
"""Restore a Daily YouTube Digest playlist from its saved repopulate pocket."""

from __future__ import annotations

import argparse
import json

from universal_agent.scripts.youtube_daily_digest import DAYS, repopulate_digest_playlist


def main() -> int:
    parser = argparse.ArgumentParser(description="Repopulate a day-specific YouTube Digest playlist.")
    parser.add_argument("--day", default="MONDAY", help="Digest day to restore, e.g. MONDAY.")
    parser.add_argument("--date", default=None, help="Pocket date to restore, YYYY-MM-DD. Defaults to latest.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without adding videos to YouTube.")
    args = parser.parse_args()

    day = args.day.upper()
    if day not in DAYS:
        parser.error(f"--day must be one of {', '.join(DAYS)}")

    result = repopulate_digest_playlist(day_override=day, date_override=args.date, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
