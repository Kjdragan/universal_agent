#!/usr/bin/env python3
"""Validate CSI data-plane health and optional live smoke flow to UA."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
DEV_ROOT = Path(__file__).resolve().parents[1]
if str(DEV_ROOT) not in sys.path:
    sys.path.insert(0, str(DEV_ROOT))

from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.emitter.ua_client import UAEmitter


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _recent_source_metrics(conn: sqlite3.Connection, *, source: str, lookback_hours: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN delivered = 1 THEN 1 ELSE 0 END) AS delivered_total,
            SUM(CASE WHEN delivered = 0 THEN 1 ELSE 0 END) AS undelivered_total,
            MAX(created_at) AS last_event_at
        FROM events
        WHERE source = ?
          AND created_at >= datetime('now', ?)
        """,
        (source, f"-{max(1, lookback_hours)} hours"),
    ).fetchone()
    attempts = conn.execute(
        """
        SELECT
            COUNT(*) AS attempts,
            SUM(CASE WHEN da.delivered = 1 THEN 1 ELSE 0 END) AS delivered_attempts,
            SUM(CASE WHEN da.delivered = 0 THEN 1 ELSE 0 END) AS failed_attempts
        FROM delivery_attempts da
        JOIN events e ON e.event_id = da.event_id
        WHERE e.source = ?
          AND da.attempted_at >= datetime('now', ?)
        """,
        (source, f"-{max(1, lookback_hours)} hours"),
    ).fetchone()
    return {
        "source": source,
        "events_recent": int((row["total"] if row is not None else 0) or 0),
        "delivered_recent": int((row["delivered_total"] if row is not None else 0) or 0),
        "undelivered_recent": int((row["undelivered_total"] if row is not None else 0) or 0),
        "last_event_at": str(row["last_event_at"] or "") if row is not None else "",
        "delivery_attempts_recent": int((attempts["attempts"] if attempts is not None else 0) or 0),
        "delivery_attempts_success": int((attempts["delivered_attempts"] if attempts is not None else 0) or 0),
        "delivery_attempts_failed": int((attempts["failed_attempts"] if attempts is not None else 0) or 0),
    }


def _build_analytics_smoke_event(*, event_type: str, instance_id: str) -> CreatorSignalEvent:
    now_iso = _utc_now_iso()
    suffix = uuid.uuid4().hex[:10]
    report_key = f"smoke:{event_type}:{suffix}"
    return CreatorSignalEvent(
        event_id=f"csi:smoke:{event_type}:{suffix}",
        dedupe_key=f"csi:smoke:{event_type}:{suffix}",
        source="csi_analytics",
        event_type=event_type,
        occurred_at=now_iso,
        received_at=now_iso,
        subject={
            "report_key": report_key,
            "report_type": event_type,
            "generated_at_utc": now_iso,
            "window_hours": 1,
            "markdown": f"# Smoke {event_type}\n\nGenerated at {now_iso}",
            "artifact_paths": {
                "markdown": f"/opt/universal_agent/artifacts/csi-reports/smoke/{report_key}.md",
                "json": f"/opt/universal_agent/artifacts/csi-reports/smoke/{report_key}.json",
            },
        },
        routing={"pipeline": "csi_smoke_validation", "priority": "standard", "tags": ["csi", "smoke", event_type]},
        metadata={"source_adapter": "csi_validate_live_flow", "instance_id": instance_id},
    )


def _ua_activity_contains_event(runtime_conn: sqlite3.Connection, *, event_id: str, lookback_minutes: int) -> bool:
    rows = runtime_conn.execute(
        """
        SELECT id, metadata_json, full_message
        FROM activity_events
        WHERE source_domain = 'csi'
          AND created_at >= datetime('now', ?)
        ORDER BY created_at DESC
        LIMIT 500
        """,
        (f"-{max(1, lookback_minutes)} minutes",),
    ).fetchall()
    marker = str(event_id or "").strip()
    if not marker:
        return False
    for row in rows:
        metadata_text = str(row["metadata_json"] or "")
        full_message = str(row["full_message"] or "")
        if marker and (marker in metadata_text or marker in full_message):
            return True
    return False


