"""Weekly pruning of stale VP-coder workspace subdirectories.

The session reaper (`session/reaper.py`) handles main-process
AGENT_RUN_WORKSPACES.  External VP-coder workspaces under
`UA_VP_CODER_WORKSPACE_ROOT` have no scheduled cleanup, so disk
usage creeps up over time (observed: 55 subdirs / 64% disk).

This script archives any VP-coder workspace older than the
retention window into a sibling `_archive` directory.  Defaults to
7 days; override via `UA_VP_CODER_WORKSPACE_RETENTION_HOURS`.

WHY WEEKLY IS STILL ACCEPTABLE (2026-06-25):
The daily regenerable-artifact reaper
(`scripts/vp_coder_regenerable_reaper.py`, registered as the
`vp_coder_workspace_regenerable_reap` cron job) owns the high-frequency
disk pressure — it removes ``.venv`` / ``__pycache__`` / ``node_modules`` /
related caches from each mission dir every day, so the 19.6G of
regenerable bloat observed in the 2026-06-25 incident never rebuilds
between weekly runs. This weekly job is the SECOND tier: it owns
WHOLE-DIR archival of fully-completed missions (everything older than
the retention window moves to ``_archive``, then is hard-deleted after
2× retention). The two jobs are complementary:

  * daily regenerable reap  -> keeps the active window's disk bounded;
  * weekly whole-dir prune  -> retires completed missions, bounds the
                               long-term directory count + non-regenerable
                               tail (source, logs, manifests).

Running weekly is acceptable BECAUSE the daily reap holds the line on
the regenerable driver; if the daily reaper is ever disabled
(``UA_VP_CODER_REGENERABLE_REAP_ENABLED=0``), tighten this cadence to
daily or address the regression before disk pressure returns.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import shutil
import sys
import time

from universal_agent.session.reaper import cleanup_stale_workspaces
from universal_agent.vp.profiles import get_vp_profile

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


def _resolve_coder_workspace_root() -> Path | None:
    """Resolve the SAME path the writer uses (vp/profiles.py::resolve_vp_profiles).

    The previous implementation read ``UA_VP_CODER_WORKSPACE_ROOT`` directly with
    no fallback, so when the env var is unset (the production default) the pruner
    no-op'd while the writer kept creating workspaces under
    ``AGENT_RUN_WORKSPACES/vp_coder_primary_external`` — an unbounded leak (H21).
    Resolving via the profile guarantees writer and reaper can never diverge.
    """
    profile = get_vp_profile("vp.coder.primary")
    if profile is None or not getattr(profile, "workspace_root", None):
        return None
    return Path(profile.workspace_root)


def _hard_delete_aged_archive(archive_root: Path, max_age_hours: int) -> int:
    """Delete archived workspace dirs older than ``max_age_hours``.

    ``cleanup_stale_workspaces`` only MOVES stale dirs into ``_archive`` on the
    SAME filesystem, which reclaims zero bytes. This second tier actually frees
    the space, after a longer grace window (default 2× retention) so an archived
    dir is recoverable for a while before deletion.
    """
    if not archive_root.exists():
        return 0
    cutoff = time.time() - max(1, max_age_hours) * 3600
    deleted = 0
    for child in sorted(archive_root.iterdir()):
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                deleted += 1
        except Exception as exc:  # never let one bad dir abort the sweep
            logger.warning("Failed deleting archived workspace %s: %s", child, exc)
    return deleted


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    root = _resolve_coder_workspace_root()
    if root is None:
        logger.info("VP-coder profile unavailable (VP disabled?); nothing to prune.")
        return 0

    root = root.expanduser()
    archive_root = root.parent / f"{root.name}_archive"
    retention = _retention_hours()
    delete_after = retention * 2  # grace before hard-delete from _archive

    logger.info(
        "Pruning VP-coder workspaces older than %dh (root=%s, archive=%s, delete_after=%dh)",
        retention,
        root,
        archive_root,
        delete_after,
    )

    archived: list = []
    if root.exists():
        archived = await cleanup_stale_workspaces(
            max_age_hours=retention,
            workspaces_dir=root,
            archive_dir=archive_root,
            dry_run=False,
        )
        logger.info("Archived %d stale VP-coder workspace(s).", len(archived))
    else:
        logger.info("VP-coder workspace root does not exist yet: %s", root)

    # Reclaim disk: archiving alone (a same-filesystem move) frees nothing.
    deleted = _hard_delete_aged_archive(archive_root, delete_after)
    logger.info("Hard-deleted %d archived workspace(s) older than %dh.", deleted, delete_after)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
