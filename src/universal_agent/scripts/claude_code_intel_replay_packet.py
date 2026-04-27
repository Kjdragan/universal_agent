"""Replay or backfill a Claude Code intelligence packet."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sqlite3

from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.services.claude_code_intel_replay import (
    ClaudeCodeIntelReplayConfig,
    replay_packet,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay/backfill a Claude Code intelligence packet.")
    parser.add_argument("--packet-dir", required=True, help="Absolute or relative path to the packet directory.")
    parser.add_argument("--work-product-dir", default="", help="Optional work-product directory to ingest into the vault.")
    parser.add_argument("--no-task-hub", action="store_true", help="Do not create/reconcile Task Hub items during replay.")
    parser.add_argument("--no-vault", action="store_true", help="Do not write/update the external knowledge vault.")
    parser.add_argument("--profile", default="local_workstation", help="Infisical/deployment profile for secret bootstrap.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    initialize_runtime_secrets(profile=args.profile)
    packet_dir = Path(args.packet_dir).expanduser().resolve()
    work_product_dir = Path(args.work_product_dir).expanduser().resolve() if args.work_product_dir.strip() else None
    cfg = ClaudeCodeIntelReplayConfig(
        packet_dir=packet_dir,
        queue_task_hub=not args.no_task_hub,
        write_vault=not args.no_vault,
        work_product_dir=work_product_dir,
    )
    with connect_runtime_db(get_activity_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        result = replay_packet(config=cfg, conn=conn)
    print(json.dumps(result, indent=2, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
