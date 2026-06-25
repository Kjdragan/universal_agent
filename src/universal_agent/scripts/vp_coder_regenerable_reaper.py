"""Daily regenerable-artifact reaper for VP-coder mission dirs.

Companion to ``scripts/vp_coder_workspace_pruner.py``. That weekly pruner
archives whole mission dirs older than the retention window (default 7d).
This daily reaper removes ONLY regenerable artifacts from each mission dir:

  ``.venv``, ``__pycache__``, ``node_modules``, ``.pytest_cache``,
  ``.ruff_cache``, ``dist``, ``build``, ``.next``

These rebuild via ``uv sync`` / ``npm install`` / the project build, so
no 7-day evidence window applies. The 2026-06-25 disk-critical incident
(30G across 221 coder mission dirs, 19.6G of it regenerable ``.venv``
between weekly runs) is the root cause this module addresses.

SAFETY CONTRACT — what this reaper will NEVER delete:

  1. The live repo's ``.venv`` (resolved-path equality hard exclude, so a
     misconfigured ``UA_VP_CODER_WORKSPACE_ROOT`` cannot nuke the runtime).
  2. Any file named ``activity_state.db`` anywhere under the workspaces
     tree (the live runtime state DB — defense-in-depth).
  3. Mission source, manifests, logs, BRIEF / ACCEPTANCE / COMPLETION,
     ``SOURCES/``, ``run_output.txt``, ``manifest.json``, etc. — none of
     these are in ``REGENERABLE_ARTIFACT_NAMES``.

ACTIVE-MISSION PROTECTION — any direct child of the workspace root whose
``mtime`` is within ``UA_VP_CODER_ACTIVE_MISSION_SKIP_HOURS`` (default 6h)
is skipped entirely. A mission mid-``uv sync`` is never raced.

Registration: ``gateway_server._ensure_vp_coder_regenerable_reap_cron_job``
via the canonical ``_register_system_cron_job`` helper (catch-up, secrets
bootstrap, update-vs-create, systemd-timer migration gate). Runs daily
inside the 06:00-21:00 CT active window — a fixed-time cron, which is
dormancy-exempt per
``project_docs/08_operations/03_dormancy_and_operating_hours.md``, but we
keep it inside the window anyway so a missed catch-up still lands inside
the operator's day.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Iterable, Iterator, Optional

from universal_agent.vp.profiles import get_vp_profile

logger = logging.getLogger(__name__)


# The canonical regenerable set. Each of these is reproducible from the
# mission's committed source (``uv sync`` for .venv, ``npm install`` for
# node_modules, the project build for dist/build/.next, interpreter/framework
# re-derivation for the cache dirs). If you add a name here, add a test.
REGENERABLE_ARTIFACT_NAMES: frozenset[str] = frozenset(
    {
        ".venv",
        "__pycache__",
        "node_modules",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".next",
    }
)

# Anything literally named activity_state.db is a live runtime DB — NEVER remove.
_HARD_EXCLUDED_FILENAMES: frozenset[str] = frozenset({"activity_state.db"})


def _active_skip_seconds(default_hours: int = 6) -> int:
    """Resolve the active-mission skip window in seconds.

    A direct-child mission dir whose mtime is within this window is skipped
    entirely. Default 6h; override via ``UA_VP_CODER_ACTIVE_MISSION_SKIP_HOURS``.
    """
    raw = (os.getenv("UA_VP_CODER_ACTIVE_MISSION_SKIP_HOURS") or "").strip()
    if not raw:
        return default_hours * 3600
    try:
        hours = int(raw)
    except ValueError:
        logger.warning(
            "Invalid UA_VP_CODER_ACTIVE_MISSION_SKIP_HOURS=%r; using default %dh",
            raw,
            default_hours,
        )
        return default_hours * 3600
    return (hours if hours > 0 else default_hours) * 3600


def _resolve_coder_workspace_root() -> Optional[Path]:
    """Resolve the SAME path the writer uses (vp/profiles.py::resolve_vp_profiles).

    Mirrors ``scripts/vp_coder_workspace_pruner.py::_resolve_coder_workspace_root``
    so writer, weekly-pruner, and daily-reaper can never diverge. Reading the
    ``UA_VP_CODER_WORKSPACE_ROOT`` env var directly with no fallback was the
    H21 bug (writer and reaper diverged on different paths).
    """
    profile = get_vp_profile("vp.coder.primary")
    if profile is None or not getattr(profile, "workspace_root", None):
        return None
    return Path(profile.workspace_root)


def _live_repo_venv_path() -> Path:
    """Resolved path of the runtime's live ``.venv`` (the one uv sync builds).

    Used as a hard exclude: even if a misconfigured ``UA_VP_CODER_WORKSPACE_ROOT``
    pointed at the repo root, we still refuse to remove this path.
    """
    repo_root = Path(__file__).resolve().parents[3]
    return (repo_root / ".venv").resolve()


def _iter_mission_dirs(root: Path) -> Iterator[Path]:
    """Yield direct child directories of ``root`` (the workspace root).

    Non-directory entries (stray files, the ``_archive`` sibling, etc.) are
    skipped. The ``_archive`` sibling is owned by the weekly pruner.
    """
    try:
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if child.name == "_archive":
                continue
            yield child
    except (FileNotFoundError, PermissionError, OSError) as exc:
        logger.warning("Failed iterating workspace root %s: %s", root, exc)


def _find_regenerable_targets(
    mission_dir: Path,
    skip_cutoff: float,
    live_repo_venv: Path,
) -> Iterator[Path]:
    """Yield regenerable artifact paths under ``mission_dir``.

    Walks the mission subtree but prunes any directory whose name is in
    ``REGENERABLE_ARTIFACT_NAMES`` (we collect it as a reap target and do
    NOT descend further — ``node_modules`` subtrees can be enormous).

    Hard excludes (defense-in-depth):

      * The resolved live repo ``.venv`` — equality on ``resolve()``.
      * Any file named ``activity_state.db`` (caught here as a no-op; the
        filename check is also enforced at the remove step).
    """
    # os.walk lets us mutate dirs[:] in place to prune regenerable subtrees
    # we're already going to remove — saves a wasted descent into node_modules.
    for dirpath, dirnames, _filenames in os.walk(mission_dir):
        keep: list[str] = []
        for name in dirnames:
            candidate = Path(dirpath) / name
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate
            if candidate.name in REGENERABLE_ARTIFACT_NAMES:
                if resolved == live_repo_venv:
                    # Hard-excluded live repo .venv: do not yield, do not
                    # descend (defense-in-depth at the walker level too).
                    logger.debug(
                        "Hard-excluded live repo .venv from walk: %s", candidate
                    )
                    continue
                yield candidate
                # Do not descend into the regenerable subtree we just yielded.
                continue
            keep.append(name)
        dirnames[:] = keep


def reap_regenerable_artifacts(
    root: Optional[Path] = None,
    skip_hours: Optional[int] = None,
    dry_run: bool = False,
    now: Optional[float] = None,
) -> list[dict]:
    """Remove regenerable artifacts from each VP-coder mission dir under ``root``.

    Args:
        root: Override the workspace root (defaults to the resolved coder profile
            root). Acceptance tests pass a temp dir.
        skip_hours: Override the active-mission skip window in hours (defaults
            to ``UA_VP_CODER_ACTIVE_MISSION_SKIP_HOURS`` or 6).
        dry_run: When True, log what would be removed but do not delete.
        now: Override the "current time" reference (tests freeze this).

    Returns:
        A list of per-reap dicts (``{"mission_dir", "artifact", "artifact_name",
        "size_bytes", "skipped_recent"}``) for observability. ``size_bytes`` is
        best-effort (``None`` when unavailable); the runtime caller can sum it.
    """
    if root is None:
        resolved_root = _resolve_coder_workspace_root()
        if resolved_root is None:
            logger.info(
                "VP-coder profile unavailable (VP disabled?); nothing to reap."
            )
            return []
        root = resolved_root

    root = Path(root).expanduser()
    if not root.exists():
        logger.info("VP-coder workspace root does not exist yet: %s", root)
        return []

    skip_seconds = (
        skip_hours * 3600
        if skip_hours is not None
        else _active_skip_seconds()
    )
    now_ts = now if now is not None else time.time()
    skip_cutoff = now_ts - skip_seconds
    live_repo_venv = _live_repo_venv_path()

    logger.info(
        "Reaping regenerable artifacts from VP-coder missions under %s "
        "(skip_hours=%d, dry_run=%s, live_repo_venv=%s)",
        root,
        skip_seconds // 3600,
        dry_run,
        live_repo_venv,
    )

    reaped: list[dict] = []
    for mission_dir in _iter_mission_dirs(root):
        try:
            mission_mtime = mission_dir.stat().st_mtime
        except OSError as exc:
            logger.warning("Could not stat mission dir %s: %s", mission_dir, exc)
            continue

        if mission_mtime > skip_cutoff:
            logger.debug(
                "Skipping recently-active mission dir (%.1fh old): %s",
                (now_ts - mission_mtime) / 3600,
                mission_dir,
            )
            continue

        for artifact_path in _find_regenerable_targets(
            mission_dir, skip_cutoff, live_repo_venv
        ):
            # Defense-in-depth: never remove the live repo .venv (resolve()
            # equality) and never remove anything literally named
            # activity_state.db — even if the iterator yielded it.
            try:
                resolved_artifact = artifact_path.resolve()
            except OSError:
                resolved_artifact = artifact_path
            if resolved_artifact == live_repo_venv:
                logger.warning(
                    "Hard-excluded live repo .venv from reap: %s", artifact_path
                )
                continue
            if artifact_path.name in _HARD_EXCLUDED_FILENAMES:
                logger.warning(
                    "Hard-excluded runtime DB from reap: %s", artifact_path
                )
                continue

            size_bytes: Optional[int] = None
            try:
                size_bytes = _dir_size_bytes(artifact_path)
            except OSError as exc:
                logger.debug("Could not size %s: %s", artifact_path, exc)

            if dry_run:
                logger.info("[DRY-RUN] Would reap: %s", artifact_path)
            else:
                try:
                    shutil.rmtree(artifact_path)
                except FileNotFoundError:
                    continue  # race with another reaper — fine
                except OSError as exc:
                    logger.warning(
                        "Failed to reap %s: %s", artifact_path, exc
                    )
                    continue

            reaped.append(
                {
                    "mission_dir": str(mission_dir),
                    "artifact": str(artifact_path),
                    "artifact_name": artifact_path.name,
                    "size_bytes": size_bytes,
                }
            )

    total_bytes = sum(
        (r.get("size_bytes") or 0) for r in reaped
    )
    logger.info(
        "Reaped %d regenerable artifact(s) (%.2f MiB) from %d mission dir(s).",
        len(reaped),
        total_bytes / (1024 * 1024),
        len({r["mission_dir"] for r in reaped}),
    )
    return reaped


def _dir_size_bytes(path: Path) -> int:
    """Best-effort du for a directory tree (symlinks skipped)."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for fname in filenames:
            fpath = Path(dirpath) / fname
            try:
                if fpath.is_symlink():
                    continue
                total += fpath.stat().st_size
            except OSError:
                continue
    return total


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    reap_regenerable_artifacts(dry_run=False)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
