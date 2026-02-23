#!/usr/bin/env python3
"""Periodic RSS event alert check for CSI."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"last_seen_id": 0}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {"last_seen_id": 0}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect newly ingested RSS events and print alert lines.")
    parser.add_argument("--db-path", required=True, help="Path to CSI sqlite db")
    parser.add_argument(
        "--state-path",
        default="/opt/universal_agent/CSI_Ingester/development/var/rss_alert_state.json",
        help="Path to persisted alert cursor state",
    )
    parser.add_argument("--max-detail", type=int, default=5, help="Max new events to print details for")
    parser.add_argument(
        "--fail-on-undelivered",
        action="store_true",
        help="Exit non-zero when undelivered RSS events are present",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path).expanduser()
    if not db_path.exists():
        print(f"RSS_ALERT_DB_MISSING path={db_path}")
        return 2

    state_path = Path(args.state_path).expanduser()
    state = _load_state(state_path)
    last_seen_id = int(state.get("last_seen_id") or 0)

    conn = _connect(db_path)
    rows = conn.execute(
        """
        SELECT id, event_id, created_at, delivered, subject_json
        FROM events
        WHERE source = 'youtube_channel_rss' AND id > ?
        ORDER BY id ASC
        """,
        (last_seen_id,),
    ).fetchall()
    undelivered = int(
        conn.execute(
            "SELECT COUNT(*) FROM events WHERE source='youtube_channel_rss' AND delivered=0"
        ).fetchone()[0]
    )
    conn.close()

    new_count = len(rows)
    if rows:
        max_seen = int(rows[-1]["id"])
        _save_state(state_path, {"last_seen_id": max_seen})
    else:
        max_seen = last_seen_id

    print(f"RSS_ALERT_LAST_SEEN_ID={last_seen_id}")
    print(f"RSS_ALERT_MAX_SEEN_ID={max_seen}")
    print(f"RSS_ALERT_NEW_EVENTS={new_count}")
    print(f"RSS_ALERT_UNDELIVERED={undelivered}")

    for row in rows[: max(0, int(args.max_detail))]:
        try:
            subject = json.loads(str(row["subject_json"] or "{}"))
        except Exception:
            subject = {}
        channel_id = str(subject.get("channel_id") or "")
        channel_name = str(subject.get("channel_name") or "")
        video_id = str(subject.get("video_id") or "")
        title = str(subject.get("title") or "").replace("\n", " ").strip()
        delivered = int(row["delivered"] or 0)
        print(
            "RSS_ALERT_EVENT "
            f"id={row['id']} "
            f"event_id={row['event_id']} "
            f"created_at={row['created_at']} "
            f"delivered={delivered} "
            f"channel_id={channel_id} "
            f"channel_name={channel_name!r} "
            f"video_id={video_id} "
            f"title={title!r}"
        )

    if args.fail_on_undelivered and undelivered > 0:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
