#!/usr/bin/env python3
"""Generate daily CSI ingestion summary artifacts."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _utc_day_bounds(target_day: str | None) -> tuple[str, str, str]:
    if target_day:
        day = datetime.strptime(target_day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        now = datetime.now(timezone.utc)
        day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    start = day
    end = day + timedelta(days=1)
    return day.strftime("%Y-%m-%d"), start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _query_events(conn: sqlite3.Connection, start_ts: str, end_ts: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, event_id, source, event_type, created_at, delivered, subject_json
        FROM events
        WHERE created_at >= ? AND created_at < ?
        ORDER BY created_at ASC, id ASC
        """,
        (start_ts, end_ts),
    ).fetchall()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate daily CSI summary markdown/json artifacts.")
    parser.add_argument("--db-path", required=True, help="Path to CSI sqlite db")
    parser.add_argument(
        "--output-root",
        default="/opt/universal_agent/artifacts/csi-reports",
        help="Directory root for daily summaries",
    )
    parser.add_argument("--day", default="", help="UTC day in YYYY-MM-DD (default: today UTC)")
    args = parser.parse_args()

    db_path = Path(args.db_path).expanduser()
    if not db_path.exists():
        print(f"DAILY_SUMMARY_DB_MISSING path={db_path}")
        return 2

    day, start_ts, end_ts = _utc_day_bounds(args.day or None)
    conn = _connect(db_path)
    rows = _query_events(conn, start_ts, end_ts)
    conn.close()

    total = len(rows)
    delivered = sum(int(r["delivered"] or 0) for r in rows)
    undelivered = total - delivered

    by_source: Counter[str] = Counter()
    by_event_type: Counter[str] = Counter()
    by_source_delivered: dict[str, int] = defaultdict(int)
    rss_channels: Counter[str] = Counter()
    rss_channel_names: dict[str, str] = {}

    for row in rows:
        source = str(row["source"] or "unknown")
        event_type = str(row["event_type"] or "unknown")
        is_delivered = int(row["delivered"] or 0)
        by_source[source] += 1
        by_event_type[event_type] += 1
        by_source_delivered[source] += is_delivered
        if source == "youtube_channel_rss":
            try:
                subject = json.loads(str(row["subject_json"] or "{}"))
            except Exception:
                subject = {}
            channel_id = str(subject.get("channel_id") or "").strip() or "unknown"
            channel_name = str(subject.get("channel_name") or "").strip()
            rss_channels[channel_id] += 1
            if channel_name:
                rss_channel_names[channel_id] = channel_name

    output_dir = Path(args.output_root).expanduser() / day
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "summary.json"
    md_path = output_dir / "summary.md"

    payload = {
        "day": day,
        "window_start_utc": start_ts,
        "window_end_utc": end_ts,
        "totals": {
            "events": total,
            "delivered": delivered,
            "undelivered": undelivered,
        },
        "by_source": dict(by_source),
        "by_source_delivered": dict(by_source_delivered),
        "by_event_type": dict(by_event_type),
        "rss_top_channels": [
            {
                "channel_id": cid,
                "channel_name": rss_channel_names.get(cid, ""),
                "event_count": count,
            }
            for cid, count in rss_channels.most_common(50)
        ],
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    lines: list[str] = []
    lines.append(f"# CSI Daily Summary ({day} UTC)")
    lines.append("")
    lines.append("## Totals")
    lines.append("")
    lines.append(f"- Events: {total}")
    lines.append(f"- Delivered: {delivered}")
    lines.append(f"- Undelivered: {undelivered}")
    lines.append("")
    lines.append("## By Source")
    lines.append("")
    if by_source:
        for source, count in sorted(by_source.items()):
            src_delivered = by_source_delivered.get(source, 0)
            lines.append(f"- {source}: total={count}, delivered={src_delivered}")
    else:
        lines.append("- No events in this window.")
    lines.append("")
    lines.append("## By Event Type")
    lines.append("")
    if by_event_type:
        for event_type, count in sorted(by_event_type.items()):
            lines.append(f"- {event_type}: {count}")
    else:
        lines.append("- No events in this window.")
    lines.append("")
    lines.append("## RSS Top Channels")
    lines.append("")
    if rss_channels:
        for cid, count in rss_channels.most_common(20):
            cname = rss_channel_names.get(cid, "")
            if cname:
                lines.append(f"- {cname} ({cid}): {count}")
            else:
                lines.append(f"- {cid}: {count}")
    else:
        lines.append("- No RSS events in this window.")
    lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"DAILY_SUMMARY_DAY={day}")
    print(f"DAILY_SUMMARY_JSON={json_path}")
    print(f"DAILY_SUMMARY_MD={md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
