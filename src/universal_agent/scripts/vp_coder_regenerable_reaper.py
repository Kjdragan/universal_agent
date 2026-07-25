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
  4. Anything git TRACKS. Added 2026-07-25 as a PRECONDITION for root 2:
     ``git ls-files`` on ``origin/main`` returns exactly one path under a
     regenerable name — ``node_modules/.vite/vitest/<sha>/results.json`` —
     so every full repo checkout has a *tracked* ``node_modules/``. Measured
     on prod 2026-07-25: all 7 trees under ``.worktrees`` carry that tracked
     file, so reaping ``node_modules`` by name there would delete tracked
     source AND leave ` D` in ``git status``, which permanently fails the
     clean-tree guard of the weekly merged-worktree prune — the daily job
     would have mechanically disabled the weekly one for the same root.
     NOT a live bug on root 1: the VP-coder mission dirs sit on refs that
     predate that file (``git ls-files -- node_modules`` is empty in all 25,
     and none has a ``node_modules/`` at all), so the guard is a no-op there
     today. It is a landmine, disarmed before stepping on it — root 2 also
     excludes ``node_modules`` from its name set outright. One
     ``git ls-files`` per tree (``vp/worktree_utils.py::tracked_artifact_dirs``)
     is the fix, and it is fail-closed: if we cannot determine tracked state,
     nothing in that tree is reaped.

ACTIVE-MISSION PROTECTION — any direct child of the workspace root whose
``mtime`` is within ``UA_VP_CODER_ACTIVE_MISSION_SKIP_HOURS`` (default 6h)
is skipped entirely. A mission mid-``uv sync`` is never raced.

SECOND ROOT — ``<repo>/.worktrees`` (added 2026-07-25):
``reap_worktree_regenerable_artifacts`` applies the same idea to the repo's
registered git worktrees, which are a different tree entirely (created by
ad-hoc Claude Code sessions running as ``ua``, outside the VP-coder profile
root, so neither tier above has ever reached them). It is branch-state
INDEPENDENT in the sense that matters — it does not require a merged PR — but
it deliberately SKIPS merged trees, because
``vp_coder_workspace_pruner.py::prune_merged_worktrees`` (weekly) removes those
whole and reclaims strictly more. The daily job's remit is therefore exactly
"trees whose PR is still open", which is the space no merged-only rule can
ever touch.

Its target set is NARROWER than the VP-mission one above and the narrowing is
the safety story — see ``WORKTREE_REGENERABLE_ARTIFACT_NAMES``. Ships OFF and
dry-run (``UA_WORKTREE_REGENERABLE_REAP_ENABLED=0``,
``UA_WORKTREE_REGENERABLE_REAP_DRY_RUN=1``).

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
from datetime import datetime
import logging
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Callable, Iterable, Iterator, Optional

from universal_agent.utils.env_utils import env_flag, env_int
from universal_agent.vp.profiles import get_vp_profile
from universal_agent.vp.worktree_utils import (
    detect_repo_root,
    gh_pr_merged_at,
    list_registered_worktrees,
    live_process_inside,
    tracked_artifact_dirs,
    worktree_prune_roots,
)

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

