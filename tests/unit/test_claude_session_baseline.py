"""Unit tests for scripts/claude_session_baseline.py.

Covers the four documented behaviours of run_baseline_check():

  1. On `main` → `git pull --ff-only` runs, status line printed.
  2. On merged feature branch → switch+pull+delete sequence runs.
  3. On open-PR branch → no mutation, "staying put" line printed.
  4. On dirty tree with real edits → no mutation, "staying put" line.

Plus guards: non-UA cwd is a no-op; missing gh CLI degrades gracefully;
the inner pipeline's exceptions never escape run_baseline_check().

Strategy: monkeypatch `_run` (the subprocess wrapper) and `_branch_pr_state`
(the gh probe) so the tests are hermetic and don't touch the live repo or
network.
"""

from __future__ import annotations

import io
from pathlib import Path
import subprocess
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import claude_session_baseline as baseline  # noqa: E402

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class _FakeRunRecorder:
    """Records every _run() call and returns scripted responses.

    Usage:
        recorder = _FakeRunRecorder(responses={
            ("git", "rev-parse", "HEAD"): _completed("abc12345"),
        })
        monkeypatch.setattr(baseline, "_run", recorder)
    """

    def __init__(self, responses: dict[tuple[str, ...], subprocess.CompletedProcess[str]]):
        self.responses = responses
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self,
        cmd: list[str],
        cwd: Path,
        *,
        timeout: int = 30,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        key = tuple(cmd)
        self.calls.append(key)
        if key in self.responses:
            result = self.responses[key]
        else:
            # Default: success with empty stdout (covers git pull, switch, branch -D, fetch, stash).
            result = _completed()
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, output=result.stdout, stderr=result.stderr
            )
        return result

    def ran(self, *cmd_tokens: str) -> bool:
        return tuple(cmd_tokens) in self.calls


def _make_ua_repo(tmp_path: Path) -> Path:
    """Create a fake UA checkout layout (.git dir is enough for _is_ua_repo)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".git").mkdir()
    return tmp_path


# --------------------------------------------------------------------------- #
# Behaviour tests
# --------------------------------------------------------------------------- #


def test_on_main_fast_forwards_and_prints_status(tmp_path, monkeypatch):
    ua_root = _make_ua_repo(tmp_path)
    recorder = _FakeRunRecorder(
        {
            ("git", "branch", "--show-current"): _completed("main\n"),
            ("git", "rev-parse", "HEAD"): _completed("abcdef1234\n"),
        }
    )
    monkeypatch.setattr(baseline, "_run", recorder)

    stream = io.StringIO()
    baseline.run_baseline_check(cwd=ua_root, ua_install_root=ua_root, stream=stream)

    assert recorder.ran("git", "fetch", "origin", "--prune", "--quiet")
    assert recorder.ran("git", "pull", "--ff-only", "origin", "main")
    out = stream.getvalue()
    assert "on main @ abcdef12" in out


def test_merged_branch_switches_and_deletes(tmp_path, monkeypatch):
    ua_root = _make_ua_repo(tmp_path)
    recorder = _FakeRunRecorder(
        {
            ("git", "branch", "--show-current"): _completed("claude/done-work\n"),
            ("git", "status", "--porcelain"): _completed(""),
            (
                "git",
                "ls-remote",
                "--exit-code",
                "--heads",
                "origin",
                "claude/done-work",
            ): _completed(returncode=2),
            ("git", "rev-parse", "HEAD"): _completed("ffeeddcc11\n"),
        }
    )
    monkeypatch.setattr(baseline, "_run", recorder)
    monkeypatch.setattr(baseline, "_branch_pr_state", lambda cwd, b: "MERGED")

    stream = io.StringIO()
    baseline.run_baseline_check(cwd=ua_root, ua_install_root=ua_root, stream=stream)

    assert recorder.ran("git", "switch", "main")
    assert recorder.ran("git", "pull", "--ff-only", "origin", "main")
    assert recorder.ran("git", "branch", "-D", "claude/done-work")
    out = stream.getvalue()
    assert "cleaned up merged claude/done-work" in out


def test_open_pr_branch_stays_put(tmp_path, monkeypatch):
    ua_root = _make_ua_repo(tmp_path)
    recorder = _FakeRunRecorder(
        {
            ("git", "branch", "--show-current"): _completed("claude/wip\n"),
            ("git", "status", "--porcelain"): _completed(""),
            (
                "git",
                "ls-remote",
                "--exit-code",
                "--heads",
                "origin",
                "claude/wip",
            ): _completed("sha\trefs/heads/claude/wip\n"),
            ("git", "rev-parse", "HEAD"): _completed("11223344\n"),
        }
    )
    monkeypatch.setattr(baseline, "_run", recorder)
    monkeypatch.setattr(baseline, "_branch_pr_state", lambda cwd, b: "OPEN")

    stream = io.StringIO()
    baseline.run_baseline_check(cwd=ua_root, ua_install_root=ua_root, stream=stream)

    assert not recorder.ran("git", "switch", "main")
    assert not recorder.ran("git", "branch", "-D", "claude/wip")
    out = stream.getvalue()
    assert "PR open" in out
    assert "staying put" in out


def test_dirty_real_edits_stay_put(tmp_path, monkeypatch):
    ua_root = _make_ua_repo(tmp_path)
    recorder = _FakeRunRecorder(
        {
            ("git", "branch", "--show-current"): _completed("claude/wip\n"),
            ("git", "status", "--porcelain"): _completed(
                " M src/universal_agent/services/foo.py\n"
                " M .omc/state/hud-state.json\n"
            ),
        }
    )
    monkeypatch.setattr(baseline, "_run", recorder)
    # _branch_pr_state should never be called when we bail early on dirty
    monkeypatch.setattr(
        baseline,
        "_branch_pr_state",
        MagicMock(side_effect=AssertionError("must not probe gh on dirty real edits")),
    )

    stream = io.StringIO()
    baseline.run_baseline_check(cwd=ua_root, ua_install_root=ua_root, stream=stream)

    assert not recorder.ran("git", "switch", "main")
    out = stream.getvalue()
    assert "uncommitted change" in out
    assert "claude/wip" in out


def test_dirty_runtime_gunk_only_still_cleans_when_merged(tmp_path, monkeypatch):
    ua_root = _make_ua_repo(tmp_path)
    recorder = _FakeRunRecorder(
        {
            ("git", "branch", "--show-current"): _completed("claude/done\n"),
            ("git", "status", "--porcelain"): _completed(
                " M .omc/state/hud-state.json\n"
                " M memory/index.json\n"
                " M MEMORY.md\n"
            ),
            (
                "git",
                "ls-remote",
                "--exit-code",
                "--heads",
                "origin",
                "claude/done",
            ): _completed(returncode=2),
            ("git", "rev-parse", "HEAD"): _completed("aabbccdd\n"),
        }
    )
    monkeypatch.setattr(baseline, "_run", recorder)
    monkeypatch.setattr(baseline, "_branch_pr_state", lambda cwd, b: "MERGED")

    stream = io.StringIO()
    baseline.run_baseline_check(cwd=ua_root, ua_install_root=ua_root, stream=stream)

    # Inner cleanup: stash the gunk, switch, pull, delete.
    assert any(call[:3] == ("git", "stash", "push") for call in recorder.calls), recorder.calls
    assert recorder.ran("git", "switch", "main")
    assert recorder.ran("git", "branch", "-D", "claude/done")


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #


def test_non_ua_cwd_is_a_noop(tmp_path, monkeypatch):
    """When the launch cwd isn't the UA checkout, do nothing."""
    other_repo = tmp_path / "other"
    other_repo.mkdir()
    (other_repo / ".git").mkdir()
    ua_root = _make_ua_repo(tmp_path / "ua")

    recorder = _FakeRunRecorder({})
    monkeypatch.setattr(baseline, "_run", recorder)

    stream = io.StringIO()
    baseline.run_baseline_check(cwd=other_repo, ua_install_root=ua_root, stream=stream)

    assert recorder.calls == []
    assert stream.getvalue() == ""


