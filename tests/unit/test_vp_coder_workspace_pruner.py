"""H21: VP-coder workspace pruner resolves the writer's path and hard-deletes
aged archive entries (archiving alone — a same-FS move — frees nothing).

The ``TestPruneMergedWorktrees`` block covers tier 3 (added 2026-07-24): the
merged-only git-worktree prune. It builds REAL temp git repos with REAL
worktrees (matching ``test_vp_worktree_utils.py``'s idiom) because the whole
point of the guard set is what git actually reports — a mocked
``git worktree list`` would prove nothing. The GitHub lookup is the one
dependency that is injected (``merged_at=``), so no test touches the network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import time

import pytest

from universal_agent.scripts.vp_coder_workspace_pruner import (
    _hard_delete_aged_archive,
    _prune_roots,
    _resolve_coder_workspace_root,
    prune_merged_worktrees,
)


def test_resolve_root_matches_writer_default(monkeypatch, tmp_path):
    # With UA_VP_CODER_WORKSPACE_ROOT unset, the pruner must resolve the SAME
    # fallback the writer uses (…/vp_coder_primary_external), not no-op.
    monkeypatch.delenv("UA_VP_CODER_WORKSPACE_ROOT", raising=False)
    monkeypatch.setenv("UA_VP_ENABLED_IDS", "vp.coder.primary")
    root = _resolve_coder_workspace_root()
    assert root is not None
    assert root.name == "vp_coder_primary_external"


def test_hard_delete_removes_only_aged_dirs(tmp_path):
    archive = tmp_path / "vp_coder_primary_external_archive"
    archive.mkdir()
    old = archive / "vp-mission-old"
    new = archive / "vp-mission-new"
    old.mkdir()
    new.mkdir()
    (old / "f.txt").write_text("x")
    (new / "f.txt").write_text("x")
    # Age `old` to 20 days; `new` stays fresh.
    twenty_days_ago = time.time() - 20 * 24 * 3600
    import os
    os.utime(old, (twenty_days_ago, twenty_days_ago))

    deleted = _hard_delete_aged_archive(archive, max_age_hours=14 * 24)  # 14-day grace
    assert deleted == 1
    assert not old.exists()
    assert new.exists()


def test_hard_delete_noop_when_archive_absent(tmp_path):
    assert _hard_delete_aged_archive(tmp_path / "nope", max_age_hours=336) == 0


# ---------------------------------------------------------------------------
# Tier 3 — merged-only git-worktree prune
# ---------------------------------------------------------------------------

_FORTY_EIGHT_HOURS = 48 * 3600


def _git(cwd: Path, *args: str, env: dict | None = None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True,
        env={**os.environ, **env} if env else None,
    )
    return result.stdout.strip()


# Branch work is backdated so the ancestry path's "tip committed" quiescence
# proxy sees an aged commit, the way a really-merged branch would.
_OLD_COMMIT_ENV = {
    "GIT_AUTHOR_DATE": "2026-07-01T12:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-07-01T12:00:00+00:00",
}


@pytest.fixture
def worktree_repo(tmp_path: Path) -> dict:
    """A real repo + real origin + five real worktrees, one per guard case.

    Layout under ``repo/.worktrees/``:

      * ``merged-squash``  — PR squash-merged (tip is NOT an ancestor of
        origin/main; this is what every PR in this repo looks like).
      * ``merged-ff``      — merged by fast-forward, so the tip IS an ancestor.
      * ``open``           — pushed, never merged.
      * ``dirty``          — merged, but has an untracked file.
      * ``unpushed``       — merged, but carries a local-only commit.

    Plus ``outside/stray`` — a merged worktree deliberately parked outside the
    allowlist root.
    """

    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "-q", "-b", "main")

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("base\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")

    def make_worktree(name: str, branch: str, parent: Path) -> Path:
        parent.mkdir(parents=True, exist_ok=True)
        wt = parent / name
        _git(repo, "worktree", "add", "-q", str(wt), "-b", branch, "main")
        (wt / f"{name}.txt").write_text(f"work for {name}\n")
        _git(wt, "add", ".")
        _git(wt, "commit", "-q", "-m", f"work on {name}", env=_OLD_COMMIT_ENV)
        _git(wt, "push", "-q", "origin", branch)
        return wt

    def squash_merge(branch: str) -> None:
        _git(repo, "merge", "--squash", branch)
        _git(repo, "commit", "-q", "-m", f"squash {branch}")
        _git(repo, "push", "-q", "origin", "main")

    roots = repo / ".worktrees"
    wts = {
        "merged_squash": make_worktree("merged-squash", "claude/merged-squash", roots),
        "merged_ff": make_worktree("merged-ff", "claude/merged-ff", roots),
        "open": make_worktree("open", "claude/open", roots),
        "dirty": make_worktree("dirty", "claude/dirty", roots),
        "unpushed": make_worktree("unpushed", "claude/unpushed", roots),
        "outside": make_worktree("stray", "claude/stray", tmp_path / "outside"),
    }

    # Fast-forward merge leaves the tip AS origin/main, i.e. an ancestor.
    _git(repo, "merge", "--ff-only", "claude/merged-ff")
    _git(repo, "push", "-q", "origin", "main")
    # Everything else lands the way this repo really lands PRs: squashed.
    for branch in ("claude/merged-squash", "claude/dirty", "claude/unpushed",
                   "claude/stray"):
        squash_merge(branch)

    # Guard-6 case: an untracked file makes the tree dirty.
    (wts["dirty"] / "scratch.tmp").write_text("in flight\n")
    # Guard-7 case: a local commit that was never pushed.
    (wts["unpushed"] / "later.txt").write_text("not pushed\n")
    _git(wts["unpushed"], "add", ".")
    _git(wts["unpushed"], "commit", "-q", "-m", "local only")

    # Guard-9: age every worktree DIRECTORY past the quiescence window. Done
    # last so no git operation above resets it.
    old = time.time() - _FORTY_EIGHT_HOURS
    for wt in wts.values():
        os.utime(wt, (old, old))

    return {"repo": repo, "origin": origin, "roots": roots, "wts": wts}


def _merged_at_stub(merged_branches: set[str], age_hours: float = 48.0):
    """Injected stand-in for ``_gh_pr_merged_at`` — no network in unit tests."""
    when = datetime.now(timezone.utc) - timedelta(hours=age_hours)

    def _lookup(branch: str):
        return when if branch in merged_branches else None

    return _lookup


def _pr_exists_stub(no_pr_branches: set[str] | None = None, unknown: bool = False):
    """Injected stand-in for ``_gh_pr_exists``.

    Default: every branch HAS a pull request, which is the pre-2026-08-02 world
    and keeps the merged/open cases reading exactly as they always did. Only the
    branches named in ``no_pr_branches`` take the no-PR lane.
    """

    named = no_pr_branches or set()

    def _lookup(branch: str):
        if unknown:
            return None
        return branch not in named

    return _lookup


def _run_prune(worktree_repo: dict, **kwargs) -> dict:
    records = prune_merged_worktrees(
        repo_root=worktree_repo["repo"],
        allowed_roots=[worktree_repo["roots"]],
        enabled=kwargs.pop("enabled", True),
        dry_run=kwargs.pop("dry_run", False),
        merged_at=kwargs.pop(
            "merged_at",
            _merged_at_stub(
                {"claude/merged-squash", "claude/dirty", "claude/unpushed",
                 "claude/stray"}
            ),
        ),
        pr_exists=kwargs.pop("pr_exists", _pr_exists_stub()),
        min_merge_age_hours=kwargs.pop("min_merge_age_hours", 24),
        **kwargs,
    )
    return {r["worktree"]: r for r in records}


class TestPruneMergedWorktrees:
    def test_disabled_by_default(self, worktree_repo, monkeypatch):
        monkeypatch.delenv("UA_WORKTREE_PRUNE_ENABLED", raising=False)
        records = prune_merged_worktrees(
            repo_root=worktree_repo["repo"],
            allowed_roots=[worktree_repo["roots"]],
            merged_at=_merged_at_stub({"claude/merged-squash"}),
        )
        assert records == []
        assert worktree_repo["wts"]["merged_squash"].exists()

    def test_removes_squash_merged_worktree(self, worktree_repo):
        by_path = _run_prune(worktree_repo)
        wt = worktree_repo["wts"]["merged_squash"]
        rec = by_path[str(wt.resolve())]
        assert rec["action"] == "removed", rec
        assert "merged PR" in rec["reason"]
        assert not wt.exists()
        # The branch ref survives — a mistaken removal stays recoverable.
        assert _git(worktree_repo["repo"], "rev-parse", "--verify",
                    "claude/merged-squash")

    def test_removes_fast_forward_merged_worktree_without_github(self, worktree_repo):
        # merged_at returns None for every branch: only the ancestry proof can
        # fire, and it must fire for the fast-forwarded branch.
        by_path = _run_prune(worktree_repo, merged_at=lambda _b: None)
        wt = worktree_repo["wts"]["merged_ff"]
        rec = by_path[str(wt.resolve())]
        assert rec["action"] == "removed", rec
        assert "ancestor" in rec["reason"]
        assert not wt.exists()

    def test_keeps_unmerged_worktree(self, worktree_repo):
        by_path = _run_prune(worktree_repo)
        wt = worktree_repo["wts"]["open"]
        rec = by_path[str(wt.resolve())]
        assert rec["action"] == "skipped"
        assert "not merged" in rec["reason"]
        assert wt.exists()

    def test_keeps_dirty_worktree_even_when_merged(self, worktree_repo):
        by_path = _run_prune(worktree_repo)
        wt = worktree_repo["wts"]["dirty"]
        rec = by_path[str(wt.resolve())]
        assert rec["action"] == "skipped"
        assert "dirty working tree" in rec["reason"]
        assert wt.exists()

    def test_keeps_worktree_with_unpushed_commits(self, worktree_repo):
        by_path = _run_prune(worktree_repo)
        wt = worktree_repo["wts"]["unpushed"]
        rec = by_path[str(wt.resolve())]
        assert rec["action"] == "skipped"
        assert "unpushed commit" in rec["reason"]
        assert wt.exists()

    def test_never_touches_main_worktree(self, worktree_repo):
        by_path = _run_prune(worktree_repo)
        assert str(worktree_repo["repo"].resolve()) not in by_path
        assert (worktree_repo["repo"] / "README.md").exists()

    def test_ignores_worktrees_outside_allowlist(self, worktree_repo):
        by_path = _run_prune(worktree_repo)
        stray = worktree_repo["wts"]["outside"]
        assert str(stray.resolve()) not in by_path
        assert stray.exists()

    def test_respects_quiescence_window(self, worktree_repo):
        # Same fixture, but the merge happened 1h ago: the 24h gate must hold.
        by_path = _run_prune(
            worktree_repo,
            merged_at=_merged_at_stub({"claude/merged-squash"}, age_hours=1.0),
        )
        wt = worktree_repo["wts"]["merged_squash"]
        rec = by_path[str(wt.resolve())]
        assert rec["action"] == "skipped"
        assert "quiescence" in rec["reason"]
        assert wt.exists()

    def test_ancestry_path_still_honours_quiescence(self, worktree_repo):
        # The ancestry proof carries no mergedAt, so the branch tip's commit
        # date is the quiescence lower bound. Widen the window past it and the
        # fast-forwarded worktree must be spared.
        by_path = _run_prune(
            worktree_repo, merged_at=lambda _b: None, min_merge_age_hours=24 * 3650,
        )
        wt = worktree_repo["wts"]["merged_ff"]
        rec = by_path[str(wt.resolve())]
        assert rec["action"] == "skipped"
        assert "tip committed" in rec["reason"]
        assert wt.exists()

    def test_recently_touched_directory_is_skipped(self, worktree_repo):
        wt = worktree_repo["wts"]["merged_squash"]
        os.utime(wt, None)  # touch to "now"
        by_path = _run_prune(worktree_repo)
        rec = by_path[str(wt.resolve())]
        assert rec["action"] == "skipped"
        assert "directory touched" in rec["reason"]
        assert wt.exists()

    def test_locked_worktree_is_skipped(self, worktree_repo):
        wt = worktree_repo["wts"]["merged_squash"]
        _git(worktree_repo["repo"], "worktree", "lock", "--reason",
             "session active", str(wt))
        by_path = _run_prune(worktree_repo)
        rec = by_path[str(wt.resolve())]
        assert rec["action"] == "skipped"
        assert "locked" in rec["reason"]
        assert wt.exists()

    def test_dry_run_reports_but_removes_nothing(self, worktree_repo):
        by_path = _run_prune(worktree_repo, dry_run=True)
        wt = worktree_repo["wts"]["merged_squash"]
        rec = by_path[str(wt.resolve())]
        assert rec["action"] == "would-remove"
        assert rec["size_bytes"] is not None and rec["size_bytes"] > 0
        assert wt.exists()

    def test_every_candidate_is_reported_with_a_reason(self, worktree_repo):
        """Requirement: no silent truncation — every in-scope worktree is
        either removed or skipped WITH a reason."""
        by_path = _run_prune(worktree_repo)
        in_scope = {"merged_squash", "merged_ff", "open", "dirty", "unpushed"}
        for key in in_scope:
            rec = by_path[str(worktree_repo["wts"][key].resolve())]
            assert rec["reason"], f"{key} reported without a reason"
            assert rec["action"] in {"removed", "would-remove", "skipped", "failed"}
        assert len(by_path) == len(in_scope)


def _age_tree(root: Path, age_seconds: float) -> None:
    """Backdate EVERY entry under ``root`` (and ``root`` itself).

    The no-PR lane's second gate reads the newest mtime anywhere beneath the
    tree, so aging the directory alone — which is all the merged lane needs —
    would leave every file looking like it was written a second ago.
    """
    past = time.time() - age_seconds
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames + dirnames:
            target = Path(dirpath) / name
            try:
                os.utime(target, (past, past), follow_symlinks=False)
            except (OSError, NotImplementedError):
                continue
    os.utime(root, (past, past))


def _make_orphan_worktree(
    repo: Path, roots: Path, name: str, *, commit_age_seconds: float,
) -> Path:
    """A worktree whose branch was NEVER pushed and has no PR — the real case.

    Mirrors ``/opt/universal_agent/.claude/worktrees/
    fix-reflection-active-count-excludes-cron`` as measured 2026-08-02: a
    committed, clean checkout on a branch with no remote-tracking ref and no
    pull request, holding a 6.7 GiB ``.venv``.
    """
    roots.mkdir(parents=True, exist_ok=True)
    wt = roots / name
    _git(repo, "worktree", "add", "-q", str(wt), "-b", f"claude/{name}", "main")
    (wt / f"{name}.txt").write_text(f"orphan work for {name}\n")
    # A .venv stand-in: the whole reason this tree matters on disk. Gitignored,
    # as the real repo does — otherwise it alone would make the tree dirty and
    # the clean-tree gate (which applies to both lanes) would skip it forever.
    (wt / ".gitignore").write_text(".venv/\n")
    (wt / ".venv" / "lib").mkdir(parents=True)
    (wt / ".venv" / "lib" / "big.bin").write_bytes(b"\x00" * 1024)
    _git(wt, "add", f"{name}.txt", ".gitignore")
    when = f"@{int(time.time() - commit_age_seconds)} +0000"
    _git(wt, "commit", "-q", "-m", f"orphan {name}",
         env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})
    return wt


_THIRTY_DAYS = 30 * 24 * 3600
_TWO_DAYS = 2 * 24 * 3600


class TestNoPrAgeOutLane:
    """The third branch state (2026-08-02): no PR was EVER opened.

    Previously indistinguishable from "a PR is open" (both are a ``None`` from
    ``gh_pr_merged_at``) and therefore skipped forever, which is how an orphan
    worktree holding a 6.7 GiB ``.venv`` survived every run of both cleanup
    jobs with the VPS at 90.0% disk.
    """

    def _orphan(self, worktree_repo: dict, *, tree_age: float,
                commit_age: float = _THIRTY_DAYS) -> Path:
        wt = _make_orphan_worktree(
            worktree_repo["repo"], worktree_repo["roots"], "orphan",
            commit_age_seconds=commit_age,
        )
        _age_tree(wt, tree_age)
        return wt

    def test_old_no_pr_worktree_is_pruned(self, worktree_repo, caplog):
        caplog.set_level("INFO")
        wt = self._orphan(worktree_repo, tree_age=_THIRTY_DAYS)
        by_path = _run_prune(
            worktree_repo, pr_exists=_pr_exists_stub({"claude/orphan"}),
        )
        rec = by_path[str(wt.resolve())]
        assert rec["action"] == "removed", rec
        assert rec["lane"] == "no-pr"
        assert "no PR ever opened" in rec["reason"]
        assert not wt.exists()
        # Loud logging: branch, SHA and age evidence all present.
        assert "NO-PR AGE-OUT" in caplog.text
        assert "claude/orphan" in caplog.text
        # The commits survive — a mistaken removal costs a `git worktree add`.
        assert _git(worktree_repo["repo"], "rev-parse", "--verify", "claude/orphan")

    def test_no_pr_worktree_younger_than_threshold_is_kept(self, worktree_repo):
        wt = self._orphan(worktree_repo, tree_age=_TWO_DAYS)
        by_path = _run_prune(
            worktree_repo, pr_exists=_pr_exists_stub({"claude/orphan"}),
        )
        rec = by_path[str(wt.resolve())]
        assert rec["action"] == "skipped"
        assert "no PR, but directory touched" in rec["reason"]
        assert "7d age-out window" in rec["reason"]
        assert wt.exists()

    def test_no_pr_worktree_with_a_live_process_is_kept(self, worktree_repo):
        """Age alone never prunes: the liveness gate is proved with a REAL
        process, and it is the ``deep=True`` variant so a venv held open by
        exe/mmap counts even when no cwd points into the tree."""
        wt = self._orphan(worktree_repo, tree_age=_THIRTY_DAYS)
        proc = subprocess.Popen(["sleep", "45"], cwd=str(wt))
        try:
            by_path = _run_prune(
                worktree_repo, pr_exists=_pr_exists_stub({"claude/orphan"}),
            )
        finally:
            proc.terminate()
            proc.wait(timeout=10)
        rec = by_path[str(wt.resolve())]
        assert rec["action"] == "skipped"
        assert "running process" in rec["reason"]
        assert wt.exists()

    def test_recent_content_beneath_an_old_directory_is_kept(self, worktree_repo):
        """The worktree root's mtime does not move when a session edits a file
        three levels down — the harness's own agent worktrees live exactly
        there, so the newest-content gate is what sees them."""
        wt = self._orphan(worktree_repo, tree_age=_THIRTY_DAYS)
        nested = wt / "src" / "deep"
        nested.mkdir(parents=True)
        (nested / "active.py").write_text("# committed a moment ago\n")
        # Committed with an OLD date, so the tree is clean and the tip-commit
        # gate passes: only the file's own mtime says somebody is still here.
        old = f"@{int(time.time() - _THIRTY_DAYS)} +0000"
        _git(wt, "add", "src")
        _git(wt, "commit", "-q", "-m", "nested",
             env={"GIT_AUTHOR_DATE": old, "GIT_COMMITTER_DATE": old})
        os.utime(nested / "active.py", None)  # ...and it was just touched
        os.utime(wt, (time.time() - _THIRTY_DAYS,) * 2)  # root still looks old

        by_path = _run_prune(
            worktree_repo, pr_exists=_pr_exists_stub({"claude/orphan"}),
        )
        rec = by_path[str(wt.resolve())]
        assert rec["action"] == "skipped"
        assert "content touched" in rec["reason"]
        assert wt.exists()

    def test_recent_tip_commit_blocks_the_prune(self, worktree_repo):
        wt = self._orphan(
            worktree_repo, tree_age=_THIRTY_DAYS, commit_age=3600.0,
        )
        by_path = _run_prune(
            worktree_repo, pr_exists=_pr_exists_stub({"claude/orphan"}),
        )
        rec = by_path[str(wt.resolve())]
        assert rec["action"] == "skipped"
        assert "branch tip committed" in rec["reason"]
        assert wt.exists()

    def test_open_pr_worktree_is_never_aged_out(self, worktree_repo):
        """Work in flight is not ours to delete at ANY age."""
        wt = self._orphan(worktree_repo, tree_age=_THIRTY_DAYS)
        by_path = _run_prune(worktree_repo, pr_exists=_pr_exists_stub())
        rec = by_path[str(wt.resolve())]
        assert rec["action"] == "skipped"
        assert "a PR exists for this branch" in rec["reason"]
        assert wt.exists()

    def test_unknown_pr_state_is_fail_closed(self, worktree_repo):
        wt = self._orphan(worktree_repo, tree_age=_THIRTY_DAYS)
        by_path = _run_prune(
            worktree_repo, pr_exists=_pr_exists_stub(unknown=True),
        )
        rec = by_path[str(wt.resolve())]
        assert rec["action"] == "skipped"
        assert "could not determine whether a PR exists" in rec["reason"]
        assert wt.exists()

    def test_lane_can_be_switched_off(self, worktree_repo):
        wt = self._orphan(worktree_repo, tree_age=_THIRTY_DAYS)
        by_path = _run_prune(
            worktree_repo,
            pr_exists=_pr_exists_stub({"claude/orphan"}),
            no_pr_enabled=False,
        )
        rec = by_path[str(wt.resolve())]
        assert rec["action"] == "skipped"
        assert "no-PR lane disabled" in rec["reason"]
        assert wt.exists()

    def test_dirty_no_pr_worktree_is_kept(self, worktree_repo):
        """Gate 6 applies to BOTH lanes: uncommitted work is never deleted."""
        wt = self._orphan(worktree_repo, tree_age=_THIRTY_DAYS)
        (wt / "scratch.tmp").write_text("in flight\n")
        os.utime(wt, (time.time() - _THIRTY_DAYS,) * 2)
        by_path = _run_prune(
            worktree_repo, pr_exists=_pr_exists_stub({"claude/orphan"}),
        )
        rec = by_path[str(wt.resolve())]
        assert rec["action"] == "skipped"
        assert "dirty working tree" in rec["reason"]
        assert wt.exists()

    def test_env_tunable_threshold(self, worktree_repo, monkeypatch):
        monkeypatch.setenv("UA_WORKTREE_PRUNE_NO_PR_MIN_AGE_DAYS", "60")
        wt = self._orphan(worktree_repo, tree_age=_THIRTY_DAYS)
        by_path = _run_prune(
            worktree_repo, pr_exists=_pr_exists_stub({"claude/orphan"}),
        )
        rec = by_path[str(wt.resolve())]
        assert rec["action"] == "skipped"
        assert "60d age-out window" in rec["reason"]
        assert wt.exists()


class TestPruneRoots:
    def test_defaults_to_both_worktree_dirs(self, monkeypatch, tmp_path):
        """Regression: defaulting to ``.worktrees`` alone left the harness's own
        ``.claude/worktrees`` — the 6.7 GiB blind spot — permanently
        out-of-scope for BOTH cleanup jobs."""
        monkeypatch.delenv("UA_WORKTREE_PRUNE_ROOTS", raising=False)
        assert _prune_roots(tmp_path) == [
            (tmp_path / ".worktrees").resolve(),
            (tmp_path / ".claude" / "worktrees").resolve(),
        ]

    def test_env_override_is_colon_separated(self, monkeypatch, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        monkeypatch.setenv("UA_WORKTREE_PRUNE_ROOTS", f"{a}:{b}")
        assert _prune_roots(tmp_path) == [a.resolve(), b.resolve()]
