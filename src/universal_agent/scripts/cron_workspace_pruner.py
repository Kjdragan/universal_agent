"""Daily pruning of aged per-run entries inside persistent ``cron_*`` workspaces.

The session reaper (``session/reaper.py::cleanup_stale_workspaces``) EXPLICITLY
skips ``cron_*`` workspaces (``SKIP_PREFIXES = ("cron_",)``) because they are
persistent — the workspace dir itself must survive across runs.  But nothing
reaps the per-run artifacts that accumulate INSIDE them: ``attempts/<NNN>/``
snapshots and the flat root ``work_products/`` (observed on prod: 171,129 files
/ 1.9 GB in a single workspace, 2.94 GB across all ``cron_*`` trees).  This is
the same unbounded-growth pattern (H21) the vp_coder pruner fixes, for a
different workspace family.

This pruner is a SIBLING of ``scripts/vp_coder_workspace_pruner.py`` (see
BRIEF/COMPLETION for the generalize-vs-sibling decision): the vp_coder pruner
moves whole one-off mission workspace dirs, whereas cron workspaces are
persistent, so only aged TOP-LEVEL ENTRIES INSIDE ``attempts/`` and
``work_products/`` are hard-deleted.

What it NEVER deletes (non-negotiable safety rules):
  * the persistent ``cron_*`` workspace dir itself
  * the ``attempts/`` and ``work_products/`` containers
  * persistent bookkeeping: ``run_manifest.json``, ``activity.jsonl``,
    ``run.log``, ``trace.json``, ``transcript.md``, ``MEMORY.md``,
    ``memory/``, ``downloads/``
  * the single newest ``attempts/<NNN>/`` dir (age-independent backstop), and
    any attempt ``run_manifest.json`` names as canonical/latest when a
    manifest exists (none do today, but the guard is kept)

What it DOES hard-delete: top-level entries (files OR subdirs) inside
``<cron_ws>/attempts/`` and ``<cron_ws>/work_products/`` whose mtime is older
than the retention window.

Env knobs (FS-only — NO Infisical-resolved secrets, so it is safe to run with a
plain ``.env`` like the vp_coder pruner):
  UA_CRON_WORKSPACE_RETENTION_HOURS  default 168 (7d)
  UA_CRON_WORKSPACE_PRUNE_DRY_RUN=1  log every would-be deletion, delete nothing
  UA_CRON_WORKSPACES_ROOT            override the workspaces root (default
                                     <repo-root>/AGENT_RUN_WORKSPACES)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import time

logger = logging.getLogger(__name__)

# Persistent bookkeeping at the cron-workspace root that must NEVER be pruned.
# (attempts/ and work_products/ are the only prunable containers.)
_PROTECTED_ROOT_NAMES = frozenset(
    {
        "run_manifest.json",
        "activity.jsonl",
        "run.log",
        "trace.json",
        "transcript.md",
        "MEMORY.md",
        "memory",
        "downloads",
    }
)

# Containers whose TOP-LEVEL entries are eligible for age-based pruning. The
# containers themselves are never deleted.
_PRUNABLE_CONTAINERS = ("attempts", "work_products")


def _repo_root() -> Path:
    """Resolve the repo root file-relative (CWD-independent).

    This module lives at ``<root>/src/universal_agent/scripts/`` so
    ``parents[3]`` is the repo root. Matches the path ``session/reaper.py``
    resolves (``AGENT_RUN_WORKSPACES`` under the repo root) when run from prod.
    """
    return Path(__file__).resolve().parents[3]


def _retention_hours(default: int = 168) -> int:
    raw = (os.getenv("UA_CRON_WORKSPACE_RETENTION_HOURS") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid UA_CRON_WORKSPACE_RETENTION_HOURS=%r; using default %d", raw, default
        )
        return default
    return value if value > 0 else default


def _dry_run() -> bool:
    return (os.getenv("UA_CRON_WORKSPACE_PRUNE_DRY_RUN") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _resolve_cron_workspaces_root() -> Path:
    """Resolve the AGENT_RUN_WORKSPACES root (env override or repo-relative)."""
    override = (os.getenv("UA_CRON_WORKSPACES_ROOT") or "").strip()
    if override:
        return Path(override).expanduser()
    return _repo_root() / "AGENT_RUN_WORKSPACES"


def _read_manifest_attempt_numbers(workspace: Path) -> set[str]:
    """Attempt subdir names (zero-padded, e.g. '001') the manifest protects.

    ``run_workspace.py::ensure_run_workspace_scaffold`` records
    ``canonical_attempt_number`` / ``latest_attempt_number`` in
    ``run_manifest.json``; those attempt dirs are the live pointers and must
    survive even if aged. Returns an empty set when no manifest exists.
    """
    protected: set[str] = set()
    manifest_path = workspace / "run_manifest.json"
    if not manifest_path.is_file():
        return protected
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Unreadable manifest %s: %s", manifest_path, exc)
        return protected
    if not isinstance(manifest, dict):
        return protected
    for key in ("canonical_attempt_number", "latest_attempt_number"):
        num = manifest.get(key)
        if isinstance(num, int):
            protected.add(f"{num:03d}")
    return protected


def _newest_entry_name(container: Path) -> str | None:
    """Name of the newest top-level entry in ``container`` by mtime, or None."""
    newest_name: str | None = None
    newest_mtime = -1.0
    try:
        children = list(container.iterdir())
    except OSError:
        return None
    for child in children:
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        if mtime > newest_mtime:
            newest_mtime = mtime
            newest_name = child.name
    return newest_name


def _format_bytes(n: int) -> str:
    """Human-readable byte count (best-effort, no float drift on the value)."""
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def _dir_size(path: Path) -> int:
    """Total bytes under ``path`` (0 if unreadable). Used for before/after delta."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        return 0
    return total


