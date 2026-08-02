"""Reusable worktree + syntax-check helpers for autonomous (tier-2) missions.

Implements the building blocks of the contract documented in
``docs/deployment/ai_coder_instructions.md`` ("Autonomous Mission Workflow").
The 2026-05-07 incident (CODIE proactive cleanup mangled
``durable/state.py`` and never reverted) is the regression these helpers
are designed to make impossible:

* Patches must run inside an isolated worktree, never on the deployed tree.
* Every modified ``.py`` file must compile() before commit.
* Backup/swap/orig artifacts must never reach a PR.

The ``vp/worker_loop.py`` already runs a worktree path for
``vp.coder.primary``; this module is the standalone, testable distillation
of that pattern that other call sites (CODIE cleanup, Cody scaffold-builder
follow-ups, future autonomous patchers) can compose with.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import shlex
import subprocess
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


__all__ = [
    "ArtifactLeakError",
    "GH_TIMEOUT_S",
    "GIT_TIMEOUT_S",
    "RegisteredWorktree",
    "RepoNotFoundError",
    "SyntaxCheckResult",
    "WorktreeError",
    "WorktreeProvisionResult",
    "assert_no_artifacts",
    "detect_repo_root",
    "gh_pr_exists",
    "gh_pr_merged_at",
    "list_changed_py_files",
    "list_registered_worktrees",
    "live_process_inside",
    "provision_worktree",
    "revert_changed_files",
    "run_git",
    "syntax_check_changed_py",
    "teardown_worktree",
    "tracked_artifact_dirs",
    "worktree_prune_roots",
]


_ARTIFACT_GLOBS: tuple[str, ...] = ("*.py.bak", "*.py.orig", "*.swp", "*.bak", "*.orig")


class WorktreeError(RuntimeError):
    """Raised for worktree provisioning / teardown failures we cannot recover from."""


class RepoNotFoundError(WorktreeError):
    """Raised when no enclosing git repository can be found."""


class ArtifactLeakError(WorktreeError):
    """Raised when a worktree contains banned artifacts (.bak / .swp / .orig).

    These artifacts trip the ``pr-validate.yml`` tripwire, so we fail fast
    locally rather than letting CI reject the PR.
    """


@dataclass(frozen=True)
class WorktreeProvisionResult:
    """Outcome of ``provision_worktree``."""

    worktree_path: Path
    branch: str
    base_branch: str
    succeeded: bool
    error: str | None = None


@dataclass(frozen=True)
class SyntaxCheckResult:
    """Outcome of ``syntax_check_changed_py``."""

    ok: bool
    failures: list[tuple[Path, str]] = field(default_factory=list)


@dataclass(frozen=True)
class RegisteredWorktree:
    """One record from ``git worktree list --porcelain``.

    ``is_main`` marks the repository's main working tree — git always emits it
    first, and it is the one entry no cleanup job may ever remove.
    """

    path: Path
    head: str | None
    #: Short branch name (``refs/heads/`` stripped); ``None`` when detached.
    branch: str | None
    detached: bool
    bare: bool
    locked: bool
    lock_reason: str | None
    prunable: bool
    prunable_reason: str | None
    is_main: bool


def detect_repo_root(start: Path | None = None) -> Path:
    """Return the git working-tree root that contains ``start``.

    Raises :class:`RepoNotFoundError` when ``start`` is not inside a git tree.
    Resolves symlinks so callers always get a canonical absolute path.
    """

    candidate = (start or Path.cwd()).resolve()
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(candidate),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RepoNotFoundError(
            f"git rev-parse --show-toplevel failed inside {candidate}: "
            f"{result.stderr.strip() or 'no output'}"
        )
    return Path(result.stdout.strip()).resolve()


def list_registered_worktrees(repo_root: Path | None = None) -> list[RegisteredWorktree]:
    """Enumerate the worktrees git itself knows about.

    Parses ``git worktree list --porcelain`` (blank-line separated records;
    ``worktree`` / ``HEAD`` / ``branch`` / ``detached`` / ``bare`` / ``locked``
    / ``prunable`` attributes). This is the ONLY enumeration source any cleanup
    job should use — walking the filesystem would let a job delete a directory
    git has no registration for.

    Returns an empty list (never raises) when git fails, so a caller running on
    a timer degrades to "prune nothing".
    """

    repo = (repo_root or detect_repo_root()).resolve()
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning(
            "list_registered_worktrees: git worktree list failed in %s: %s",
            repo, (result.stderr or result.stdout).strip(),
        )
        return []

    out: list[RegisteredWorktree] = []
    record: dict[str, object] = {}

    def _flush() -> None:
        raw_path = record.get("worktree")
        if not raw_path:
            return
        raw_branch = record.get("branch")
        branch = str(raw_branch).removeprefix("refs/heads/") if raw_branch else None
        out.append(
            RegisteredWorktree(
                path=Path(str(raw_path)),
                head=record.get("HEAD"),  # type: ignore[arg-type]
                branch=branch,
                detached=bool(record.get("detached")),
                bare=bool(record.get("bare")),
                locked="locked" in record,
                lock_reason=(record.get("locked") or None),  # type: ignore[arg-type]
                prunable="prunable" in record,
                prunable_reason=(record.get("prunable") or None),  # type: ignore[arg-type]
                # git documents the main working tree as the first record.
                is_main=not out,
            )
        )
        record.clear()

    for line in result.stdout.splitlines():
        if not line.strip():
            _flush()
            continue
        key, _, value = line.partition(" ")
        if key in {"detached", "bare"}:
            record[key] = True
        else:
            record[key] = value.strip()
    _flush()

    return out


# ---------------------------------------------------------------------------
# Shared cleanup helpers
#
# Used by BOTH unattended worktree jobs: the WEEKLY merged-worktree prune
# (``scripts/vp_coder_workspace_pruner.py::prune_merged_worktrees``) and the
# DAILY worktree regenerable reap
# (``scripts/vp_coder_regenerable_reaper.py::reap_worktree_regenerable_artifacts``).
# They live here rather than in either script because the pruner already
# imports ``_dir_size_bytes`` FROM the reaper — putting a shared helper in
# either script makes the other's import circular. One copy also means the two
# jobs can never drift about scope (``worktree_prune_roots``) or liveness
# (``live_process_inside``), which is exactly how two halves of a cleanup
# system start fighting each other.
# ---------------------------------------------------------------------------

#: Subprocess budget for git/gh calls made by unattended cleanup jobs. A hung
#: network call must never wedge a oneshot systemd unit.
GIT_TIMEOUT_S = 60
GH_TIMEOUT_S = 30


def run_git(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: int = GIT_TIMEOUT_S,
) -> Optional[subprocess.CompletedProcess]:
    """Run a git command, returning ``None`` on timeout/OSError (never raising).

    ``None`` means "we could not find out" — every caller treats that as a
    fail-closed SKIP rather than as a negative answer.
    """

    try:
        return subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Command failed (%s): %s", " ".join(cmd), exc)
        return None


#: The two directories that hold this estate's registered worktrees. BOTH are
#: defaults (2026-08-02): ``.worktrees`` is where ``vp/worker_loop.py`` and
#: hand-rolled ``git worktree add`` calls land, while ``.claude/worktrees`` is
#: where the Claude Code harness puts every agent worktree it spawns. Defaulting
#: to ``.worktrees`` alone is how a 6.7 GiB ``.venv`` (torch + nvidia + triton)
#: sat untouched under ``.claude/worktrees`` while BOTH cleanup jobs enumerated
#: its worktree and discarded it as out-of-scope, and the daily reaper logged
#: "Reaped 0 … 0.00 MiB" three runs running with the disk at 90.0%.
_DEFAULT_PRUNE_ROOT_RELPATHS: tuple[str, ...] = (".worktrees", ".claude/worktrees")


def worktree_prune_roots(repo_root: Path) -> list[Path]:
    """Directories whose CHILDREN are prunable worktrees (the allowlist).

    Defaults to BOTH ``<repo>/.worktrees`` AND ``<repo>/.claude/worktrees``
    (see ``_DEFAULT_PRUNE_ROOT_RELPATHS``); override with a colon-separated
    ``UA_WORKTREE_PRUNE_ROOTS``. This allowlist is load-bearing, not
    decoration: nine registered worktrees live under
    ``AGENT_RUN_WORKSPACES/vp_coder_primary_external/**``, a subtree already
    owned by the VP-coder-profile jobs, and a worktree job that enumerated
    every registration without a path allowlist would race their
    ``shutil.move`` / ``shutil.rmtree``.

    Widening the DEFAULT does not widen what gets deleted on its own — every
    per-worktree guard in both consumers still has to pass. It only stops the
    two jobs from silently declaring the harness's own worktree directory
    somebody else's problem.

    Both worktree jobs read the SAME env var deliberately: a daily reaper and a
    weekly pruner that disagreed about scope would be much worse than either
    being slightly too narrow.
    """

    raw = (os.getenv("UA_WORKTREE_PRUNE_ROOTS") or "").strip()
    defaults = [(repo_root / rel).resolve() for rel in _DEFAULT_PRUNE_ROOT_RELPATHS]
    if not raw:
        return defaults
    roots: list[Path] = []
    for part in raw.split(":"):
        part = part.strip()
        if part:
            roots.append(Path(part).expanduser().resolve())
    return roots or defaults


def live_process_inside(path: Path, *, deep: bool = False) -> bool:
    """True when a running process is using ``path``.

    ``cwd`` is the baseline signal: ``vp/clients/claude_cli_client.py`` launches
    the CLI with ``cwd=str(workspace_dir)``, so a live mission sits inside its
    own tree. ``deep=True`` additionally inspects ``/proc/<pid>/exe`` and
    ``/proc/<pid>/maps``, because a process *running* an interpreter from a
    tree's ``.venv`` (or mmapping a shared object out of it) is bound by
    exe/maps, not by cwd — and such a process may well have been started from
    somewhere else entirely.

    Unreadable ``/proc`` entries are ignored (a process we cannot inspect is
    almost always another user's, i.e. not one of ours).

    KNOWN GAP — do not mistake a False for proof of idleness: interactive agent
    sessions on the production box run with ``cwd=/opt/universal_agent`` even
    while editing a worktree through absolute paths, so a live editing session
    is invisible here. Callers must pair this with a long quiescence window.
    """

    proc = Path("/proc")
    if not proc.is_dir():
        return False
    target = str(path.resolve())
    prefix = target + os.sep
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        links = ("cwd", "exe") if deep else ("cwd",)
        for link in links:
            try:
                dest = os.readlink(entry / link)
            except OSError:
                continue
            if dest == target or dest.startswith(prefix):
                logger.info(
                    "Live process %s has %s inside %s", entry.name, link, path
                )
                return True
        if deep:
            try:
                mapped = (entry / "maps").read_text(errors="ignore")
            except OSError:
                continue
            if prefix in mapped:
                logger.info(
                    "Live process %s maps a file inside %s", entry.name, path
                )
                return True
    return False


def _gh_env() -> dict[str, str]:
    """Minimal env allow-list for spawning ``gh``.

    ``gh`` is a third-party binary, i.e. a trust boundary: the process env of
    these units carries Infisical-resolved secrets it has no business reading,
    so it gets an explicit allow-list rather than the inherited environment.
    """

    allowed = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "XDG_CONFIG_HOME",
               "GH_HOST", "GH_TOKEN", "GITHUB_TOKEN")
    return {k: v for k, v in os.environ.items() if k in allowed and v}


def gh_pr_exists(branch: str) -> Optional[bool]:
    """Whether ANY pull request (any state) was ever opened for ``branch``.

    Returns ``True`` (at least one PR exists — open, closed, or merged),
    ``False`` (GitHub definitively knows of none), or ``None`` (we could not
    find out: non-zero exit, timeout, unparseable JSON). Callers MUST treat
    ``None`` as "do not act".

    WHY THIS EXISTS (2026-08-02): ``gh_pr_merged_at`` returns ``None`` for two
    very different worlds — "a PR is open, work in flight, hands off" and "no
    PR was ever opened, this branch exists only in somebody's abandoned
    worktree". Collapsing them made the weekly prune skip the second case
    forever, which is how an orphan worktree holding a 6.7 GiB ``.venv`` (its
    branch on no remote, no PR, its commit reachable from nothing else)
    survived every run of both cleanup jobs. This is the query that tells them
    apart; the ``False`` answer is the ONLY one that unlocks the age-out lane,
    and it unlocks it only alongside that lane's own age/liveness gates.
    """

    repo = os.getenv("UA_GH_REPO", "Kjdragan/universal_agent")
    cmd = [
        "gh", "pr", "list", "--repo", repo, "--head", branch,
        "--state", "all", "--limit", "10", "--json", "number,state",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=GH_TIMEOUT_S, env=_gh_env(),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("gh pr list (state=all) failed for %s: %s", branch, exc)
        return None
    if result.returncode != 0:
        logger.warning(
            "gh pr list (state=all) returned %d for %s: %s",
            result.returncode, branch, (result.stderr or "").strip(),
        )
        return None
    try:
        rows = json.loads(result.stdout or "[]")
    except ValueError as exc:
        logger.warning(
            "gh pr list (state=all) emitted unparseable JSON for %s: %s", branch, exc
        )
        return None
    if not isinstance(rows, list):
        logger.warning("gh pr list (state=all) returned a non-list for %s", branch)
        return None
    return bool(rows)


def gh_pr_merged_at(branch: str) -> Optional[datetime]:
    """Return the merge timestamp of ``branch``'s PR, or ``None``.

    Fail-closed: ANY non-zero exit, timeout, empty result, or missing
    ``mergedAt`` returns ``None``. What ``None`` *means* differs by caller —
    the weekly prune reads it as "not provably merged, leave the tree alone",
    the daily reap reads it as "not merged, so this open tree is mine" — which
    is why both keep their own additional guards. Neither may read it as "no PR
    exists"; that question has its own query (``gh_pr_exists``).

    ``--state merged`` also excludes closed-but-unmerged branches, whose trees
    are deliberately left alone.
    """

    env = _gh_env()
    repo = os.getenv("UA_GH_REPO", "Kjdragan/universal_agent")
    cmd = [
        "gh", "pr", "list", "--repo", repo, "--head", branch,
        "--state", "merged", "--json", "number,mergedAt",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=GH_TIMEOUT_S, env=env,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("gh pr list failed for %s: %s", branch, exc)
        return None
    if result.returncode != 0:
        logger.warning(
            "gh pr list returned %d for %s: %s",
            result.returncode, branch, (result.stderr or "").strip(),
        )
        return None

    try:
        rows = json.loads(result.stdout or "[]")
    except ValueError as exc:
        logger.warning("gh pr list emitted unparseable JSON for %s: %s", branch, exc)
        return None
    if not isinstance(rows, list) or len(rows) != 1:
        logger.info(
            "gh reports %d merged PR(s) for %s; treating as not-merged.",
            len(rows) if isinstance(rows, list) else -1, branch,
        )
        return None
    raw = (rows[0] or {}).get("mergedAt")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Unparseable mergedAt %r for %s", raw, branch)
        return None


def tracked_artifact_dirs(
    tree_root: Path,
    names: Iterable[str],
) -> Optional[set[str]]:
    """Relative dir paths under ``tree_root`` that git TRACKS content inside.

    Returns POSIX-relative paths (e.g. ``"node_modules"``, ``"web/dist"``)
    whose final component is in ``names`` and under which at least one tracked
    file exists. ``set()`` means "nothing tracked" (including: ``tree_root`` is
    not inside a git work tree at all, so nothing under it *can* be tracked).
    ``None`` means "could not determine" — the caller MUST skip.

    WHY THIS EXISTS (measured 2026-07-25): this repo tracks exactly one path
    under a regenerable name —
    ``node_modules/.vite/vitest/<sha>/results.json`` — so every full checkout
    has a *tracked* ``node_modules/``. Reaping it by name deletes tracked
    source and leaves ` D` in ``git status``, which (a) is data loss, however
    small, and (b) permanently disqualifies that worktree from the weekly
    merged-worktree prune, whose clean-tree guard would then never pass. One
    ``git ls-files`` per tree is the whole fix.
    """

    wanted = {n for n in names if n}
    if not wanted:
        return set()

    inside = run_git(
        ["git", "rev-parse", "--is-inside-work-tree"], cwd=tree_root
    )
    if inside is None:
        return None  # timeout / OSError => unknown => caller skips
    if inside.returncode != 0:
        return set()  # not a git work tree: nothing here can be tracked

    listed = run_git(["git", "ls-files", "-z"], cwd=tree_root)
    if listed is None or listed.returncode != 0:
        return None

    out: set[str] = set()
    for entry in listed.stdout.split("\0"):
        if not entry:
            continue
        parts = entry.split("/")
        for idx, part in enumerate(parts[:-1]):
            if part in wanted:
                out.add("/".join(parts[: idx + 1]))
    return out


def _safe_segment(value: str) -> str:
    """Sanitize a string into a path/branch-safe slug.

    ``..`` and ``/`` are replaced with ``_`` so a malicious or
    accidentally-templated task_id cannot escape the workspace_root.
    """

    cleaned = value.replace("..", "_").replace("/", "_").replace("\\", "_").strip()
    return cleaned or "task"


def provision_worktree(
    *,
    bot_name: str,
    task_id: str,
    base_branch: str = "origin/main",
    repo_root: Path | None = None,
    workspace_root: Path | None = None,
) -> WorktreeProvisionResult:
    """Create a fresh worktree on ``<bot>/<task_id>`` based on ``base_branch``.

    Workspace path defaults to ``/tmp/<bot>_<task_id>`` (matching the
    contract example in ``ai_coder_instructions.md``). When the branch
    already exists locally we fall back to ``git worktree add <path>
    <branch>`` (without ``-b``) so re-runs of the same task pick the
    existing branch instead of failing.
    """

    repo = (repo_root or detect_repo_root()).resolve()
    bot_slug = _safe_segment(bot_name)
    task_slug = _safe_segment(task_id)
    branch = f"{bot_slug}/{task_slug}"

    base = workspace_root or Path("/tmp")
    base = base.resolve()
    base.mkdir(parents=True, exist_ok=True)
    worktree_path = (base / f"{bot_slug}_{task_slug}").resolve()

    logger.info(
        "provision_worktree: repo=%s base_branch=%s branch=%s worktree=%s",
        repo, base_branch, branch, worktree_path,
    )

    if worktree_path.exists():
        return WorktreeProvisionResult(
            worktree_path=worktree_path,
            branch=branch,
            base_branch=base_branch,
            succeeded=False,
            error=f"worktree path already exists: {worktree_path}",
        )

    result = subprocess.run(
        ["git", "worktree", "add", str(worktree_path), "-b", branch, base_branch],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and "already exists" in (result.stderr or ""):
        result = subprocess.run(
            ["git", "worktree", "add", str(worktree_path), branch],
            cwd=str(repo),
            capture_output=True,
            text=True,
        )

    if result.returncode != 0:
        return WorktreeProvisionResult(
            worktree_path=worktree_path,
            branch=branch,
            base_branch=base_branch,
            succeeded=False,
            error=(result.stderr or result.stdout).strip(),
        )

    return WorktreeProvisionResult(
        worktree_path=worktree_path,
        branch=branch,
        base_branch=base_branch,
        succeeded=True,
    )


def teardown_worktree(
    worktree_path: Path,
    *,
    repo_root: Path | None = None,
    force: bool = True,
) -> bool:
    """Remove the worktree. Returns True iff the directory is gone.

    ``force=True`` (the default, preserving every existing caller) passes
    ``--force`` so a dirty mission workspace is still torn down. Unattended
    cleanup jobs should pass ``force=False``: git then refuses to remove a
    dirty or locked worktree on its own, which is a second safety net
    independent of whatever checks the caller ran.

    Note ``git worktree remove`` deletes only the checkout and the admin
    files — the branch ref (and every commit on it) survives, which is what
    makes a mistaken removal recoverable.
    """

    worktree_path = worktree_path.resolve()
    if not worktree_path.exists():
        return True

    repo = (repo_root or detect_repo_root()).resolve()
    cmd = ["git", "worktree", "remove"]
    if force:
        cmd.append("--force")
    cmd.append(str(worktree_path))
    result = subprocess.run(
        cmd,
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning(
            "teardown_worktree: git worktree remove failed for %s: %s",
            worktree_path, (result.stderr or result.stdout).strip(),
        )
    return not worktree_path.exists()


def syntax_check_changed_py(
    worktree_path: Path,
    changed_files: list[Path],
) -> SyntaxCheckResult:
    """Compile every ``*.py`` in ``changed_files`` (relative to ``worktree_path``).

    Returns a :class:`SyntaxCheckResult` with the exception text for each
    failing file. Non-Python files are ignored. Missing files are reported
    as failures so a buggy ``patch_fn`` cannot silently skip them.
    """

    worktree_path = worktree_path.resolve()
    failures: list[tuple[Path, str]] = []

    for raw in changed_files:
        path = raw if raw.is_absolute() else (worktree_path / raw)
        path = path.resolve()
        if path.suffix != ".py":
            continue
        if not path.exists():
            failures.append((path, "file does not exist"))
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            failures.append((path, f"unicode-decode-error: {exc}"))
            continue
        try:
            compile(source, str(path), "exec")
        except SyntaxError as exc:
            failures.append((path, f"SyntaxError: {exc.msg} (line {exc.lineno})"))
        except Exception as exc:  # pragma: no cover - defensive
            failures.append((path, f"{type(exc).__name__}: {exc}"))

    return SyntaxCheckResult(ok=not failures, failures=failures)


def revert_changed_files(
    worktree_path: Path,
    files: list[Path],
) -> bool:
    """Run ``git checkout HEAD -- <files>`` inside the worktree.

    Returns True if every file was reverted (or there were none to revert).
    Files outside the worktree are filtered out; absolute paths are
    converted to worktree-relative form.
    """

    worktree_path = worktree_path.resolve()
    if not files:
        return True

    relative: list[str] = []
    for raw in files:
        path = raw.resolve() if raw.is_absolute() else (worktree_path / raw).resolve()
        try:
            rel = path.relative_to(worktree_path)
        except ValueError:
            logger.warning(
                "revert_changed_files: skipping out-of-worktree path %s", path,
            )
            continue
        relative.append(str(rel))

    if not relative:
        return True

    cmd = ["git", "checkout", "HEAD", "--", *relative]
    logger.info(
        "revert_changed_files: cwd=%s cmd=%s", worktree_path, shlex.join(cmd),
    )
    result = subprocess.run(
        cmd, cwd=str(worktree_path), capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.warning(
            "revert_changed_files: checkout failed: %s",
            (result.stderr or result.stdout).strip(),
        )
        return False
    return True


def assert_no_artifacts(worktree_path: Path) -> None:
    """Raise :class:`ArtifactLeakError` when banned artifacts are present.

    Mirrors the tripwire in ``.github/workflows/pr-validate.yml`` so a
    mission fails locally before pushing instead of being rejected by CI.
    """

    worktree_path = worktree_path.resolve()
    leaked: list[Path] = []
    for pattern in _ARTIFACT_GLOBS:
        leaked.extend(p for p in worktree_path.rglob(pattern) if p.is_file())

    if leaked:
        rendered = ", ".join(str(p.relative_to(worktree_path)) for p in leaked[:10])
        raise ArtifactLeakError(
            f"banned editor/backup artifacts present in {worktree_path}: {rendered}"
            + ("" if len(leaked) <= 10 else f" (+{len(leaked) - 10} more)")
        )


def list_changed_py_files(worktree_path: Path) -> list[Path]:
    """Return every ``.py`` path that differs from HEAD inside the worktree.

    Useful as a fallback when ``patch_fn`` does not return its own
    change-set. Includes both staged and unstaged modifications, plus
    untracked files.
    """

    worktree_path = worktree_path.resolve()
    out: list[Path] = []

    tracked = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=str(worktree_path), capture_output=True, text=True,
    )
    if tracked.returncode == 0:
        for line in tracked.stdout.splitlines():
            line = line.strip()
            if line.endswith(".py"):
                out.append((worktree_path / line).resolve())

    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=str(worktree_path), capture_output=True, text=True,
    )
    if untracked.returncode == 0:
        for line in untracked.stdout.splitlines():
            line = line.strip()
            if line.endswith(".py"):
                out.append((worktree_path / line).resolve())

    return out
