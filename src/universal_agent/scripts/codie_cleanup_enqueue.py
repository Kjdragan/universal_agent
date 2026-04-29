"""Deterministically enqueue the CODIE proactive cleanup Task Hub item."""

from __future__ import annotations

import argparse
import json
import sqlite3
from typing import Any

from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
from universal_agent.services.proactive_codie import queue_cleanup_task


def enqueue_codie_cleanup(
    *,
    db_path: str | None = None,
    theme: str = "",
    note: str = "",
    priority: int = 2,
    nudge: bool = True,
) -> dict[str, Any]:
    """Queue one CODIE cleanup task and wake dispatch when available."""
    conn = connect_runtime_db(db_path or get_activity_db_path())
    try:
        result = queue_cleanup_task(
            conn,
            theme=theme,
            note=note,
            priority=priority,
        )
        conn.commit()
    finally:
        conn.close()

    nudge_result = "skipped"
    if nudge:
        try:
            from universal_agent.services.idle_dispatch_loop import nudge_dispatch

            task_id = str((result.get("task") or {}).get("task_id") or "codie_cleanup")
            nudge_dispatch(reason=f"codie_cleanup_enqueued:{task_id}")
            nudge_result = "requested"
        except Exception as exc:
            nudge_result = f"failed:{type(exc).__name__}:{exc}"

    return {
        "ok": True,
        "task": result.get("task"),
        "artifact": result.get("artifact"),
        "dispatch_nudge": nudge_result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default="", help="Override activity DB path.")
    parser.add_argument("--theme", default="", help="Optional cleanup theme override.")
    parser.add_argument("--note", default="", help="Optional operator note.")
    parser.add_argument("--priority", type=int, default=2, help="Task Hub priority 1-4.")
    parser.add_argument("--no-nudge", action="store_true", help="Do not wake idle dispatch.")
    args = parser.parse_args(argv)

    try:
        payload = enqueue_codie_cleanup(
            db_path=args.db_path or None,
            theme=args.theme,
            note=args.note,
            priority=args.priority,
            nudge=not args.no_nudge,
        )
    except sqlite3.Error as exc:
        payload = {"ok": False, "error": f"sqlite:{exc}"}
        print(json.dumps(payload, indent=2, ensure_ascii=True))
        return 1
    except Exception as exc:
        payload = {"ok": False, "error": f"{type(exc).__name__}:{exc}"}
        print(json.dumps(payload, indent=2, ensure_ascii=True))
        return 1

    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
