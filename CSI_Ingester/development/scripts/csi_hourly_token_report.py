#!/usr/bin/env python3
"""Emit hourly CSI token usage summary events to UA."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from csi_ingester.config import load_config
from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.emitter.ua_client import UAEmitter
from csi_ingester.store import dlq as dlq_store
from csi_ingester.store import events as event_store
from csi_ingester.store.sqlite import connect, ensure_schema


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _window_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = now or datetime.now(timezone.utc)
    end = current.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=1)
    return start, end


def _aggregate(conn: sqlite3.Connection, start_db: str, end_db: str) -> dict[str, Any]:
    totals_row = conn.execute(
        """
        SELECT
            COUNT(*) AS records,
            COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
            COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
            COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM token_usage
        WHERE occurred_at >= ? AND occurred_at < ?
        """,
        (start_db, end_db),
    ).fetchone()

    by_process: list[dict[str, Any]] = []
    for row in conn.execute(
        """
        SELECT
            process_name,
            COUNT(*) AS records,
            COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
            COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
            COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM token_usage
        WHERE occurred_at >= ? AND occurred_at < ?
        GROUP BY process_name
        ORDER BY total_tokens DESC, process_name ASC
        """,
        (start_db, end_db),
    ).fetchall():
        by_process.append(
            {
                "process_name": str(row["process_name"] or "unknown"),
                "records": int(row["records"] or 0),
                "prompt_tokens": int(row["prompt_tokens"] or 0),
                "completion_tokens": int(row["completion_tokens"] or 0),
                "total_tokens": int(row["total_tokens"] or 0),
            }
        )

    by_model: list[dict[str, Any]] = []
    for row in conn.execute(
        """
        SELECT
            COALESCE(NULLIF(model_name, ''), 'unknown') AS model_name,
            COUNT(*) AS records,
            COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
            COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
            COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM token_usage
        WHERE occurred_at >= ? AND occurred_at < ?
        GROUP BY COALESCE(NULLIF(model_name, ''), 'unknown')
        ORDER BY total_tokens DESC, model_name ASC
        """,
        (start_db, end_db),
    ).fetchall():
        by_model.append(
            {
                "model_name": str(row["model_name"] or "unknown"),
                "records": int(row["records"] or 0),
                "prompt_tokens": int(row["prompt_tokens"] or 0),
                "completion_tokens": int(row["completion_tokens"] or 0),
                "total_tokens": int(row["total_tokens"] or 0),
            }
        )

    return {
        "records": int(totals_row["records"] or 0),
        "prompt_tokens": int(totals_row["prompt_tokens"] or 0),
        "completion_tokens": int(totals_row["completion_tokens"] or 0),
        "total_tokens": int(totals_row["total_tokens"] or 0),
        "by_process": by_process,
        "by_model": by_model,
    }


async def _emit_report(conn: sqlite3.Connection, report_event: CreatorSignalEvent) -> tuple[bool, int, dict[str, Any]]:
    config = load_config()
    if not config.ua_endpoint or not config.ua_shared_secret:
        return False, 503, {"error": "ua_delivery_not_configured"}
    emitter = UAEmitter(
        endpoint=config.ua_endpoint,
        shared_secret=config.ua_shared_secret,
        instance_id=config.instance_id,
    )
    return await emitter.emit_with_retries([report_event], max_attempts=3)


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if not item or item.startswith("#") or "=" not in item:
            continue
        key, raw = item.split("=", 1)
        val = raw.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        values[key.strip()] = val
    return values


def _apply_env_defaults(path: Path) -> None:
    for key, val in _load_env_file(path).items():
        os.environ.setdefault(key, val)


def main() -> int:
    parser = argparse.ArgumentParser(description="Send hourly CSI token usage report event to UA.")
    parser.add_argument(
        "--db-path",
        default="/opt/universal_agent/CSI_Ingester/development/var/csi.db",
        help="Path to CSI sqlite db",
    )
    parser.add_argument(
        "--state-path",
        default="/opt/universal_agent/CSI_Ingester/development/var/token_usage_report_state.json",
        help="Path to report state cursor file",
    )
    parser.add_argument(
        "--csi-env-file",
        default="/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env",
        help="CSI env file to seed process env defaults for manual runs",
    )
    parser.add_argument(
        "--env-file",
        default="/opt/universal_agent/.env",
        help="Root env file to seed process env defaults for manual runs",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Emit report even if the same hour key was already sent",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path).expanduser()
    state_path = Path(args.state_path).expanduser()
    _apply_env_defaults(Path(args.csi_env_file).expanduser())
    _apply_env_defaults(Path(args.env_file).expanduser())

    conn = connect(db_path)
    ensure_schema(conn)

    start, end = _window_bounds()
    hour_key = start.strftime("%Y-%m-%dT%H:00:00Z")
    state = _load_state(state_path)
    if not args.force and state.get("last_hour_key") == hour_key:
        print(f"TOKEN_REPORT_SKIPPED already_sent_hour={hour_key}")
        conn.close()
        return 0

    start_db = start.strftime("%Y-%m-%d %H:%M:%S")
    end_db = end.strftime("%Y-%m-%d %H:%M:%S")
    aggregate = _aggregate(conn, start_db, end_db)

    config = load_config()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    event = CreatorSignalEvent(
        event_id=f"csi:token_report:{config.instance_id}:{start.strftime('%Y%m%d%H')}",
        dedupe_key=f"csi:token_report:{config.instance_id}:{start.strftime('%Y%m%d%H')}",
        source="csi_analytics",
        event_type="hourly_token_usage_report",
        occurred_at=end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        received_at=now_iso,
        subject={
            "report_type": "token_usage_hourly",
            "window_start_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "window_end_utc": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "totals": {
                "records": aggregate["records"],
                "prompt_tokens": aggregate["prompt_tokens"],
                "completion_tokens": aggregate["completion_tokens"],
                "total_tokens": aggregate["total_tokens"],
            },
            "by_process": aggregate["by_process"],
            "by_model": aggregate["by_model"],
        },
        routing={"pipeline": "csi_token_telemetry", "priority": "standard", "tags": ["csi", "token_usage", "hourly"]},
        metadata={"source_adapter": "csi_hourly_token_report_v1"},
    )

    event_store.insert_event(conn, event)
    import asyncio

    delivered, status_code, payload = asyncio.run(_emit_report(conn, event))
    if delivered:
        event_store.mark_delivered(conn, event.event_id)
        _save_state(state_path, {"last_hour_key": hour_key, "last_sent_at": now_iso, "last_status_code": status_code})
        print(f"TOKEN_REPORT_SENT hour={hour_key} status={status_code} total_tokens={aggregate['total_tokens']}")
        conn.close()
        return 0

    dlq_store.enqueue(
        conn,
        event_id=event.event_id,
        event=event.model_dump(),
        error_reason=f"ua_status_{status_code}",
        retry_count=3,
    )
    print(f"TOKEN_REPORT_SEND_FAILED hour={hour_key} status={status_code} payload={payload}")
    conn.close()
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