# The reap set for the SECOND root (``<repo>/.worktrees``). Deliberately
# NARROWER than REGENERABLE_ARTIFACT_NAMES, because a registered git worktree
# is a full repo checkout, not a generated mission workspace. The narrowing IS
# the safety story of this tier (all figures measured on prod 2026-07-25):
#
#   * ``.venv`` EXCLUDED — it is not, in fact, automatically regenerated.
#     Nothing in UA ever runs ``uv sync`` in a worktree: ``vp/worker_loop.py``
#     only does ``git worktree add``, and
#     ``services/dependency_upgrade.py::run_uv_sync`` targets the repo root. A
#     worktree ``.venv`` exists only because a human or an agent typed
#     ``uv sync`` there to run tests. Deleting it breaks no service (prod runs
#     ``/opt/universal_agent/.venv``, hard-excluded), but it silently bills the
#     next session in that tree a multi-minute, mostly-RE-DOWNLOADED rebuild —
#     ``~ua/.cache/uv`` is 317 MB against a 6.5 GiB venv. That is a reap with a
#     real uncompensated cost, not a free cache drop. The one ``.venv`` in
#     ``.worktrees`` today (6.9 GiB) sits behind a MERGED PR anyway, so the
#     weekly merged-worktree prune already reaches it — and reaches more of it.
#   * ``node_modules`` EXCLUDED — this repo TRACKS
#     ``node_modules/.vite/vitest/<sha>/results.json``, so every full checkout
#     has a *tracked* ``node_modules/`` (32 B of content). The tracked-content
#     guard would refuse it anyway; excluding it by name means we never ask.
#   * ``dist`` / ``build`` / ``.next`` EXCLUDED — 0 bytes outside ``.venv`` in
#     all 7 production worktrees, and unlike in a generated mission workspace
#     they are plausible TRACKED directory names in a full repo checkout.
#
# What remains regenerates for free with no download: CPython rewrites
# ``__pycache__`` on the next import, pytest and ruff rebuild their caches on
# the next run. Honest reach at the time of writing: 73.3 MiB across the five
# open-PR worktrees. Do not quote a larger number without re-measuring.
WORKTREE_REGENERABLE_ARTIFACT_NAMES: frozenset[str] = frozenset(
    {"__pycache__", ".pytest_cache", ".ruff_cache"}
)

# Never descend into these while hunting for targets, in EITHER root: they are
# either out of scope by policy (``.venv`` / ``node_modules`` in a worktree),
# already collected whole (the VP-mission case), or git's own metadata.
# Pruning ``.venv`` matters concretely: a naive walk of the 6.9 GiB worktree
# venv finds ~31 MB of ``__pycache__`` *inside* the tree we just promised not
# to touch.
_NEVER_DESCEND_DIRS: frozenset[str] = frozenset({".git"}) | REGENERABLE_ARTIFACT_NAMES

# Directory names whose mtimes are NOT evidence that a human or agent touched a
# worktree: one repo-root ``ruff check .`` rewrote ``.ruff_cache`` inside six
# production worktrees at one identical instant (2026-07-23 23:24:17 UTC, 58
# files each), and any import rewrites ``__pycache__``. Excluded when asking
# "when was this tree really last worked on?" — with them included, six of
# seven trees look active forever.
_QUIESCENCE_IGNORED_DIRS: frozenset[str] = _NEVER_DESCEND_DIRS | {
    ".pytest_cache",
    ".ruff_cache",
}


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
    names: Iterable[str] = REGENERABLE_ARTIFACT_NAMES,
) -> Iterator[Path]:
    """Yield regenerable artifact paths under ``mission_dir``.

    ``names`` is the set we YIELD (the reap allowlist — narrower for the
    ``.worktrees`` root). Descent is pruned at ``_NEVER_DESCEND_DIRS`` as well,
    so a directory that is out of scope by policy is never *entered*: without
    that, a walk for ``__pycache__`` inside a worktree happily finds ~31 MB of
    it inside the 6.9 GiB ``.venv`` we just promised not to touch. ``.git`` is
    never entered either — no reap target can legitimately live in git's own
    metadata.

    Hard excludes (defense-in-depth):

      * The resolved live repo ``.venv`` — equality on ``resolve()``.
      * Any file named ``activity_state.db`` (caught here as a no-op; the
        filename check is also enforced at the remove step).
    """
    yield_names = frozenset(names)
    # os.walk lets us mutate dirs[:] in place to prune subtrees we either
    # collect whole or refuse to touch — saves a wasted descent into
    # node_modules / .venv / .git.
    for dirpath, dirnames, _filenames in os.walk(mission_dir):
        keep: list[str] = []
        for name in dirnames:
            candidate = Path(dirpath) / name
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate
            if candidate.name in yield_names:
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
            if candidate.name in _NEVER_DESCEND_DIRS:
                # Out of scope by policy: not yielded, and never entered.
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

        # One `git ls-files` per mission dir: a mission dir that IS a git
        # worktree (nine of them are) can hold TRACKED content under a
        # regenerable name. Fail-closed — unknown means reap nothing here.
        tracked_dirs = tracked_artifact_dirs(mission_dir, REGENERABLE_ARTIFACT_NAMES)
        if tracked_dirs is None:
            logger.warning(
                "Skipping mission dir %s: could not determine git-tracked "
                "content (fail-closed).",
                mission_dir,
            )
            continue

        for artifact_path in _find_regenerable_targets(
            mission_dir, skip_cutoff, live_repo_venv
        ):
            try:
                relative = artifact_path.relative_to(mission_dir).as_posix()
            except ValueError:
                relative = None
            if relative is None or relative in tracked_dirs:
                logger.warning(
                    "Skipping %s: git tracks content inside it — never delete "
                    "tracked source.",
                    artifact_path,
                )
                continue

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


