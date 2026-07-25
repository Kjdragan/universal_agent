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

THIRD TIER — MERGED-ONLY GIT-WORKTREE PRUNE (added 2026-07-24):
``prune_merged_worktrees`` removes registered git worktrees under
``<repo>/.worktrees`` whose branch is provably merged. Re-measured
2026-07-25: ``/opt/universal_agent/.worktrees`` holds 8,772,672,614 B
(8.17 GiB) across **7** worktrees — still the single largest consumer under
``/opt/universal_agent``, larger than the live ``.venv`` (6.9 GiB) — and
nothing in the estate had ever reclaimed it (both existing tiers scope to the
VP-coder profile root). The figure this docstring shipped with on 2026-07-24
("15,700 MiB across 8 worktrees, 13.25 GiB of it two ``.venv`` trees") went
~7 GiB stale within a day: an autonomous proactive-health run removed the 8th
worktree at 2026-07-25 00:06 UTC. Re-measure before quoting; the estate moves
underneath this file.

The still-live 6.9 GiB ``.venv`` is in ``cron-dispatch-wedge`` (PR #1482,
merged) — i.e. inside THIS tier's reach, not something an open-PR rule could
get. Both of the two large ``.venv`` trees the original note described were
behind MERGED PRs.

COMPANION — DAILY WORKTREE REGENERABLE REAP (added 2026-07-25):
``vp_coder_regenerable_reaper.py::reap_worktree_regenerable_artifacts`` reaps
regenerable artifacts (``__pycache__`` / ``.pytest_cache`` / ``.ruff_cache``
only) from worktrees in the SAME allowlisted roots, and it explicitly SKIPS any
tree whose PR is merged — those are this tier's, and this tier reclaims the
whole tree rather than a few MiB of caches. The two compose rather than
overlap: daily owns OPEN-PR trees (73.3 MiB across five of them, measured
2026-07-25), weekly owns merged ones (6.94 GiB). Neither may leave a tree
dirty, because guard 6 below would then refuse it forever — which is why both
jobs run ``vp/worktree_utils.py::tracked_artifact_dirs`` before deleting
anything.