def test_inner_exception_is_swallowed(tmp_path, monkeypatch):
    """Any unexpected exception inside the pipeline must NOT escape."""
    ua_root = _make_ua_repo(tmp_path)

    def boom(*a: Any, **kw: Any) -> None:
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(baseline, "_run", boom)

    stream = io.StringIO()
    # Must not raise.
    baseline.run_baseline_check(cwd=ua_root, ua_install_root=ua_root, stream=stream)

    out = stream.getvalue()
    assert "baseline check skipped" in out
    assert "synthetic failure" in out


def test_branch_pr_state_returns_none_when_gh_missing(tmp_path, monkeypatch):
    """If `gh` is not on PATH, _branch_pr_state returns None."""
    monkeypatch.setattr(baseline.shutil, "which", lambda _: None)
    result = baseline._branch_pr_state(tmp_path, "claude/anything")
    assert result is None


def test_branch_pr_state_returns_none_when_gh_fails(tmp_path, monkeypatch):
    """If gh exits non-zero (e.g. no PR for the branch), return None."""
    monkeypatch.setattr(baseline.shutil, "which", lambda _: "/usr/bin/gh")
    recorder = _FakeRunRecorder(
        {
            (
                "gh",
                "pr",
                "view",
                "claude/x",
                "--json",
                "state",
                "-q",
                ".state",
            ): _completed(returncode=1, stderr="no pull requests found"),
        }
    )
    monkeypatch.setattr(baseline, "_run", recorder)
    assert baseline._branch_pr_state(tmp_path, "claude/x") is None


def test_detached_head_emits_info_and_does_not_mutate(tmp_path, monkeypatch):
    ua_root = _make_ua_repo(tmp_path)
    recorder = _FakeRunRecorder(
        {
            ("git", "branch", "--show-current"): _completed(""),
        }
    )
    monkeypatch.setattr(baseline, "_run", recorder)

    stream = io.StringIO()
    baseline.run_baseline_check(cwd=ua_root, ua_install_root=ua_root, stream=stream)

    out = stream.getvalue()
    assert "detached HEAD" in out
    assert not recorder.ran("git", "switch", "main")


# --------------------------------------------------------------------------- #
# Pure-helper unit tests
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "paths,expected",
    [
        ([], True),
        ([".omc/state/hud-state.json"], True),
        ([".omc/state/x", "memory/index.json", "MEMORY.md", "temp/notes.md"], True),
        ([".omc/state/x", "src/foo.py"], False),
        (["src/foo.py"], False),
        (["docs/whatever.md"], False),
    ],
)
def test_only_runtime_gunk(paths, expected):
    assert baseline._only_runtime_gunk(paths) is expected