async def _emit_smoke_events(
    *,
    endpoint: str,
    secret: str,
    instance_id: str,
    runtime_db_path: Path,
    verify_minutes: int,
    settle_seconds: int,
) -> tuple[bool, list[dict[str, Any]]]:
    emitter = UAEmitter(endpoint=endpoint, shared_secret=secret, instance_id=instance_id)
    smoke_types = ["rss_trend_report", "reddit_trend_report"]
    results: list[dict[str, Any]] = []
    runtime_conn = _connect(runtime_db_path)
    try:
        for event_type in smoke_types:
            event = _build_analytics_smoke_event(event_type=event_type, instance_id=instance_id)
            delivered, status_code, payload = await emitter.emit_with_retries([event], max_attempts=3)
            accepted = int(payload.get("accepted") or 0) if isinstance(payload, dict) else 0
            internal_dispatches = int(payload.get("internal_dispatches") or 0) if isinstance(payload, dict) else 0
            seen_in_ua = False
            if delivered:
                timeout_at = time.time() + max(1, settle_seconds)
                while time.time() <= timeout_at:
                    if _ua_activity_contains_event(
                        runtime_conn,
                        event_id=event.event_id,
                        lookback_minutes=max(5, verify_minutes),
                    ):
                        seen_in_ua = True
                        break
                    await asyncio.sleep(1.0)
            results.append(
                {
                    "event_type": event_type,
                    "event_id": event.event_id,
                    "delivered": bool(delivered),
                    "status_code": int(status_code or 0),
                    "accepted": accepted,
                    "internal_dispatches": internal_dispatches,
                    "seen_in_ua_activity": seen_in_ua,
                    "payload": payload if isinstance(payload, dict) else {"payload": payload},
                }
            )
    finally:
        runtime_conn.close()
    ok = all(
        bool(item["delivered"]) and int(item["accepted"]) >= 1 and bool(item["seen_in_ua_activity"])
        for item in results
    )
    return ok, results


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate CSI RSS/Reddit data-plane and optional live smoke flow.")
    parser.add_argument("--csi-db", default="/opt/universal_agent/CSI_Ingester/development/var/csi.db")
    parser.add_argument("--ua-runtime-db", default=os.getenv("UA_RUNTIME_DB_PATH", "/opt/universal_agent/runtime_state.db"))
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--min-rss-events", type=int, default=1)
    parser.add_argument("--min-reddit-events", type=int, default=1)
    parser.add_argument("--emit-smoke", action="store_true")
    parser.add_argument("--smoke-settle-seconds", type=int, default=20)
    parser.add_argument("--verify-minutes", type=int, default=30)
    parser.add_argument("--endpoint", default=os.getenv("CSI_UA_ENDPOINT", ""))
    parser.add_argument("--secret", default=os.getenv("CSI_UA_SHARED_SECRET", ""))
    parser.add_argument("--instance-id", default=os.getenv("CSI_INSTANCE_ID", "csi-live-validation"))
    parser.add_argument("--allow-empty-ingest", action="store_true")
    args = parser.parse_args()

    csi_db_path = Path(args.csi_db).expanduser()
    runtime_db_path = Path(args.ua_runtime_db).expanduser()
    if not csi_db_path.exists():
        print(f"LIVE_FLOW_FAIL csi_db_missing path={csi_db_path}")
        return 2
    if args.emit_smoke and not runtime_db_path.exists():
        print(f"LIVE_FLOW_FAIL ua_runtime_db_missing path={runtime_db_path}")
        return 2

    conn = _connect(csi_db_path)
    try:
        rss_metrics = _recent_source_metrics(conn, source="youtube_channel_rss", lookback_hours=max(1, args.lookback_hours))
        reddit_metrics = _recent_source_metrics(conn, source="reddit_discovery", lookback_hours=max(1, args.lookback_hours))
    finally:
        conn.close()

    summary = {
        "checked_at_utc": _utc_now_iso(),
        "lookback_hours": max(1, args.lookback_hours),
        "rss": rss_metrics,
        "reddit": reddit_metrics,
        "smoke": {"enabled": bool(args.emit_smoke), "results": []},
    }
    print("LIVE_FLOW_SUMMARY", json.dumps(summary, ensure_ascii=False, separators=(",", ":")))

    failures: list[str] = []
    if not args.allow_empty_ingest:
        if int(rss_metrics["events_recent"]) < max(0, int(args.min_rss_events)):
            failures.append(
                f"rss_events_recent_below_min({int(rss_metrics['events_recent'])}<{int(args.min_rss_events)})"
            )
        if int(reddit_metrics["events_recent"]) < max(0, int(args.min_reddit_events)):
            failures.append(
                f"reddit_events_recent_below_min({int(reddit_metrics['events_recent'])}<{int(args.min_reddit_events)})"
            )

    if args.emit_smoke:
        endpoint = str(args.endpoint or "").strip()
        secret = str(args.secret or "").strip()
        if not endpoint or not secret:
            failures.append("smoke_missing_endpoint_or_secret")
        else:
            ok, smoke_results = asyncio.run(
                _emit_smoke_events(
                    endpoint=endpoint,
                    secret=secret,
                    instance_id=str(args.instance_id or "csi-live-validation").strip(),
                    runtime_db_path=runtime_db_path,
                    verify_minutes=max(5, int(args.verify_minutes)),
                    settle_seconds=max(5, int(args.smoke_settle_seconds)),
                )
            )
            summary["smoke"]["results"] = smoke_results
            print("LIVE_FLOW_SMOKE", json.dumps(smoke_results, ensure_ascii=False, separators=(",", ":")))
            if not ok:
                failures.append("smoke_flow_failed")

    if failures:
        print("LIVE_FLOW_FAIL", ",".join(failures))
        return 1
    print("LIVE_FLOW_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

