#!/usr/bin/env python3
"""Create baseline CSI analysis tasks so the task runner always has work."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from csi_ingester.store import analysis_tasks as analysis_task_store
from csi_ingester.store.sqlite import connect, ensure_schema


@dataclass(frozen=True)
class TaskSpec:
    request_type: str
    cadence_minutes: int
    priority: int


DEFAULT_SPECS: tuple[TaskSpec, ...] = (
    TaskSpec("trend_followup", 60, 75),
    TaskSpec("category_deep_dive", 180, 65),
    TaskSpec("channel_deep_dive", 180, 65),
)


def _load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if not item or item.startswith("#") or "=" not in item:
            continue
        key, raw = item.split("=", 1)
        val = raw.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        out[key.strip()] = val
    return out


def _apply_env_defaults(path: Path) -> None:
    for key, val in _load_env_file(path).items():
        os.environ.setdefault(key, val)


def _resolve_setting(keys: list[str], env_file_values: dict[str, str]) -> str:
    for key in keys:
        env_val = os.getenv(key, "").strip()
        if env_val:
            return env_val
        file_val = env_file_values.get(key, "").strip()
        if file_val:
            return file_val
    return ""


def _has_recent_task(conn: sqlite3.Connection, *, request_type: str, cadence_minutes: int) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM analysis_tasks
        WHERE request_type = ?
          AND status IN ('pending', 'running', 'completed')
          AND created_at >= datetime('now', ?)
        """,
        (request_type, f"-{max(1, int(cadence_minutes))} minutes"),
    ).fetchone()
    return bool(int(row["n"] or 0) > 0)


def _top_category(conn: sqlite3.Connection, *, lookback_hours: int) -> str:
    row = conn.execute(
        """
        SELECT category, COUNT(*) AS n
        FROM rss_event_analysis
        WHERE analyzed_at >= datetime('now', ?)
        GROUP BY category
        ORDER BY n DESC
        LIMIT 1
        """,
        (f"-{max(1, int(lookback_hours))} hours",),
    ).fetchone()
    if not row:
        return "ai"
    return str(row["category"] or "ai").strip() or "ai"


def _top_channel(conn: sqlite3.Connection, *, lookback_hours: int) -> dict[str, str]:
    row = conn.execute(
        """
        SELECT channel_id, channel_name, COUNT(*) AS n
        FROM rss_event_analysis
        WHERE analyzed_at >= datetime('now', ?)
          AND (channel_id IS NOT NULL OR channel_name IS NOT NULL)
        GROUP BY channel_id, channel_name
        ORDER BY n DESC
        LIMIT 1
        """,
        (f"-{max(1, int(lookback_hours))} hours",),
    ).fetchone()
    if not row:
        return {}
    out: dict[str, str] = {}
    channel_id = str(row["channel_id"] or "").strip()
    channel_name = str(row["channel_name"] or "").strip()
    if channel_id:
        out["channel_id"] = channel_id
    if channel_name:
        out["channel_name"] = channel_name
    return out


def _build_payload(conn: sqlite3.Connection, *, request_type: str, lookback_hours: int, limit: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "lookback_hours": int(max(1, lookback_hours)),
        "limit": int(max(20, min(limit, 500))),
        "reason": "scheduled_bootstrap",
    }
    if request_type == "trend_followup":
        payload["category"] = _top_category(conn, lookback_hours=lookback_hours)
    elif request_type == "category_deep_dive":
        payload["category"] = _top_category(conn, lookback_hours=lookback_hours)
    elif request_type == "channel_deep_dive":
        payload.update(_top_channel(conn, lookback_hours=lookback_hours))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap CSI analysis task queue with recurring baseline tasks.")
    parser.add_argument("--db-path", default="/opt/universal_agent/CSI_Ingester/development/var/csi.db")
    parser.add_argument("--lookback-hours", type=int, default=48)
    parser.add_argument("--limit", type=int, default=180)
    parser.add_argument("--request-types", default="")
    parser.add_argument("--max-pending-per-type", type=int, default=1)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--env-file", default="/opt/universal_agent/.env")
    parser.add_argument(
        "--csi-env-file",
        default="/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env",
    )
    args = parser.parse_args()

    _apply_env_defaults(Path(args.csi_env_file).expanduser())
    _apply_env_defaults(Path(args.env_file).expanduser())
    env_file_values = _load_env_file(Path(args.env_file).expanduser())
    env_file_values.update(_load_env_file(Path(args.csi_env_file).expanduser()))

    configured_types = [item.strip() for item in args.request_types.split(",") if item.strip()]
    if not configured_types:
        configured_types_env = _resolve_setting(["CSI_ANALYSIS_TASK_BOOTSTRAP_REQUEST_TYPES"], env_file_values)
        configured_types = [item.strip() for item in configured_types_env.split(",") if item.strip()]
    if not configured_types:
        configured_types = [spec.request_type for spec in DEFAULT_SPECS]

    spec_by_type = {spec.request_type: spec for spec in DEFAULT_SPECS}
    selected_specs: list[TaskSpec] = []
    for request_type in configured_types:
        if request_type in spec_by_type:
            selected_specs.append(spec_by_type[request_type])

    if not selected_specs:
        print("CSI_TASK_BOOTSTRAP_SKIPPED=1")
        print("CSI_TASK_BOOTSTRAP_REASON=no_supported_request_types")
        return 0

    conn = connect(Path(args.db_path).expanduser())
    ensure_schema(conn)

    created = 0
    skipped = 0
    for spec in selected_specs:
        pending_row = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM analysis_tasks
            WHERE request_type = ? AND status IN ('pending', 'running')
            """,
            (spec.request_type,),
        ).fetchone()
        pending_n = int(pending_row["n"] or 0)
        if not args.force and pending_n >= max(0, int(args.max_pending_per_type)):
            skipped += 1
            continue

        if not args.force and _has_recent_task(
            conn,
            request_type=spec.request_type,
            cadence_minutes=spec.cadence_minutes,
        ):
            skipped += 1
            continue

        payload = _build_payload(
            conn,
            request_type=spec.request_type,
            lookback_hours=max(1, int(args.lookback_hours)),
            limit=max(20, int(args.limit)),
        )
        analysis_task_store.create_task(
            conn,
            request_type=spec.request_type,
            payload=payload,
            priority=spec.priority,
            request_source="csi_scheduler",
        )
        created += 1

    conn.close()
    print(f"CSI_TASK_BOOTSTRAP_CREATED={created}")
    print(f"CSI_TASK_BOOTSTRAP_SKIPPED={skipped}")
    print(f"CSI_TASK_BOOTSTRAP_TYPES={','.join(spec.request_type for spec in selected_specs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
