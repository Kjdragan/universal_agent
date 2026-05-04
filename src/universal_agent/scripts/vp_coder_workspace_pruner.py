"""Weekly pruning of stale VP-coder workspace subdirectories.

The session reaper (`session/reaper.py`) handles main-process
AGENT_RUN_WORKSPACES.  External VP-coder workspaces under
`UA_VP_CODER_WORKSPACE_ROOT` have no scheduled cleanup, so disk
usage creeps up over time (observed: 55 subdirs / 64% disk).

This script archives any VP-coder workspace older than the
retention window into a sibling `_archive` directory.  Defaults to
7 days; override via `UA_VP_CODER_WORKSPACE_RETENTION_HOURS`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from universal_agent.feature_flags import vp_coder_workspace_root
from universal_agent.session.reaper import cleanup_stale_workspaces

logger = logging.getLogger(__name__)


def _retention_hours(default: int = 168) -> int:
    raw = (os.getenv("UA_VP_CODER_WORKSPACE_RETENTION_HOURS") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid UA_VP_CODER_WORKSPACE_RETENTION_HOURS=%r; using default %d", raw, default)
        return default
    return value if value > 0 else default


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    root_str = vp_coder_workspace_root(default="")
    if not root_str:
        logger.info("UA_VP_CODER_WORKSPACE_ROOT unset; nothing to prune.")
        return 0

    root = Path(root_str).expanduser()
    if not root.exists():
        logger.info("VP-coder workspace root does not exist: %s", root)
        return 0

    archive_root = root.parent / f"{root.name}_archive"
    retention = _retention_hours()

    logger.info(
        "Pruning VP-coder workspaces older than %dh (root=%s, archive=%s)",
        retention,
        root,
        archive_root,
    )

    archived = await cleanup_stale_workspaces(
        max_age_hours=retention,
        workspaces_dir=root,
        archive_dir=archive_root,
        dry_run=False,
    )
    logger.info("Archived %d stale VP-coder workspace(s).", len(archived))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
