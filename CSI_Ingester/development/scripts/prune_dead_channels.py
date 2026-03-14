#!/usr/bin/env python3
"""
prune_dead_channels.py — Remove dead YouTube channel IDs from channels_watchlist.json.

Uses already-recorded systemd journal data from the VPS (csi-ingester logs).
NO live HTTP requests are made — no proxy needed.

Usage:
    uv run python3 scripts/prune_dead_channels.py [--threshold N] [--days N] [--dry-run]
    uv run python3 scripts/prune_dead_channels.py --threshold 10 --days 2 --dry-run
    uv run python3 scripts/prune_dead_channels.py --threshold 10 --days 2
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

WATCHLIST_PATH_LOCAL = Path(__file__).resolve().parents[1] / "channels_watchlist.json"
WATCHLIST_PATH_VPS = "/opt/universal_agent/CSI_Ingester/development/channels_watchlist.json"
VPS_HOST = "hostinger-vps"
JOURNAL_UNIT = "csi-ingester"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_journal_404_counts(*, days: int, host: str) -> Counter[str]:
    """
    SSH into VPS and read journal for csi-ingester 404 errors.
    Returns a Counter of channel_id -> how many times it 404'd.
    No live HTTP requests — reads already-recorded log data only.
    """
    since_arg = f"{days} days ago"
    cmd = [
        "ssh", host,
        f"journalctl -u {JOURNAL_UNIT} --no-pager --since='{since_arg}' --output=cat 2>&1"
        " | grep 'status=404'"
        " | grep -oE 'channel_id=[A-Za-z0-9_-]+'"
        " | sed 's/channel_id=//'"
    ]
    print(f"[info] Fetching journal 404 data from {host} (last {days} day(s))…")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode not in (0, 1):  # grep exits 1 if no matches
        print(f"[warn] SSH/journal fetch exited {result.returncode}: {result.stderr[:200]}")
    counts: Counter[str] = Counter()
    for line in result.stdout.splitlines():
        cid = line.strip()
        if cid:
            counts[cid] += 1
    print(f"[info] Found {len(counts)} unique channel IDs with at least one 404.")
    return counts


def load_watchlist(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_watchlist(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def write_watchlist_remote(host: str, data: dict, remote_path: str) -> None:
    """Push updated watchlist JSON to the VPS via SSH pipe."""
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    cmd = ["ssh", host, f"cat > {remote_path}"]
    result = subprocess.run(cmd, input=payload, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to write watchlist to VPS: {result.stderr}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune dead YouTube channels from watchlist using VPS journal data.")
    parser.add_argument("--threshold", type=int, default=10,
                        help="Min 404 count to mark a channel as dead (default: 10)")
    parser.add_argument("--days", type=int, default=2,
                        help="How many days of journal data to analyse (default: 2)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be removed without writing any files")
    parser.add_argument("--host", default=VPS_HOST,
                        help=f"SSH hostname for VPS (default: {VPS_HOST})")
    parser.add_argument("--watchlist-local", type=Path, default=WATCHLIST_PATH_LOCAL,
                        help="Local path to channels_watchlist.json")
    parser.add_argument("--watchlist-vps", default=WATCHLIST_PATH_VPS,
                        help="Remote path to channels_watchlist.json on VPS")
    parser.add_argument("--skip-vps-write", action="store_true",
                        help="Only update local file, skip writing to VPS")
    args = parser.parse_args()

    # 1. Fetch 404 counts from journal
    counts = fetch_journal_404_counts(days=args.days, host=args.host)

    # 2. Determine which channels are dead
    dead_ids = {cid for cid, cnt in counts.items() if cnt >= args.threshold}
    print(f"[info] {len(dead_ids)} channel(s) have ≥ {args.threshold} 404s — marking as dead.")

    # 3. Load local watchlist
    if not args.watchlist_local.exists():
        print(f"[error] Watchlist not found at {args.watchlist_local}")
        return 1
    data = load_watchlist(args.watchlist_local)
    channels: list[dict] = data.get("channels", [])
    original_count = len(channels)

    # 4. Identify channels to remove
    to_remove = [ch for ch in channels if ch.get("channel_id") in dead_ids]
    to_keep   = [ch for ch in channels if ch.get("channel_id") not in dead_ids]

    # 5. Report
    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Results:")
    print(f"  Watchlist total:    {original_count} channels")
    print(f"  Dead (to prune):    {len(to_remove)} channels")
    print(f"  Alive (to keep):    {len(to_keep)} channels")

    if to_remove:
        print(f"\nChannels to remove (sorted by 404 count desc):")
        for ch in sorted(to_remove, key=lambda c: counts.get(c.get("channel_id", ""), 0), reverse=True):
            cid = ch.get("channel_id", "?")
            name = ch.get("channel_name", "?")
            hits = counts.get(cid, 0)
            print(f"  [{hits:>3} 404s]  {name:<40}  {cid}")

    # 6. Dead channel IDs in journal but NOT in watchlist (already removed or never added)
    extra_dead = dead_ids - {ch.get("channel_id") for ch in channels}
    if extra_dead:
        print(f"\n[info] {len(extra_dead)} dead channel ID(s) found in journal but not in local watchlist (already pruned or never added).")

    if args.dry_run:
        print("\n[dry-run] No files written. Remove --dry-run to apply changes.")
        return 0

    if not to_remove:
        print("\n[info] Nothing to prune. Watchlist is clean.")
        return 0

    # 7. Update data
    data["channels"] = to_keep
    data["unique_channels"] = len(to_keep)
    data["pruned_at"] = _utc_now_iso()
    data["pruned_count"] = len(to_remove)
    data["pruned_channel_ids"] = [ch.get("channel_id") for ch in to_remove]

    # 8. Write local
    write_watchlist(args.watchlist_local, data)
    print(f"\n[ok] Local watchlist updated: {args.watchlist_local}")

    # 9. Write to VPS
    if not args.skip_vps_write:
        try:
            write_watchlist_remote(args.host, data, args.watchlist_vps)
            print(f"[ok] VPS watchlist updated: {args.host}:{args.watchlist_vps}")
        except Exception as exc:
            print(f"[error] Could not write to VPS: {exc}")
            print("[hint] Run with --skip-vps-write and copy manually, or push via git.")
            return 1

    print(f"\n[done] Pruned {len(to_remove)} dead channels.")
    print("[next] Restart ingester to pick up changes:")
    print(f"       ssh {args.host} 'sudo systemctl restart {JOURNAL_UNIT}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
