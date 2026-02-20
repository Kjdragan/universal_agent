from __future__ import annotations

import argparse
import asyncio
import logging
import os

from universal_agent.durable.db import connect_runtime_db, get_runtime_db_path
from universal_agent.durable.migrations import ensure_schema
from universal_agent.feature_flags import vp_enabled_ids
from universal_agent.vp.worker_loop import VpWorkerLoop

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run external VP worker loop.")
    parser.add_argument("--vp-id", required=True, help="VP profile id (e.g. vp.coder.primary)")
    parser.add_argument("--worker-id", default="", help="Optional worker id override.")
    parser.add_argument("--db-path", default="", help="Runtime sqlite path override.")
    parser.add_argument("--workspace-base", default="", help="Optional workspace base for profile resolution.")
    parser.add_argument("--poll-interval-seconds", type=int, default=0)
    parser.add_argument("--lease-ttl-seconds", type=int, default=0)
    parser.add_argument("--max-concurrent-missions", type=int, default=0)
    return parser.parse_args()


async def _run() -> None:
    args = parse_args()
    if args.vp_id not in set(vp_enabled_ids(default=("vp.coder.primary", "vp.general.primary"))):
        raise SystemExit(f"vp_id '{args.vp_id}' is not enabled by UA_VP_ENABLED_IDS")

    db_path = args.db_path.strip() or get_runtime_db_path()
    conn = connect_runtime_db(db_path)
    ensure_schema(conn)

    loop = VpWorkerLoop(
        conn=conn,
        vp_id=args.vp_id,
        worker_id=args.worker_id.strip() or None,
        workspace_base=args.workspace_base.strip() or None,
        poll_interval_seconds=args.poll_interval_seconds or None,
        lease_ttl_seconds=args.lease_ttl_seconds or None,
        max_concurrent_missions=args.max_concurrent_missions or None,
    )
    try:
        await loop.run_forever()
    finally:
        conn.close()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("UA_VP_WORKER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_run())


if __name__ == "__main__":
    main()
