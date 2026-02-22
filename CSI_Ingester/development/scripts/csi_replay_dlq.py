#!/usr/bin/env python3
"""Replay CSI dead-letter queue entries."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from csi_ingester.config import load_config
from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.emitter.ua_client import UAEmitter
from csi_ingester.store import dlq as dlq_store
from csi_ingester.store import events as event_store
from csi_ingester.store.sqlite import connect, ensure_schema


async def _run(args: argparse.Namespace) -> int:
    config = load_config(args.config_path)
    db_path = Path(args.db_path) if args.db_path else config.db_path
    conn = connect(db_path)
    ensure_schema(conn)
    rows = dlq_store.list_entries(conn, event_id=args.event_id or "", limit=args.limit)
    if not rows:
        print("DLQ empty for given filter.")
        return 0

    endpoint = config.ua_endpoint
    secret = config.ua_shared_secret
    emitter = None
    if endpoint and secret:
        emitter = UAEmitter(endpoint=endpoint, shared_secret=secret, instance_id=config.instance_id)

    ok_count = 0
    fail_count = 0
    for row in rows:
        row_id = int(row["id"])
        event_id = str(row["event_id"])
        try:
            payload = json.loads(str(row["event_json"]))
            event = CreatorSignalEvent.model_validate(payload)
        except Exception as exc:
            fail_count += 1
            print(f"SKIP id={row_id} event_id={event_id} reason=invalid_payload error={exc}")
            continue

        if args.dry_run or emitter is None:
            print(f"DRYRUN id={row_id} event_id={event_id}")
            ok_count += 1
            continue

        delivered, status_code, body = await emitter.emit_with_retries([event], max_attempts=args.max_attempts)
        if delivered:
            event_store.mark_delivered(conn, event_id)
            dlq_store.delete_entry(conn, row_id)
            ok_count += 1
            print(f"REPLAY_OK id={row_id} event_id={event_id} status={status_code}")
        else:
            fail_count += 1
            print(f"REPLAY_FAIL id={row_id} event_id={event_id} status={status_code} body={body}")

    print(f"SUMMARY ok={ok_count} fail={fail_count} total={len(rows)}")
    return 0 if fail_count == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay CSI dead-letter queue entries.")
    parser.add_argument("--config-path", default="", help="Path to CSI config YAML")
    parser.add_argument("--db-path", default="", help="Path to CSI SQLite DB")
    parser.add_argument("--event-id", default="", help="Replay a single event id")
    parser.add_argument("--limit", type=int, default=100, help="Max entries to replay")
    parser.add_argument("--max-attempts", type=int, default=3, help="Emitter retry attempts")
    parser.add_argument("--dry-run", action="store_true", help="Do not emit; list replay candidates only")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
