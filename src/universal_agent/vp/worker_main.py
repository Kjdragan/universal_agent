from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess

from universal_agent.durable.db import connect_runtime_db, get_vp_db_path
from universal_agent.durable.migrations import ensure_schema
from universal_agent.feature_flags import vp_enabled_ids
from universal_agent.vp.worker_loop import VpWorkerLoop

logger = logging.getLogger(__name__)


def _configure_git_for_push() -> None:
    """Configure git credentials so VP worker subprocesses can push.

    Uses GITHUB_TOKEN from Infisical (loaded by initialize_runtime_secrets).
    Falls back gracefully — if no token is available, git push will still
    work if the repo remote URL has an embedded token.
    """
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        logger.info("GITHUB_TOKEN not set — git push will rely on repo remote URL credentials")
        return

    try:
        # Set git identity for commits made by the VP worker
        subprocess.run(
            ["git", "config", "--global", "user.name", "VP Coder Agent"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "--global", "user.email", "vp-coder@universal-agent.local"],
            check=True, capture_output=True,
        )
        # Configure credential helper to supply the token for any https push
        # This script echoes the token as the password when git asks for credentials
        helper_script = (
            f'!f() {{ echo "username=x-access-token"; '
            f'echo "password={token}"; }}; f'
        )
        subprocess.run(
            ["git", "config", "--global", "credential.helper", helper_script],
            check=True, capture_output=True,
        )
        logger.info("Git credential helper configured for VP worker push")
    except Exception as exc:
        logger.warning("Failed to configure git credentials: %s", exc)


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

    # Bootstrap runtime secrets from Infisical so API keys (COMPOSIO_API_KEY,
    # ANTHROPIC_API_KEY, etc.) are available before any agent code runs.
    try:
        from universal_agent.infisical_loader import initialize_runtime_secrets
        initialize_runtime_secrets()
        logger.info("Infisical runtime secrets loaded for VP worker")
    except Exception as exc:
        logger.warning("Infisical secret bootstrap skipped: %s", exc)

    # Configure git credentials so agent subprocesses can push branches
    _configure_git_for_push()

    if args.vp_id not in set(vp_enabled_ids(default=("vp.coder.primary", "vp.general.primary"))):
        raise SystemExit(f"vp_id '{args.vp_id}' is not enabled by UA_VP_ENABLED_IDS")

    db_path = args.db_path.strip() or get_vp_db_path()
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
