"""Prune (optionally) tailnet-scratchpad artifacts, and refresh the artifact index.

The scratchpad (`/home/ua/ua_scratch/<slug>/`) is the operator's persistent artifact
store, surfaced by the browsable index. **Retention is unlimited by default** — nothing
is deleted, so the store keeps every artifact. Pruning is opt-in: set
``UA_SCRATCH_RETENTION_DAYS`` to a positive integer and this sweep deletes slug-dirs whose
newest content is older than that many days (a value ``<= 0`` or unset means unlimited).

Either way it then does a best-effort rebuild of the artifact index, so even when nothing
is pruned the daily run keeps the index fresh. It runs on the VPS where the scratch dir
is local (registered as a daily system cron — see
``gateway_server._ensure_scratch_pruning_cron_job``).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

logger = logging.getLogger(__name__)

DEFAULT_ROOT = "/home/ua/ua_scratch"
# 0 = unlimited retention (keep every artifact; never prune). Set a positive
# UA_SCRATCH_RETENTION_DAYS to opt into a deletion window.
DEFAULT_RETENTION_DAYS = 0


def _retention_days() -> int:
    """Retention window in days; ``<= 0`` means unlimited (never prune)."""
    raw = (os.getenv("UA_SCRATCH_RETENTION_DAYS") or "").strip()
    if not raw:
        return DEFAULT_RETENTION_DAYS
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_RETENTION_DAYS


def _scratch_root() -> Path:
    return Path(os.getenv("UA_SCRATCH_ROOT") or DEFAULT_ROOT)


def prune_scratch(
    root: Path | None = None,
    retention_days: int | None = None,
    *,
    now: float | None = None,
) -> dict:
    """Remove scratch slug-dirs whose newest content is older than the retention.

    With ``retention_days <= 0`` (the default) retention is unlimited: nothing is
    deleted and every slug-dir is kept. Returns a summary dict:
    ``{root, retention_days, removed, kept, errors}``. Best-effort: per-dir failures are
    counted and logged, never raised.
    """
    root = Path(root) if root is not None else _scratch_root()
    days = retention_days if retention_days is not None else _retention_days()
    now = now if now is not None else time.time()
    result = {"root": str(root), "retention_days": days, "removed": 0, "kept": 0, "errors": 0}

    if not root.is_dir():
        logger.info("scratch prune: root %s does not exist; nothing to do", root)
        return result

    if days <= 0:
        result["kept"] = sum(1 for child in root.iterdir() if child.is_dir())
        logger.info(
            "scratch prune: unlimited retention (UA_SCRATCH_RETENTION_DAYS<=0) — kept all %d slug-dir(s)",
            result["kept"],
        )
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


def _rebuild_index_best_effort(root: Path) -> None:
    """Regenerate the browsable artifact index after a prune. Never fails the sweep.

    The publish path rebuilds the index on every publish; this keeps it accurate in the
    gap after a prune deletes dirs (otherwise a removed artifact lingers as a dead row).
    Uses the stdlib-only ``scripts/build_scratch_index.py``.
    """
    builder = Path(__file__).resolve().parents[3] / "scripts" / "build_scratch_index.py"
    if not builder.is_file():
        logger.info("scratch prune: index builder %s not found; skipping reindex", builder)
        return
    try:
        subprocess.run(
            [sys.executable, str(builder), "--root", str(root)],
            check=False,
            capture_output=True,
            timeout=60,
        )
    except Exception:  # noqa: BLE001 — reindex is best-effort; never break the prune
        logger.warning("scratch prune: index rebuild failed", exc_info=True)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = prune_scratch()
    _rebuild_index_best_effort(_scratch_root())
    # Non-zero only on real removal errors, so a clean sweep is a green cron tick
    # and a permissions/IO problem surfaces in /dashboard/cron-jobs.
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
