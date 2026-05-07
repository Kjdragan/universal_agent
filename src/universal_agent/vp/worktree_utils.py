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
import logging
from pathlib import Path
import shlex
import subprocess

logger = logging.getLogger(__name__)


__all__ = [
    "ArtifactLeakError",
    "RepoNotFoundError",
    "SyntaxCheckResult",
    "WorktreeError",
    "WorktreeProvisionResult",
    "assert_no_artifacts",
    "detect_repo_root",
    "list_changed_py_files",
    "provision_worktree",
    "revert_changed_files",
    "syntax_check_changed_py",
    "teardown_worktree",
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
    base_branch: str = "feature/latest2",
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
) -> bool:
    """Force-remove the worktree. Returns True iff the directory is gone."""

    worktree_path = worktree_path.resolve()
    if not worktree_path.exists():
        return True

    repo = (repo_root or detect_repo_root()).resolve()
    result = subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_path)],
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