# ---------------------------------------------------------------------------
# Second root: the repo's registered git worktrees (``<repo>/.worktrees``)
# ---------------------------------------------------------------------------


def _worktree_idle_seconds(default_hours: int = 72) -> int:
    """Quiescence window for a worktree, in seconds (default 72h).

    Deliberately an order of magnitude longer than the 6h mission-dir window:
    a mission dir is machine-generated and its mtime tracks the mission, while
    a worktree belongs to an interactive agent session whose activity we cannot
    observe directly (see ``live_process_inside``'s KNOWN GAP).
    """
    hours = env_int(
        "UA_WORKTREE_REGENERABLE_REAP_IDLE_HOURS", default_hours, minimum=1
    )
    return hours * 3600


def _worktree_reap_names() -> frozenset[str]:
    """Resolve the reap set for the worktree root.

    ``UA_WORKTREE_REGENERABLE_REAP_NAMES`` (comma-separated) can only NARROW
    ``WORKTREE_REGENERABLE_ARTIFACT_NAMES``, never widen it. An operator who
    types ``.venv`` there gets a warning and no ``.venv`` deletion: the reasons
    that name is excluded are code-level facts about this estate, not a default
    someone should be able to flip from ``.env`` at 3am.
    """
    raw = (os.getenv("UA_WORKTREE_REGENERABLE_REAP_NAMES") or "").strip()
    if not raw:
        return WORKTREE_REGENERABLE_ARTIFACT_NAMES
    requested = {part.strip() for part in raw.split(",") if part.strip()}
    rejected = requested - WORKTREE_REGENERABLE_ARTIFACT_NAMES
    if rejected:
        logger.warning(
            "UA_WORKTREE_REGENERABLE_REAP_NAMES asked for non-allowlisted "
            "name(s) %s; ignoring them (this knob can only narrow the "
            "allowlist %s, never widen it).",
            sorted(rejected),
            sorted(WORKTREE_REGENERABLE_ARTIFACT_NAMES),
        )
    return frozenset(requested & WORKTREE_REGENERABLE_ARTIFACT_NAMES)


def _newest_activity_mtime(root: Path) -> Optional[float]:
    """Newest mtime under ``root``, ignoring caches and git metadata.

    Returns ``None`` when ``root`` cannot be stat'ed (caller fails closed).
    Raw newest-file mtime is provably useless as an idleness signal here: six
    of seven production worktrees reported their newest file at the same
    instant, 58 ``.ruff_cache`` files each, from one repo-root ``ruff check .``.
    With the cache dirs excluded, the real last-touch times separate cleanly
    (2026-07-10 … 2026-07-15 for the open trees).
    """
    try:
        newest = root.stat().st_mtime
    except OSError as exc:
        logger.warning("Could not stat %s: %s", root, exc)
        return None

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _QUIESCENCE_IGNORED_DIRS]
        for name in dirnames + filenames:
            try:
                mtime = (Path(dirpath) / name).lstat().st_mtime
            except OSError:
                continue
            if mtime > newest:
                newest = mtime
    return newest


