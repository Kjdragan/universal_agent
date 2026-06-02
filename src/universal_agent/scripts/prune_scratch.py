"""Prune stale tailnet-scratchpad artifacts.

The scratchpad (`/home/ua/ua_scratch/<slug>/`) is a **delivery surface, not a
system of record**. The durable copy of any report lives elsewhere (e.g. the
YouTube digest markdown under `AGENT_RUN_WORKSPACES/daily_digests/`); the scratch
HTML is just the rendered copy we email as a link. Once a report is old enough
that nobody will click the email link, the slug-dir is pure clutter — and left
unbounded the scratch root would accumulate thousands of dead dirs over a year.

This sweep deletes scratch slug-dirs whose newest content is older than
``UA_SCRATCH_RETENTION_DAYS`` (default 30) — long enough to revisit a recent
email, short enough to keep the root tidy. Anything older can be regenerated from
the durable source if ever needed.

Pure-filesystem GC: no subprocess, no network. It runs on the VPS where the
scratch dir is local (registered as a daily system cron — see
``gateway_server._ensure_scratch_pruning_cron_job``).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import sys
import time

logger = logging.getLogger(__name__)

DEFAULT_ROOT = "/home/ua/ua_scratch"
DEFAULT_RETENTION_DAYS = 30


def _retention_days() -> int:
    raw = (os.getenv("UA_SCRATCH_RETENTION_DAYS") or "").strip()
    try:
        days = int(raw) if raw else DEFAULT_RETENTION_DAYS
    except ValueError:
        days = DEFAULT_RETENTION_DAYS
    return max(1, days)


def _scratch_root() -> Path:
    return Path(os.getenv("UA_SCRATCH_ROOT") or DEFAULT_ROOT)


def prune_scratch(
    root: Path | None = None,
    retention_days: int | None = None,
    *,
    now: float | None = None,
) -> dict:
    """Remove scratch slug-dirs whose newest content is older than the retention.

    Returns a summary dict: ``{root, retention_days, removed, kept, errors}``.
    Best-effort: per-dir failures are counted and logged, never raised.
    """
    root = Path(root) if root is not None else _scratch_root()
    days = retention_days if retention_days is not None else _retention_days()
    now = now if now is not None else time.time()
    result = {"root": str(root), "retention_days": days, "removed": 0, "kept": 0, "errors": 0}

    if not root.is_dir():
        logger.info("scratch prune: root %s does not exist; nothing to do", root)
        return result

    cutoff = now - days * 86400
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            # Newest mtime among the dir and its files: a slug-dir whose HTML was
            # (re)published recently counts as fresh even if the dir node is older.
            mtimes = [child.stat().st_mtime]
            for f in child.iterdir():
                try:
                    mtimes.append(f.stat().st_mtime)
                except OSError:
                    pass
            newest = max(mtimes)
        except OSError:
            result["errors"] += 1
            logger.warning("scratch prune: could not stat %s; skipping", child)
            continue

        if newest < cutoff:
            try:
                shutil.rmtree(child)
                result["removed"] += 1
                logger.info(
                    "scratch prune: removed %s (age %.1fd)",
                    child.name, (now - newest) / 86400,
                )
            except OSError as exc:
                result["errors"] += 1
                logger.warning("scratch prune: failed to remove %s: %s", child, exc)
        else:
            result["kept"] += 1

    logger.info(
        "scratch prune done: removed=%d kept=%d errors=%d (root=%s, retention=%dd)",
        result["removed"], result["kept"], result["errors"], root, days,
    )
    return result


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = prune_scratch()
    # Non-zero only on real removal errors, so a clean sweep is a green cron tick
    # and a permissions/IO problem surfaces in /dashboard/cron-jobs.
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