**The merged predicate is a UNION, deliberately.** ``git merge-base
--is-ancestor <head> origin/main`` is checked first, but this repo
squash-merges every PR (``gh pr merge --squash --auto --delete-branch``),
so a merged branch's tip is never an ancestor of ``main``: measured
2026-07-24, ``--is-ancestor`` reported NOT-merged for all 8 worktree
branches, *including* two whose PRs were definitively merged (#1482,
#1415). An ``--is-ancestor``-only rule reclaims exactly zero bytes,
forever, while looking healthy in the log. So a second, authoritative
proof is consulted when the ancestry check says no: the GitHub PR state
via ``gh pr list --state merged``. Either proof alone is sufficient;
neither is skipped for convenience.

This tier is the first code in the estate that deletes git-managed state
on a timer, so it ships **disabled and dry-run by default**
(``UA_WORKTREE_PRUNE_ENABLED=0``, ``UA_WORKTREE_PRUNE_DRY_RUN=1``) and
every candidate is logged with its removal-or-skip reason.
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
from typing import Callable, Optional

# Reuse the daily reaper's best-effort du rather than writing a second one.
from universal_agent.scripts.vp_coder_regenerable_reaper import _dir_size_bytes
from universal_agent.session.reaper import cleanup_stale_workspaces
from universal_agent.utils.env_utils import env_flag as _env_flag
from universal_agent.vp.profiles import get_vp_profile

# Shared with the daily worktree reap — one copy in worktree_utils so the two
# jobs can never disagree about scope (`_prune_roots`), liveness
# (`_live_process_inside`) or merged-state (`_gh_pr_merged_at`). They cannot
# live in either script: this module already imports from the reaper.
from universal_agent.vp.worktree_utils import (
    GIT_TIMEOUT_S as _GIT_TIMEOUT_S,
    RegisteredWorktree,
    detect_repo_root,
    gh_pr_merged_at as _gh_pr_merged_at,
    list_registered_worktrees,
    live_process_inside as _live_process_inside,
    run_git as _run,
    teardown_worktree,
    worktree_prune_roots as _prune_roots,
)

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


# ---------------------------------------------------------------------------
# Tier 3: merged-only git-worktree prune
# ---------------------------------------------------------------------------


def _min_merge_age_hours(default: int = 24) -> int:
    """Quiescence window between "PR merged" and "safe to remove the tree".

    Default 24h, override via ``UA_WORKTREE_PRUNE_MIN_MERGE_AGE_HOURS``. This
    gate is the one that saves the hazard case measured 2026-07-24: PR #1482
    merged at 02:49:20 UTC while its 7,060 MiB worktree had files written at
    02:42:40 UTC the same morning.
    """
    raw = (os.getenv("UA_WORKTREE_PRUNE_MIN_MERGE_AGE_HOURS") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid UA_WORKTREE_PRUNE_MIN_MERGE_AGE_HOURS=%r; using default %d",
            raw, default,
        )
        return default
    return value if value > 0 else default


def _fetch_base(repo_root: Path, base_ref: str) -> bool:
    """Freshen the base remote-tracking ref (e.g. ``origin/main``).

    Deliberately fetches ONE branch and does NOT ``--prune``: the merged
    branches we care about have already had their remote refs deleted by
    ``gh pr merge --delete-branch``, and pruning the local remote-tracking
    refs would erase the "nothing unpushed" evidence that gate 7 needs.
    """
    remote, _, branch = base_ref.partition("/")
    if not remote or not branch:
        logger.warning("Unparseable base ref %r; skipping fetch.", base_ref)
        return False
    result = _run(
        ["git", "fetch", "--quiet", remote, branch],
        cwd=repo_root, timeout=_GIT_TIMEOUT_S,
    )
    if result is None or result.returncode != 0:
        detail = "timeout" if result is None else (result.stderr or result.stdout).strip()
        logger.warning("git fetch %s %s failed: %s", remote, branch, detail)
        return False
    return True


def _skip(records: list[dict], wt: RegisteredWorktree, reason: str) -> None:
    logger.info("worktree-prune SKIP %s (branch=%s): %s", wt.path, wt.branch, reason)
    records.append(
        {
            "worktree": str(wt.path),
            "branch": wt.branch,
            "action": "skipped",
            "reason": reason,
            "size_bytes": None,
        }
    )


def prune_merged_worktrees(
    *,
    repo_root: Optional[Path] = None,
    allowed_roots: Optional[list[Path]] = None,
    base_ref: Optional[str] = None,
    merged_at: Optional[Callable[[str], Optional[datetime]]] = None,
    min_merge_age_hours: Optional[int] = None,
    enabled: Optional[bool] = None,
    dry_run: Optional[bool] = None,
    now: Optional[float] = None,
    fetch: bool = True,
) -> list[dict]:
    """Remove registered git worktrees whose branch is provably merged.

    Every I/O dependency is injectable so unit tests never touch the network
    (mirrors ``vp_coder_regenerable_reaper.reap_regenerable_artifacts``).

    A candidate is removed only when ALL of these hold; anything that errors
    or is unknown means SKIP (fail-closed), and every skip is logged with its
    reason:

      1. It came from ``git worktree list --porcelain`` (never a filesystem walk).
      2. It is not the main working tree and not ``bare``.
      3. Its parent directory is in ``allowed_roots``.
      4. It is not ``detached`` (no branch => no PR to resolve).
      5. It is not ``locked`` (git's own do-not-touch marker).
      6. ``git status --porcelain`` is empty.
      7. ``refs/remotes/origin/<branch>`` resolves AND nothing is unpushed.
      8. The branch is merged — ``git merge-base --is-ancestor`` against the
         freshly-fetched base ref, OR (because this repo squash-merges) a
         merged GitHub PR.
      9. Quiescent: the merge is at least ``min_merge_age_hours`` old AND the
         worktree DIRECTORY's own mtime is older than the same window. The
         directory mtime is used deliberately — a single repo-root
         ``ruff check .`` rewrote ``.ruff_cache`` inside six worktrees at one
         identical instant, so *file* mtimes are not an idleness signal here.
     10. No running process has its cwd inside the tree.

    Returns one record per candidate (``action`` in ``removed`` /
    ``would-remove`` / ``skipped`` / ``failed``) for observability.
    """

    enabled = _env_flag("UA_WORKTREE_PRUNE_ENABLED", False) if enabled is None else enabled
    if not enabled:
        logger.info(
            "Merged-worktree prune disabled (UA_WORKTREE_PRUNE_ENABLED=0); skipping."
        )
        return []

    dry_run = _env_flag("UA_WORKTREE_PRUNE_DRY_RUN", True) if dry_run is None else dry_run
    base_ref = base_ref or (os.getenv("UA_WORKTREE_PRUNE_BASE_REF") or "origin/main").strip()
    min_age_h = min_merge_age_hours if min_merge_age_hours is not None else _min_merge_age_hours()
    now_ts = now if now is not None else time.time()
    merged_at = merged_at or _gh_pr_merged_at

    try:
        repo = (repo_root or detect_repo_root()).resolve()
    except Exception as exc:  # noqa: BLE001 - a timer job must never crash here
        logger.warning("Could not resolve repo root; skipping worktree prune: %s", exc)
        return []

    roots = [Path(r).resolve() for r in (allowed_roots or _prune_roots(repo))]
    logger.info(
        "Merged-worktree prune: repo=%s roots=%s base_ref=%s min_merge_age=%dh dry_run=%s",
        repo, [str(r) for r in roots], base_ref, min_age_h, dry_run,
    )

    # Free bonus: drops administrative metadata for worktrees whose directory
    # is already gone. It can never delete a directory, so it is unconditional.
    pruned = _run(["git", "worktree", "prune", "-v"], cwd=repo, timeout=_GIT_TIMEOUT_S)
    if pruned is not None and pruned.stdout.strip():
        logger.info("git worktree prune removed stale registrations: %s", pruned.stdout.strip())

    # A stale base ref only ever makes branches look LESS merged, but a failed
    # fetch also means we cannot trust ancestry at all — fail closed.
    if fetch and not _fetch_base(repo, base_ref):
        logger.warning("Base ref %s could not be freshened; skipping prune this run.", base_ref)
        return []

    records: list[dict] = []
    out_of_scope = 0
    cutoff = now_ts - min_age_h * 3600

    for wt in list_registered_worktrees(repo_root=repo):
        # 2. never the main working tree, never a bare repo
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

        # 3. allowlist. Anything outside it is not a candidate at all (it
        # belongs to another owner), so it is counted in the summary rather
        # than reported per-entry — 11 of the 19 registered worktrees on the
        # box are out of scope and would drown the useful lines.
        if wt_path.parent not in roots:
            out_of_scope += 1
            logger.debug("worktree-prune out-of-scope (not under an allowed root): %s", wt_path)
            continue

        if wt.prunable:
            _skip(records, wt, f"git reports prunable ({wt.prunable_reason or 'no reason'})")
            continue
        if wt.detached:
            _skip(records, wt, "detached HEAD (no branch => no PR to resolve)")
            continue
        if wt.locked:
            _skip(records, wt, f"locked by git ({wt.lock_reason or 'no reason given'})")
            continue
        if not wt.branch:
            _skip(records, wt, "no branch recorded")
            continue
        if not wt_path.is_dir():
            _skip(records, wt, "worktree directory missing")
            continue

        branch = wt.branch

        # 6. clean
        status = _run(["git", "status", "--porcelain"], cwd=wt_path, timeout=_GIT_TIMEOUT_S)
        if status is None or status.returncode != 0:
            _skip(records, wt, "git status failed")
            continue
        if status.stdout.strip():
            n = len(status.stdout.strip().splitlines())
            _skip(records, wt, f"dirty working tree ({n} changed/untracked entr(ies))")
            continue

        # 7. nothing unpushed. A missing remote-tracking ref is ambiguous
        # (never-pushed vs pruned-after-merge) => skip.
        tracking = f"refs/remotes/origin/{branch}"
        have_ref = _run(
            ["git", "rev-parse", "--verify", "--quiet", tracking],
            cwd=repo, timeout=_GIT_TIMEOUT_S,
        )
        if have_ref is None or have_ref.returncode != 0:
            _skip(records, wt, f"no remote-tracking ref {tracking} (cannot prove pushed)")
            continue
        ahead = _run(
            ["git", "rev-list", "--count", f"{tracking}..{branch}"],
            cwd=repo, timeout=_GIT_TIMEOUT_S,
        )
        if ahead is None or ahead.returncode != 0:
            _skip(records, wt, "could not count unpushed commits")
            continue
        if (ahead.stdout.strip() or "0") != "0":
            _skip(records, wt, f"{ahead.stdout.strip()} unpushed commit(s) vs {tracking}")
            continue

        # 8. merged? ancestry first (cheap, offline), then GitHub PR state.
        merged_ts: Optional[datetime] = None
        anc = _run(
            ["git", "merge-base", "--is-ancestor", wt.head or branch, base_ref],
            cwd=repo, timeout=_GIT_TIMEOUT_S,
        )
        is_ancestor = anc is not None and anc.returncode == 0
        if is_ancestor:
            proof = f"HEAD is an ancestor of {base_ref}"
        else:
            merged_ts = merged_at(branch)
            if merged_ts is None:
                _skip(
                    records, wt,
                    f"not merged (not an ancestor of {base_ref}, and no merged PR found)",
                )
                continue
            proof = f"merged PR (mergedAt={merged_ts.isoformat()})"

        # 9. quiescence. The GitHub mergedAt is authoritative when we have it;
        # on the ancestry path we have no merge timestamp, so the branch tip's
        # commit date stands in as a lower bound (you cannot merge work before
        # you commit it).
        if merged_ts is not None:
            merge_epoch: Optional[float] = merged_ts.timestamp()
            age_label = "merged"
        else:
            committed = _run(
                ["git", "log", "-1", "--format=%ct", wt.head or branch],
                cwd=repo, timeout=_GIT_TIMEOUT_S,
            )
            if committed is None or committed.returncode != 0:
                _skip(records, wt, "could not read branch tip commit date")
                continue
            try:
                merge_epoch = float(committed.stdout.strip())
            except ValueError:
                _skip(records, wt, "unparseable branch tip commit date")
                continue
            age_label = "tip committed"
        if merge_epoch > cutoff:
            age_h = (now_ts - merge_epoch) / 3600
            _skip(records, wt, f"{age_label} only {age_h:.1f}h ago (< {min_age_h}h quiescence)")
            continue
        try:
            dir_mtime = wt_path.stat().st_mtime
        except OSError as exc:
            _skip(records, wt, f"could not stat worktree directory: {exc}")
            continue
        if dir_mtime > cutoff:
            age_h = (now_ts - dir_mtime) / 3600
            _skip(records, wt, f"directory touched {age_h:.1f}h ago (< {min_age_h}h quiescence)")
            continue

        # 10. live process
        if _live_process_inside(wt_path):
            _skip(records, wt, "a running process has its cwd inside this worktree")
            continue

        size_bytes: Optional[int] = None
        try:
            size_bytes = _dir_size_bytes(wt_path)
        except OSError as exc:
            logger.debug("Could not size %s: %s", wt_path, exc)

        if dry_run:
            logger.info(
                "worktree-prune [DRY-RUN] would remove %s (branch=%s, %.1f MiB): %s",
                wt_path, branch, (size_bytes or 0) / (1024 * 1024), proof,
            )
            records.append(
                {
                    "worktree": str(wt_path), "branch": branch,
                    "action": "would-remove", "reason": proof, "size_bytes": size_bytes,
                }
            )
            continue

        # force=False so git independently refuses a dirty/locked tree.
        # `git worktree remove` keeps the branch ref, so this is recoverable.
        removed = teardown_worktree(wt_path, repo_root=repo, force=False)
        if removed:
            logger.info(
                "worktree-prune REMOVED %s (branch=%s, %.1f MiB reclaimed): %s",
                wt_path, branch, (size_bytes or 0) / (1024 * 1024), proof,
            )
            records.append(
                {
                    "worktree": str(wt_path), "branch": branch,
                    "action": "removed", "reason": proof, "size_bytes": size_bytes,
                }
            )
        else:
            logger.warning(
                "worktree-prune FAILED to remove %s (branch=%s); left in place.",
                wt_path, branch,
            )
            records.append(
                {
                    "worktree": str(wt_path), "branch": branch,
                    "action": "failed", "reason": "git worktree remove refused",
                    "size_bytes": size_bytes,
                }
            )

    reclaimed = sum(
        (r.get("size_bytes") or 0)
        for r in records
        if r["action"] in {"removed", "would-remove"}
    )
    logger.info(
        "Merged-worktree prune finished: %d removed, %d would-remove, %d skipped, "
        "%d failed, %d out-of-scope (%.1f MiB%s).",
        sum(1 for r in records if r["action"] == "removed"),
        sum(1 for r in records if r["action"] == "would-remove"),
        sum(1 for r in records if r["action"] == "skipped"),
        sum(1 for r in records if r["action"] == "failed"),
        out_of_scope,
        reclaimed / (1024 * 1024),
        " identified" if dry_run else " reclaimed",
    )
    return records


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    root = _resolve_coder_workspace_root()
    if root is None:
        logger.info("VP-coder profile unavailable (VP disabled?); nothing to prune.")
    else:
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
        logger.info(
            "Hard-deleted %d archived workspace(s) older than %dh.", deleted, delete_after
        )

    # Tier 3 — additive and independent of the VP-coder profile (the worktrees
    # live under the repo, not under the profile root). It must never abort the
    # job: the two tiers above already did their work by this point.
    try:
        prune_merged_worktrees()
    except Exception as exc:  # noqa: BLE001 - a timer job always exits 0
        logger.warning("Merged-worktree prune raised; continuing: %s", exc, exc_info=True)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