def _wt_record(
    records: list[dict],
    wt_path: Path,
    branch: Optional[str],
    action: str,
    reason: str,
    *,
    artifact: Optional[Path] = None,
    size_bytes: Optional[int] = None,
) -> None:
    records.append(
        {
            "worktree": str(wt_path),
            "branch": branch,
            "action": action,
            "reason": reason,
            "artifact": str(artifact) if artifact is not None else None,
            "artifact_name": artifact.name if artifact is not None else None,
            "size_bytes": size_bytes,
        }
    )


def _wt_skip(
    records: list[dict],
    wt_path: Path,
    branch: Optional[str],
    reason: str,
    *,
    artifact: Optional[Path] = None,
) -> None:
    logger.info(
        "worktree-regenerable-reap SKIP %s (branch=%s): %s",
        artifact if artifact is not None else wt_path,
        branch,
        reason,
    )
    _wt_record(records, wt_path, branch, "skipped", reason, artifact=artifact)


def reap_worktree_regenerable_artifacts(
    *,
    repo_root: Optional[Path] = None,
    allowed_roots: Optional[list[Path]] = None,
    names: Optional[Iterable[str]] = None,
    idle_hours: Optional[int] = None,
    enabled: Optional[bool] = None,
    dry_run: Optional[bool] = None,
    merged_at: Optional[Callable[[str], Optional[datetime]]] = None,
    now: Optional[float] = None,
) -> list[dict]:
    """Reap regenerable artifacts from registered git worktrees.

    The SECOND root of this daily job. Unlike
    ``vp_coder_workspace_pruner.py::prune_merged_worktrees`` (weekly) it does
    not need a merged PR — it frees the space sitting behind worktrees whose
    PRs are still OPEN, which no merged-only rule can ever reach. It removes no
    git metadata, no source, and no whole worktree.

    Every I/O dependency is injectable so unit tests never touch the network.

    An artifact is removed only when ALL of these hold; anything that errors or
    is unknown means SKIP (fail-closed), and every skip is logged with its
    reason:

      1. The worktree came from ``worktree_utils.py::list_registered_worktrees``
         (``git worktree list --porcelain``) — never a filesystem walk.
      2. Not the main working tree, not ``bare``, not the repo root itself.
      3. Its parent is in the allowlist — ``worktree_prune_roots``, i.e. the
         SAME ``UA_WORKTREE_PRUNE_ROOTS`` the weekly prune uses, so the two
         jobs can never disagree about scope. This also keeps this root out of
         ``AGENT_RUN_WORKSPACES/**``, which ``reap_regenerable_artifacts``
         already owns.
      4. Not ``prunable``, not ``detached``, not ``locked`` (git's own
         do-not-touch marker), and it has a branch.
      5. NOT merged. A merged tree belongs to the weekly prune, which removes
         it whole and reclaims strictly more; taking bytes out from under it
         would only make its clean-tree guard harder to satisfy.
      6. Quiescent for ``idle_hours`` (default 72) on BOTH the worktree
         directory's own mtime and the newest non-cache mtime beneath it.
      7. No live process has its cwd, exe, or a mapped file inside the tree.
      8. The artifact's name is in the reap allowlist (``names``), and git
         tracks nothing inside it.
      9. The existing hard excludes still apply: the live repo ``.venv`` and
         ``activity_state.db``.

    Returns one record per candidate (``action`` in ``reaped`` / ``would-reap``
    / ``skipped``) for observability.
    """

    enabled = (
        env_flag("UA_WORKTREE_REGENERABLE_REAP_ENABLED", False)
        if enabled is None
        else enabled
    )
    if not enabled:
        logger.info(
            "Worktree regenerable reap disabled "
            "(UA_WORKTREE_REGENERABLE_REAP_ENABLED=0); skipping."
        )
        return []

    dry_run = (
        env_flag("UA_WORKTREE_REGENERABLE_REAP_DRY_RUN", True)
        if dry_run is None
        else dry_run
    )
    # The allowlist is a code-level policy, not a parameter: an injected
    # ``names`` (or the env knob) can only narrow it. Nothing outside
    # WORKTREE_REGENERABLE_ARTIFACT_NAMES is deletable through this path.
    requested = frozenset(names) if names is not None else _worktree_reap_names()
    reap_names = requested & WORKTREE_REGENERABLE_ARTIFACT_NAMES
    if requested - reap_names:
        logger.warning(
            "Refusing non-allowlisted reap name(s) %s; allowlist is %s.",
            sorted(requested - reap_names),
            sorted(WORKTREE_REGENERABLE_ARTIFACT_NAMES),
        )
    idle_seconds = (
        idle_hours * 3600 if idle_hours is not None else _worktree_idle_seconds()
    )
    now_ts = now if now is not None else time.time()
    merged_at = merged_at or gh_pr_merged_at
    live_repo_venv = _live_repo_venv_path()

    if not reap_names:
        logger.warning("Worktree regenerable reap has an empty name set; nothing to do.")
        return []

    try:
        repo = (repo_root or detect_repo_root()).resolve()
    except Exception as exc:  # noqa: BLE001 - a timer job must never crash here
        logger.warning("Could not resolve repo root; skipping worktree reap: %s", exc)
        return []

    roots = [Path(r).resolve() for r in (allowed_roots or worktree_prune_roots(repo))]
    cutoff = now_ts - idle_seconds
    logger.info(
        "Worktree regenerable reap: repo=%s roots=%s names=%s idle=%dh dry_run=%s",
        repo,
        [str(r) for r in roots],
        sorted(reap_names),
        idle_seconds // 3600,
        dry_run,
    )

    records: list[dict] = []
    out_of_scope = 0

    for wt in list_registered_worktrees(repo_root=repo):
        if wt.is_main or wt.bare:
            out_of_scope += 1
            continue
        try:
            wt_path = wt.path.resolve()
        except OSError:
            wt_path = wt.path
        if wt_path == repo:
            out_of_scope += 1
            continue
        if wt_path.parent not in roots:
            # Owned by someone else (the VP-coder profile root has nine
            # registrations of its own). Counted, not reported per-entry.
            out_of_scope += 1
            logger.debug("worktree-regenerable-reap out-of-scope: %s", wt_path)
            continue

        branch = wt.branch
        if wt.prunable:
            _wt_skip(records, wt_path, branch,
                     f"git reports prunable ({wt.prunable_reason or 'no reason'})")
            continue
        if wt.detached:
            _wt_skip(records, wt_path, branch, "detached HEAD (no branch to resolve)")
            continue
        if wt.locked:
            _wt_skip(records, wt_path, branch,
                     f"locked by git ({wt.lock_reason or 'no reason given'})")
            continue
        if not branch:
            _wt_skip(records, wt_path, branch, "no branch recorded")
            continue
        if not wt_path.is_dir():
            _wt_skip(records, wt_path, branch, "worktree directory missing")
            continue

        # Cheap local quiescence floor first, so we never spend a network call
        # on an obviously-active tree.
        try:
            dir_mtime = wt_path.stat().st_mtime
        except OSError as exc:
            _wt_skip(records, wt_path, branch, f"could not stat worktree: {exc}")
            continue
        if dir_mtime > cutoff:
            _wt_skip(records, wt_path, branch,
                     f"directory touched {(now_ts - dir_mtime) / 3600:.1f}h ago "
                     f"(< {idle_seconds // 3600}h quiescence)")
            continue

        # A merged tree is the weekly prune's, not ours.
        merged_ts = merged_at(branch)
        if merged_ts is not None:
            _wt_skip(records, wt_path, branch,
                     f"branch merged at {merged_ts.isoformat()} — owned by the "
                     "weekly merged-worktree prune, which reclaims the whole tree")
            continue

        newest = _newest_activity_mtime(wt_path)
        if newest is None:
            _wt_skip(records, wt_path, branch, "could not determine last activity")
            continue
        if newest > cutoff:
            _wt_skip(records, wt_path, branch,
                     f"last touched {(now_ts - newest) / 3600:.1f}h ago "
                     f"(< {idle_seconds // 3600}h quiescence, caches excluded)")
            continue

        if live_process_inside(wt_path, deep=True):
            _wt_skip(records, wt_path, branch,
                     "a running process has its cwd/exe/mapping inside this worktree")
            continue

        tracked_dirs = tracked_artifact_dirs(wt_path, reap_names)
        if tracked_dirs is None:
            _wt_skip(records, wt_path, branch,
                     "could not determine git-tracked content (fail-closed)")
            continue

        for artifact_path in _find_regenerable_targets(
            wt_path, cutoff, live_repo_venv, names=reap_names
        ):
            try:
                relative = artifact_path.relative_to(wt_path).as_posix()
            except ValueError:
                relative = None
            if relative is None or relative in tracked_dirs:
                _wt_skip(records, wt_path, branch,
                         "git tracks content inside it — never delete tracked source",
                         artifact=artifact_path)
                continue

            try:
                resolved_artifact = artifact_path.resolve()
            except OSError:
                resolved_artifact = artifact_path
            if resolved_artifact == live_repo_venv:
                _wt_skip(records, wt_path, branch, "live repo .venv (hard exclude)",
                         artifact=artifact_path)
                continue
            if artifact_path.name in _HARD_EXCLUDED_FILENAMES:
                _wt_skip(records, wt_path, branch, "runtime DB (hard exclude)",
                         artifact=artifact_path)
                continue

            size_bytes: Optional[int] = None
            try:
                size_bytes = _dir_size_bytes(artifact_path)
            except OSError as exc:
                logger.debug("Could not size %s: %s", artifact_path, exc)

            if dry_run:
                logger.info(
                    "worktree-regenerable-reap [DRY-RUN] would reap %s (%.1f MiB)",
                    artifact_path, (size_bytes or 0) / (1024 * 1024),
                )
                _wt_record(records, wt_path, branch, "would-reap", "regenerable",
                           artifact=artifact_path, size_bytes=size_bytes)
                continue

            try:
                shutil.rmtree(artifact_path)
            except FileNotFoundError:
                continue  # race with another reaper — fine
            except OSError as exc:
                _wt_skip(records, wt_path, branch, f"rmtree failed: {exc}",
                         artifact=artifact_path)
                continue
            logger.info(
                "worktree-regenerable-reap REAPED %s (%.1f MiB)",
                artifact_path, (size_bytes or 0) / (1024 * 1024),
            )
            _wt_record(records, wt_path, branch, "reaped", "regenerable",
                       artifact=artifact_path, size_bytes=size_bytes)

    per_category: dict[str, list[int]] = {}
    for rec in records:
        if rec["action"] not in {"reaped", "would-reap"}:
            continue
        bucket = per_category.setdefault(rec["artifact_name"] or "?", [0, 0])
        bucket[0] += 1
        bucket[1] += rec.get("size_bytes") or 0
    total_bytes = sum(bucket[1] for bucket in per_category.values())
    breakdown = "; ".join(
        f"{name} {count} dir(s) / {size / (1024 * 1024):.1f} MiB"
        for name, (count, size) in sorted(per_category.items())
    ) or "nothing"
    logger.info(
        "Worktree regenerable reap finished: %d reaped, %d would-reap, %d skipped, "
        "%d out-of-scope (%.1f MiB %s). Per category: %s",
        sum(1 for r in records if r["action"] == "reaped"),
        sum(1 for r in records if r["action"] == "would-reap"),
        sum(1 for r in records if r["action"] == "skipped"),
        out_of_scope,
        total_bytes / (1024 * 1024),
        "identified" if dry_run else "reclaimed",
        breakdown,
    )
    return records


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    reap_regenerable_artifacts(dry_run=False)

    # Second root — additive and independent. It must never abort the job: the
    # VP-mission reap above has already done its work by this point, and this
    # half shells out to git/gh, which can fail in ways pure FS work cannot.
    try:
        reap_worktree_regenerable_artifacts()
    except Exception as exc:  # noqa: BLE001 - a timer job always exits 0
        logger.warning(
            "Worktree regenerable reap raised; continuing: %s", exc, exc_info=True
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
