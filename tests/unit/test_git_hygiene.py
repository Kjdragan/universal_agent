"""Guards for scripts/git_hygiene.sh — the automatic checkout-freshness sweep.

This exists because the repo previously only ever *defended* against a stale
local checkout (CLAUDE.md said "branch from origin/main instead";
.claude/hooks/guard-fresh-branch.sh denies branching off a stale base) and never
removed the drift. It reached 339 commits behind origin/main with 35 worktrees
and 413 local branches, and agents then read stale files and drew stale
conclusions from them.

The script runs unattended at session start, so its safety contract is the thing
worth testing: it must never destroy work.
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess

REPO_ROOT = Path(__file__).resolve().parents[2]
HYGIENE = REPO_ROOT / "scripts" / "git_hygiene.sh"
SESSION_START = REPO_ROOT / ".claude" / "hooks" / "session-start.sh"


def _body() -> str:
    """Script text with comment lines stripped, so prose can't satisfy a check."""
    return "\n".join(
        line
        for line in HYGIENE.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


def test_script_exists_and_parses() -> None:
    assert HYGIENE.is_file(), "scripts/git_hygiene.sh is missing"
    proc = subprocess.run(
        ["bash", "-n", str(HYGIENE)], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, f"bash -n failed: {proc.stderr}"


def test_wired_into_session_start_for_all_sessions() -> None:
    """It must run for LOCAL sessions too — that is the checkout that drifts.

    session-start.sh returns early for non-web sessions to skip `uv sync`; the
    hygiene call has to come BEFORE that early return or the desktop checkout
    (the only one that persists between sessions) is never swept.
    """
    text = SESSION_START.read_text()
    assert "git_hygiene.sh" in text, "hygiene is not wired into the SessionStart hook"

    hygiene_at = text.index("git_hygiene.sh")
    remote_gate = text.index('CLAUDE_CODE_REMOTE:-')
    assert hygiene_at < remote_gate, (
        "the hygiene call sits after the web-only early return, so it never runs "
        "on the desktop checkout — which is the one that actually drifts"
    )


def test_hook_falls_back_to_origin_main_when_working_tree_lacks_the_script() -> None:
    """The desktop checkout is routinely PARKED on an old task branch.

    It sat on `fix/scratch-index-dir-links` with 40 dirty files while `main` was
    339 commits ahead. On such a branch `scripts/git_hygiene.sh` is not in the
    working tree at all, so a bare `[ -f ... ]` test silently skips — disabling
    the sweep on exactly the checkout that drifts. The hook must therefore fall
    back to the canonical copy on origin/main.
    """
    text = SESSION_START.read_text()
    assert "origin/main:scripts/git_hygiene.sh" in text, (
        "no origin/main fallback: on a parked checkout the hook would silently "
        "do nothing, which is where the sweep is needed most"
    )
    # Must fetch first, or a cold session compares against a stale origin ref.
    fallback_at = text.index("origin/main:scripts/git_hygiene.sh")
    fetch_at = text.rindex("git fetch", 0, fallback_at)
    assert fetch_at < fallback_at


def test_uses_patch_id_comparison_not_ancestry() -> None:
    """Squash-merge is the repo's merge strategy.

    A merged branch's commits never appear upstream by SHA, so `--is-ancestor`
    and `--merged` both report merged branches as unmerged. `git cherry`
    compares by patch id and gets it right.
    """
    body = _body()
    assert "git cherry" in body
    assert "--is-ancestor" not in body, (
        "ancestry comparison misclassifies every squash-merged branch as unmerged"
    )


def test_deletes_only_branches_with_no_unique_commits() -> None:
    """The delete must be gated on a zero count of '+' lines from git cherry."""
    body = _body()
    assert re.search(r"grep -c\s+'\^\+'", body), (
        "expected a count of '^+' lines (commits NOT upstream) to gate deletion"
    )
    assert "git branch -D" in body
    delete_at = body.index("git branch -D")
    guard_at = body.index("git cherry")
    assert guard_at < delete_at, "the deletion must be guarded by the cherry check"


def test_never_switches_branches_or_discards_work() -> None:
    """Unattended + destructive-verb = the combination that loses work."""
    body = _body()
    forbidden = [
        "git checkout",
        "git switch",
        "git reset --hard",
        "git clean",
        "git stash",
        "git push",
        "--force",
    ]
    for verb in forbidden:
        assert verb not in body, (
            f"{verb!r} in git_hygiene.sh: this script runs unattended at session "
            "start and must never move HEAD or discard uncommitted work"
        )


def test_fast_forward_only() -> None:
    """A merge that isn't --ff-only could create a merge commit on main."""
    body = _body()
    if "git merge" in body:
        assert "--ff-only" in body, "main must only ever be fast-forwarded"


def test_dry_run_makes_no_writes() -> None:
    """--dry-run must route every mutation through the echo path."""
    body = _body()
    assert "DRY_RUN" in body
    assert "--dry-run" in HYGIENE.read_text()
    # The run() helper is the single mutation chokepoint.
    assert re.search(r"run\(\)\s*\{", body), "expected a run() wrapper"


def test_soft_fails_and_is_bounded() -> None:
    """A hygiene failure must never block a session from starting."""
    assert "exit 0" in _body()
    # The hook bounds it so a hung fetch cannot stall session start.
    assert "timeout" in SESSION_START.read_text()
