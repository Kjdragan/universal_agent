#!/usr/bin/env python3
import argparse
import os
import sqlite3
import time


def _resolve_db_path(cli_path: str | None) -> str:
    if cli_path:
        return cli_path
    env_path = os.getenv("UA_RUNTIME_DB_PATH")
    if env_path:
        return env_path
    try:
        from universal_agent.durable.db import get_runtime_db_path

        return get_runtime_db_path()
    except Exception:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(repo_root, "AGENT_RUN_WORKSPACES", "runtime_state.db")


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _print_row(row: sqlite3.Row) -> None:
    raw_name = row["raw_tool_name"] or ""
    if not raw_name:
        raw_name = row["tool_name"]
    print(
        f"[{row['updated_at']}] status={row['status']} raw_tool_name={raw_name} "
        f"tool_name={row['tool_name']} tool_namespace={row['tool_namespace']} "
        f"tool_call_id={row['tool_call_id']}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tail tool_calls ledger for raw_tool_name/tool_call_id."
    )
    parser.add_argument("--db-path", help="Path to runtime_state.db")
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--include-updates",
        action="store_true",
        help="Emit rows again when updated_at changes (status updates).",
    )
    args = parser.parse_args()

    db_path = _resolve_db_path(args.db_path)
    if not os.path.exists(db_path):
        print(f"Runtime DB not found: {db_path}")
        return 1

    conn = _connect(db_path)
    last_rowid = 0
    last_updated_at = ""
    try:
        while True:
            if args.include_updates:
                rows = conn.execute(
                    """
                    SELECT rowid, tool_call_id, raw_tool_name, tool_name, tool_namespace,
                           status, updated_at
                    FROM tool_calls
                    WHERE updated_at > ?
                    ORDER BY updated_at ASC
                    """,
                    (last_updated_at,),
                ).fetchall()
                for row in rows:
                    _print_row(row)
                    last_updated_at = row["updated_at"]
                    last_rowid = max(last_rowid, int(row["rowid"]))
            else:
                rows = conn.execute(
                    """
                    SELECT rowid, tool_call_id, raw_tool_name, tool_name, tool_namespace,
                           status, updated_at
                    FROM tool_calls
                    WHERE rowid > ?
                    ORDER BY rowid ASC
                    """,
                    (last_rowid,),
                ).fetchall()
                for row in rows:
                    _print_row(row)
                    last_rowid = int(row["rowid"])
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
