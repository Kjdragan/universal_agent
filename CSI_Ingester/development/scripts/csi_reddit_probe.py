#!/usr/bin/env python3
"""Probe Reddit watchlist endpoints and print a compact canary summary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx


def _load_watchlist(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    out: list[str] = []
    if isinstance(payload, dict):
        raw = payload.get("subreddits")
        if isinstance(raw, list):
            for row in raw:
                if isinstance(row, str) and row.strip():
                    out.append(row.strip())
                elif isinstance(row, dict):
                    name = str(row.get("name") or "").strip() or str(row.get("subreddit") or "").strip()
                    if name:
                        out.append(name)
    elif isinstance(payload, list):
        for row in payload:
            if isinstance(row, str) and row.strip():
                out.append(row.strip())
            elif isinstance(row, dict):
                name = str(row.get("name") or "").strip() or str(row.get("subreddit") or "").strip()
                if name:
                    out.append(name)

    deduped: list[str] = []
    seen: set[str] = set()
    for item in out:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _first_post(children: list[dict[str, Any]]) -> dict[str, Any]:
    for child in children:
        if isinstance(child, dict) and isinstance(child.get("data"), dict):
            return child["data"]
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Reddit watchlist for canary readiness.")
    parser.add_argument(
        "--watchlist-file",
        default="/opt/universal_agent/CSI_Ingester/development/reddit_watchlist.json",
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--user-agent", default="CSIIngester/1.0 (by u/csi_ingester)")
    args = parser.parse_args()

    watchlist = _load_watchlist(Path(args.watchlist_file).expanduser())
    if not watchlist:
        print("REDDIT_PROBE_WATCHLIST_EMPTY=1")
        return 1

    ok = 0
    fail = 0
    print(f"REDDIT_PROBE_SUBREDDITS={len(watchlist)}")
    with httpx.Client(timeout=max(5, int(args.timeout_seconds)), follow_redirects=True) as client:
        for subreddit in watchlist:
            try:
                resp = client.get(
                    f"https://www.reddit.com/r/{subreddit}/new.json",
                    params={"limit": max(1, min(25, int(args.limit)))},
                    headers={"User-Agent": args.user_agent, "Accept": "application/json"},
                )
            except Exception as exc:
                fail += 1
                print(f"REDDIT_PROBE_FAIL subreddit={subreddit} error={type(exc).__name__}:{exc}")
                continue

            if resp.status_code >= 400:
                fail += 1
                print(f"REDDIT_PROBE_FAIL subreddit={subreddit} status={resp.status_code}")
                continue

            payload = resp.json() if resp.content else {}
            data = payload.get("data") if isinstance(payload, dict) else {}
            children = data.get("children") if isinstance(data, dict) and isinstance(data.get("children"), list) else []
            first = _first_post(children)
            first_id = str(first.get("id") or "")
            first_title = str(first.get("title") or "").replace("\n", " ")
            print(
                "REDDIT_PROBE_OK "
                f"subreddit={subreddit} status={resp.status_code} posts={len(children)} "
                f"first_id={first_id} first_title={first_title[:120]}"
            )
            ok += 1

    print(f"REDDIT_PROBE_OK_COUNT={ok}")
    print(f"REDDIT_PROBE_FAIL_COUNT={fail}")
    return 0 if ok > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
