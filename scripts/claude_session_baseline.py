"""Session baseline cleanup for interactive Claude Code sessions.

Called from scripts/_claude_launcher.py right before exec'ing `claude`,
when the launch CWD is the universal_agent git checkout. Restores the
local checkout to a sensible baseline state so every new session lands
on a fresh `main` without manual cleanup.

Behaviour (four cases):

  1. On `main`:                     `git pull --ff-only origin main`
  2. On a merged feature branch:    stash runtime gunk, `git switch main`,
                                    fast-forward, `git branch -D <old>`
  3. On a feature branch with open
     or no PR:                      stay put (work in progress)
  4. Dirty tree with real edits:    stay put (preserve work — never
                                    destructive of uncommitted source)

Best-effort: any unexpected failure prints a warning and falls through so
the operator's Claude session still launches.

Why this exists
---------------
Agent PRs (head=`claude/*`, base=`main`) auto-merge via
`.github/workflows/pr-auto-merge.yml` once `pr-validate.yml` passes. The
remote branch is deleted on merge, but the local checkout stays put on
the now-dead branch. Without this baseline, every new terminal lands on
stale state — sometimes the pre-merge version of files (the trigger for
this work: CLAUDE.md was 40k on a stale branch, 28k on main), and `git
status` shows a mass diff that the operator didn't author.

Single chokepoint: claudereal → claude_with_mcp_env.sh → _claude_launcher.py
→ this module. One call per session. Idempotent.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

HOME_BRANCH = "main"

# Files/dirs the harness rewrites mid-session. Safe to auto-stash when
# we need to switch branches — they regenerate on the next tick.
_RUNTIME_GUNK_PREFIXES: tuple[str, ...] = (
    ".omc/state/",
    "memory/",
    "MEMORY.md",
    "temp/",
)

_GIT_TIMEOUT_SECS = 30
_GH_TIMEOUT_SECS = 15


class _BaselineError(Exception):
    """Internal sentinel — caller catches and prints a warning."""


def _run(
    cmd: list[str],
    cwd: Path,
    *,
    timeout: int = _GIT_TIMEOUT_SECS,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


def _is_ua_repo(cwd: Path, ua_install_root: Path) -> bool:
    try:
        return cwd.resolve() == ua_install_root.resolve() and (cwd / ".git").exists()
    except OSError:
        return False


def _current_branch(cwd: Path) -> str:
    return _run(["git", "branch", "--show-current"], cwd).stdout.strip()


def _current_sha(cwd: Path, ref: str = "HEAD") -> str:
    return _run(["git", "rev-parse", ref], cwd).stdout.strip()[:8]


def _dirty_paths(cwd: Path) -> list[str]:
    """Return list of paths reported by `git status --porcelain`.

    Each entry is the path only (status code stripped). Untracked files
    are included.
    """
    out = _run(["git", "status", "--porcelain"], cwd).stdout.splitlines()
    paths: list[str] = []
    for line in out:
        if len(line) < 4:
            continue
        # porcelain v1 format: XY<space>path
        paths.append(line[3:].split(" -> ")[-1])
    return paths


def _only_runtime_gunk(paths: list[str]) -> bool:
    if not paths:
        return True
    return all(p.startswith(_RUNTIME_GUNK_PREFIXES) for p in paths)


def _branch_pr_state(cwd: Path, branch: str) -> str | None:
    """Return PR state for `branch` per gh, or None if no PR / gh missing.

    Possible return values: "OPEN", "MERGED", "CLOSED", None.
    """
    if shutil.which("gh") is None:
        return None
    try:
        proc = _run(
            ["gh", "pr", "view", branch, "--json", "state", "-q", ".state"],
            cwd,
            timeout=_GH_TIMEOUT_SECS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    state = proc.stdout.strip()
    return state or None


def _remote_branch_exists(cwd: Path, branch: str) -> bool:
    proc = _run(
        ["git", "ls-remote", "--exit-code", "--heads", "origin", branch],
        cwd,
        check=False,
    )
    return proc.returncode == 0


def _switch_to_main_and_cleanup(cwd: Path, old_branch: str) -> tuple[str, str]:
    """Stash runtime gunk, switch to main, FF, delete old local branch.

    Returns (status_emoji, status_message) for the caller to print.
    """
    stashed = False
    if _dirty_paths(cwd):
        # Already verified upstream to be runtime-gunk-only.
        stash_proc = _run(
            ["git", "stash", "push", "-u", "-m", f"baseline-auto: {old_branch}"],
            cwd,
            check=False,
        )
        stashed = stash_proc.returncode == 0 and "No local changes" not in stash_proc.stdout

    _run(["git", "switch", HOME_BRANCH], cwd)
    _run(["git", "pull", "--ff-only", "origin", HOME_BRANCH], cwd)
    _run(["git", "branch", "-D", old_branch], cwd, check=False)
    if stashed:
        _run(["git", "stash", "drop"], cwd, check=False)

    sha = _current_sha(cwd)
    return ("🧹", f"cleaned up merged {old_branch}; on {HOME_BRANCH} @ {sha}")


def run_baseline_check(
    cwd: Path,
    ua_install_root: Path,
    *,
    stream=sys.stderr,
) -> None:
    """Restore the UA checkout to a fresh `main` baseline when safe.

    Prints a single status line on `stream`. Never raises — wraps the
    inner pipeline so a baseline failure can't block the claude launch.
    """
    try:
        _run_baseline_inner(cwd, ua_install_root, stream)
    except subprocess.TimeoutExpired as exc:
        print(f"⚠️  baseline check timed out: {exc.cmd}", file=stream)
    except subprocess.CalledProcessError as exc:
        # Print the git stderr verbatim — it usually tells the operator
        # exactly what's wrong (non-FF, detached HEAD, no upstream, …).
        msg = (exc.stderr or "").strip().splitlines()
        first = msg[0] if msg else f"exit {exc.returncode}"
        print(f"⚠️  baseline check skipped ({first})", file=stream)
    except _BaselineError as exc:
        print(f"ℹ️  {exc}", file=stream)
    except Exception as exc:  # noqa: BLE001 — never block the session
        print(f"⚠️  baseline check skipped: {exc}", file=stream)


def _run_baseline_inner(cwd: Path, ua_install_root: Path, stream) -> None:
    if not _is_ua_repo(cwd, ua_install_root):
        return

    # Reconcile our view of origin (prune removes refs for branches
    # auto-merge already deleted).
    _run(["git", "fetch", "origin", "--prune", "--quiet"], cwd)

    branch = _current_branch(cwd)
    if not branch:
        raise _BaselineError("detached HEAD; staying put")

    if branch == HOME_BRANCH:
        _run(["git", "pull", "--ff-only", "origin", HOME_BRANCH], cwd)
        sha = _current_sha(cwd)
        print(f"✓ on {HOME_BRANCH} @ {sha}", file=stream)
        return

    dirty = _dirty_paths(cwd)
    if dirty and not _only_runtime_gunk(dirty):
        n = len(dirty)
        raise _BaselineError(
            f"on {branch} with {n} uncommitted change(s); staying put"
        )

    state = _branch_pr_state(cwd, branch)
    remote_gone = not _remote_branch_exists(cwd, branch)

    if state == "MERGED" or remote_gone:
        emoji, msg = _switch_to_main_and_cleanup(cwd, branch)
        print(f"{emoji} {msg}", file=stream)
        return

    if state == "OPEN":
        sha = _current_sha(cwd)
        print(
            f"ℹ on {branch} @ {sha} (PR open); staying put",
            file=stream,
        )
        return

    # No PR yet, or gh unavailable, and the remote branch is still alive
    # — operator is mid-task. Stay put.
    sha = _current_sha(cwd)
    suffix = "no PR yet" if state is None else f"PR state={state}"
    print(f"ℹ on {branch} @ {sha} ({suffix}); staying put", file=stream)


if __name__ == "__main__":  # manual smoke test
    install_root = Path(os.environ.get("UA_INSTALL_ROOT", "/opt/universal_agent"))
    run_baseline_check(cwd=Path.cwd(), ua_install_root=install_root)
