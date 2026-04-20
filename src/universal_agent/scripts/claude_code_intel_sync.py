"""Cron/script entry point for the Claude Code X intelligence lane."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3

from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.services.claude_code_intel import ClaudeCodeIntelConfig, run_sync

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Poll @ClaudeDevs via X API and write a Claude Code Intel packet.")
    parser.add_argument("--handle", default="", help="X handle to poll, without @. Defaults to env or ClaudeDevs.")
    parser.add_argument("--max-results", type=int, default=0, help="Maximum posts to fetch, 5-100.")
    parser.add_argument(
        "--no-task-hub",
        action="store_true",
        help="Write packet/artifact only; do not queue Task Hub follow-up tasks.",
    )
    parser.add_argument(
        "--profile",
        default="local_workstation",
        help="Infisical/deployment profile for secret bootstrap.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    initialize_runtime_secrets(profile=args.profile)
    cfg = ClaudeCodeIntelConfig.from_env()
    if args.handle.strip():
        cfg = ClaudeCodeIntelConfig(
            handle=args.handle.strip().lstrip("@"),
            max_results=cfg.max_results,
            queue_task_hub=cfg.queue_task_hub,
            request_timeout_seconds=cfg.request_timeout_seconds,
            artifacts_root=cfg.artifacts_root,
        )
    if args.max_results:
        cfg = ClaudeCodeIntelConfig(
            handle=cfg.handle,
            max_results=args.max_results,
            queue_task_hub=cfg.queue_task_hub,
            request_timeout_seconds=cfg.request_timeout_seconds,
            artifacts_root=cfg.artifacts_root,
        )
    if args.no_task_hub:
        cfg = ClaudeCodeIntelConfig(
            handle=cfg.handle,
            max_results=cfg.max_results,
            queue_task_hub=False,
            request_timeout_seconds=cfg.request_timeout_seconds,
            artifacts_root=cfg.artifacts_root,
        )

    with connect_runtime_db(get_activity_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        result = run_sync(config=cfg, conn=conn)

    payload = {
        "ok": result.ok,
        "generated_at": result.generated_at,
        "handle": result.handle,
        "user_id": result.user_id,
        "packet_dir": result.packet_dir,
        "new_post_count": result.new_post_count,
        "seen_post_count": result.seen_post_count,
        "action_count": result.action_count,
        "queued_task_count": result.queued_task_count,
        "artifact_id": result.artifact_id,
        "error": result.error,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