def _prune_container(
    container: Path,
    cutoff_mtime: float,
    preserve_names: set[str],
    dry_run: bool,
) -> tuple[int, int, int]:
    """Hard-delete aged top-level entries (files+dirs) inside ``container``.

    Returns ``(deleted_dirs, deleted_files, freed_bytes)`` where ``freed_bytes``
    is the bytes reclaimed (or that WOULD be reclaimed in dry-run). Entries in
    ``preserve_names`` (by name) and entries newer than ``cutoff_mtime`` are
    always kept. One bad entry never aborts the sweep. The container itself is
    never deleted.
    """
    deleted_dirs = 0
    deleted_files = 0
    freed_bytes = 0
    try:
        children = list(container.iterdir())
    except OSError as exc:
        logger.warning("Cannot read container %s: %s", container, exc)
        return 0, 0, 0

    for child in children:
        if child.name in preserve_names:
            continue
        try:
            st = child.stat()
        except OSError as exc:
            logger.warning("Cannot stat %s: %s", child, exc)
            continue
        if st.st_mtime >= cutoff_mtime:
            continue  # fresh
        # Bytes this entry occupies: file size directly; for a dir, the full
        # subtree (computed while it still exists in both real + dry-run modes).
        entry_bytes = _dir_size(child) if (child.is_dir() and not child.is_symlink()) else st.st_size
        if dry_run:
            logger.info("[DRY-RUN] would delete %s", child)
            if child.is_dir():
                deleted_dirs += 1
            else:
                deleted_files += 1
            freed_bytes += entry_bytes
            continue
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=False)
                deleted_dirs += 1
            else:
                child.unlink(missing_ok=True)
                deleted_files += 1
            freed_bytes += entry_bytes
            logger.info("Deleted aged entry %s", child)
        except Exception as exc:  # never let one entry abort the sweep
            logger.warning("Failed deleting %s: %s", child, exc)
    return deleted_dirs, deleted_files, freed_bytes


def _prune_workspace(workspace: Path, retention_hours: int, dry_run: bool) -> dict[str, int]:
    """Prune aged entries from one cron workspace. Returns per-workspace stats."""
    stats = {"deleted_dirs": 0, "deleted_files": 0, "freed_bytes": 0}
    # Belt-and-suspenders: never touch protected root bookkeeping. (We only ever
    # descend into _PRUNABLE_CONTAINERS, so this is a defense-in-depth invariant.)
    if workspace.name in _PROTECTED_ROOT_NAMES:
        return stats

    manifest_protected = _read_manifest_attempt_numbers(workspace)
    cutoff_mtime = time.time() - max(1, retention_hours) * 3600

    for container_name in _PRUNABLE_CONTAINERS:
        container = workspace / container_name
        if not container.is_dir():
            continue

        # Preserve manifest-named attempts (canonical/latest). For work_products
        # there is no per-entry preservation — age-pruning naturally keeps the
        # live scratch fresh, and we never delete the container itself.
        preserve = set(manifest_protected) if container_name == "attempts" else set()
        # Age-independent backstop: always keep the single newest entry so a
        # workspace is never fully emptied of its latest run.
        newest = _newest_entry_name(container)
        if newest is not None:
            preserve.add(newest)

        del_dirs, del_files, freed = _prune_container(container, cutoff_mtime, preserve, dry_run)
        stats["deleted_dirs"] += del_dirs
        stats["deleted_files"] += del_files
        stats["freed_bytes"] += freed

    return stats


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    root = _resolve_cron_workspaces_root().expanduser()
    retention = _retention_hours()
    dry_run = _dry_run()

    logger.info(
        "Pruning aged per-run entries in cron_* workspaces "
        "(root=%s, retention=%dh, dry_run=%s)",
        root,
        retention,
        dry_run,
    )

    if not root.is_dir():
        logger.info("Workspaces root does not exist yet: %s", root)
        return 0

    total_dirs = 0
    total_files = 0
    total_freed = 0
    workspace_count = 0
    for workspace in sorted(
        p for p in root.iterdir() if p.is_dir() and p.name.startswith("cron_")
    ):
        workspace_count += 1
        stats = _prune_workspace(workspace, retention, dry_run)
        total_dirs += stats["deleted_dirs"]
        total_files += stats["deleted_files"]
        total_freed += stats["freed_bytes"]
        verb = "would free" if dry_run else "freed"
        logger.info(
            "Workspace %s: %d dirs / %d entries %s(%s %s)",
            workspace.name,
            stats["deleted_dirs"],
            stats["deleted_files"],
            "(would delete) " if dry_run else "",
            verb,
            _format_bytes(stats["freed_bytes"]),
        )

    action = "Would delete" if dry_run else "Deleted"
    freed_verb = "would free" if dry_run else "freed"
    logger.info(
        "%s %d dirs / %d entries across %d cron_* workspace(s) — %s %s",
        action,
        total_dirs,
        total_files,
        workspace_count,
        freed_verb,
        _format_bytes(total_freed),
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
