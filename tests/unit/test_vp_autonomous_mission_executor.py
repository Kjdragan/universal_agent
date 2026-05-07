"""Unit tests for ``universal_agent.vp.autonomous_mission_executor``.

The orchestrator is small but every branch matters — these are the
gates that prevent another 2026-05-07-style production import storm.

Coverage targets:

* Happy path: valid patch -> compile passes -> tests pass -> branch
  pushed -> PR opened. Worktree is torn down.
* Syntax-fail path: patch writes invalid Python -> compile catches it
  -> file reverted -> mission marked reverted=True -> NO PR.
* Artifact-leak path: patch leaves a ``.py.bak`` -> mission fails fast
  -> file reverted -> NO PR.
* Test-fail path: tests return non-zero -> file reverted -> NO PR.
* No-op path: patch_fn returns no files -> mission short-circuits
  with a clear failure_reason and tears the worktree down.
* PR-creator failure: push or PR creation fails -> mission marked
  failed but ``pushed=False`` is honest about state.

The ``pr_creator`` and ``test_runner`` callables are dependency-injected
so the tests never hit the network or run pytest recursively.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from universal_agent.vp.autonomous_mission_executor import (
    MissionResult,
    execute_autonomous_mission,
)

# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(repo), check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=str(repo), check=True,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"],
        cwd=str(repo), check=True,
    )
    (repo / "hello.py").write_text("def hello():\n    return 1\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"], cwd=str(repo), check=True,
    )
    subprocess.run(
        ["git", "branch", "-M", "feature/latest2"], cwd=str(repo), check=True,
    )
    return repo


@pytest.fixture
def task() -> dict:
    return {
        "task_id": "task-001",
        "title": "tighten hello.py",
        "description": "Add a docstring to hello().",
    }


def _no_op_test_runner(_worktree: Path, _changed: list[Path]) -> tuple[bool, str]:
    return True, "skipped (test fixture)"


def _failing_test_runner(_worktree: Path, _changed: list[Path]) -> tuple[bool, str]:
    return False, "FAILED tests/test_xxx.py::test_foo"


def _stub_pr_creator(
    *, worktree_path, branch, base_branch, title, body,
):
    # Simulate successful push + PR creation. Tests that need to assert
    # push behaviour do so via subprocess mocking; here we just record.
    _stub_pr_creator.calls.append({
        "worktree_path": str(worktree_path),
        "branch": branch,
        "base_branch": base_branch,
        "title": title,
        "body": body,
    })
    return f"https://github.com/example/repo/pull/{len(_stub_pr_creator.calls)}"


_stub_pr_creator.calls = []  # type: ignore[attr-defined]


def _failing_pr_creator(*, worktree_path, branch, base_branch, title, body):
    return None


# ── Patch functions ──────────────────────────────────────────────────────


def patch_valid(worktree: Path) -> list[Path]:
    target = worktree / "hello.py"
    target.write_text(
        '"""hello module."""\n'
        '\n'
        'def hello():\n'
        '    """Return one."""\n'
        '    return 1\n'
    )
    return [target]


def patch_syntax_error(worktree: Path) -> list[Path]:
    """Mirror the docstring-in-arglist regression from 2026-05-07."""

    target = worktree / "hello.py"
    target.write_text(
        'def hello(\n'
        '    """misplaced docstring inside arglist."""\n'
        ') -> int:\n'
        '    return 1\n'
    )
    return [target]


def patch_leaves_bak(worktree: Path) -> list[Path]:
    """Valid Python but leaves a banned .py.bak artifact."""

    target = worktree / "hello.py"
    target.write_text("def hello():\n    return 7\n")
    (worktree / "hello.py.bak").write_text("ignore me\n")
    return [target]


def patch_no_op(_worktree: Path) -> list[Path]:
    return []


def patch_raises(_worktree: Path) -> list[Path]:
    raise RuntimeError("simulated patch_fn crash")


# ── Tests ────────────────────────────────────────────────────────────────


class TestHappyPath:
    def setup_method(self):
        _stub_pr_creator.calls = []

    def test_full_run_pushes_and_opens_pr(self, fresh_git_repo: Path, task: dict, tmp_path: Path):
        result = execute_autonomous_mission(
            task=task,
            patch_fn=patch_valid,
            bot_name="codie",
            base_branch="feature/latest2",
            repo_root=fresh_git_repo,
            workspace_root=tmp_path / "ws",
            pr_creator=_stub_pr_creator,
            test_runner=_no_op_test_runner,
        )
        assert result.succeeded is True, result.failure_reason
        assert result.pushed is True
        assert result.pr_url and result.pr_url.startswith("https://")
        assert result.branch == "codie/task-001"
        assert result.reverted is False
        assert result.failure_reason is None
        # Worktree is cleaned up on success.
        assert result.worktree_path is not None
        assert result.teardown_ok is True
        # PR creator called with the contract base branch.
        assert len(_stub_pr_creator.calls) == 1
        assert _stub_pr_creator.calls[0]["base_branch"] == "feature/latest2"

    def test_as_dict_returns_json_friendly_payload(
        self, fresh_git_repo: Path, task: dict, tmp_path: Path,
    ):
        result = execute_autonomous_mission(
            task=task,
            patch_fn=patch_valid,
            bot_name="codie",
            repo_root=fresh_git_repo,
            workspace_root=tmp_path / "ws",
            pr_creator=_stub_pr_creator,
            test_runner=_no_op_test_runner,
        )
        as_dict = result.as_dict()
        # Must be JSON-serialisable end to end (no Path objects leak).
        import json as _json

        _json.dumps(as_dict)
        assert as_dict["succeeded"] is True


class TestSyntaxFailPath:
    def test_invalid_python_triggers_revert(
        self, fresh_git_repo: Path, task: dict, tmp_path: Path,
    ):
        result = execute_autonomous_mission(
            task=task,
            patch_fn=patch_syntax_error,
            bot_name="codie",
            repo_root=fresh_git_repo,
            workspace_root=tmp_path / "ws",
            pr_creator=_stub_pr_creator,
            test_runner=_no_op_test_runner,
        )
        assert result.succeeded is False
        assert result.failure_reason == "syntax-check-failed"
        assert result.reverted is True
        assert result.pushed is False
        assert result.pr_url is None
        assert len(result.syntax_failures) >= 1
        # Worktree is removed even on failure.
        assert result.teardown_ok is True


class TestArtifactLeakPath:
    def test_bak_artifact_blocks_mission(
        self, fresh_git_repo: Path, task: dict, tmp_path: Path,
    ):
        result = execute_autonomous_mission(
            task=task,
            patch_fn=patch_leaves_bak,
            bot_name="codie",
            repo_root=fresh_git_repo,
            workspace_root=tmp_path / "ws",
            pr_creator=_stub_pr_creator,
            test_runner=_no_op_test_runner,
        )
        assert result.succeeded is False
        assert "artifact-leak" in (result.failure_reason or "")
        assert result.reverted is True
        assert result.pushed is False


class TestTestFailPath:
    def test_test_failure_reverts(
        self, fresh_git_repo: Path, task: dict, tmp_path: Path,
    ):
        result = execute_autonomous_mission(
            task=task,
            patch_fn=patch_valid,
            bot_name="codie",
            repo_root=fresh_git_repo,
            workspace_root=tmp_path / "ws",
            pr_creator=_stub_pr_creator,
            test_runner=_failing_test_runner,
        )
        assert result.succeeded is False
        assert result.failure_reason == "tests-failed"
        assert result.reverted is True
        assert result.pushed is False
        assert "FAILED" in (result.test_output or "")


class TestNoOpAndPatchCrash:
    def test_no_op_patch_short_circuits(
        self, fresh_git_repo: Path, task: dict, tmp_path: Path,
    ):
        result = execute_autonomous_mission(
            task=task,
            patch_fn=patch_no_op,
            bot_name="codie",
            repo_root=fresh_git_repo,
            workspace_root=tmp_path / "ws",
            pr_creator=_stub_pr_creator,
            test_runner=_no_op_test_runner,
        )
        assert result.succeeded is False
        assert "no-op" in (result.failure_reason or "")
        assert result.pushed is False

    def test_patch_crash_is_captured(
        self, fresh_git_repo: Path, task: dict, tmp_path: Path,
    ):
        result = execute_autonomous_mission(
            task=task,
            patch_fn=patch_raises,
            bot_name="codie",
            repo_root=fresh_git_repo,
            workspace_root=tmp_path / "ws",
            pr_creator=_stub_pr_creator,
            test_runner=_no_op_test_runner,
        )
        assert result.succeeded is False
        assert "patch-fn-raised" in (result.failure_reason or "")
        assert "RuntimeError" in (result.failure_reason or "")


class TestPRCreatorFailure:
    def test_push_failure_marks_failure_without_pushed_flag(
        self, fresh_git_repo: Path, task: dict, tmp_path: Path,
    ):
        result = execute_autonomous_mission(
            task=task,
            patch_fn=patch_valid,
            bot_name="codie",
            repo_root=fresh_git_repo,
            workspace_root=tmp_path / "ws",
            pr_creator=_failing_pr_creator,
            test_runner=_no_op_test_runner,
        )
        assert result.succeeded is False
        assert result.failure_reason == "pr-creation-failed"
        assert result.pushed is False
        assert result.pr_url is None


class TestBaseBranchHonoured:
    def test_mission_uses_supplied_base_branch(
        self, fresh_git_repo: Path, task: dict, tmp_path: Path,
    ):
        # Create a second branch we can target.
        subprocess.run(
            ["git", "branch", "develop"], cwd=str(fresh_git_repo), check=True,
        )
        _stub_pr_creator.calls = []
        result = execute_autonomous_mission(
            task=task,
            patch_fn=patch_valid,
            bot_name="codie",
            base_branch="develop",
            repo_root=fresh_git_repo,
            workspace_root=tmp_path / "ws",
            pr_creator=_stub_pr_creator,
            test_runner=_no_op_test_runner,
        )
        assert result.succeeded is True
        assert _stub_pr_creator.calls[0]["base_branch"] == "develop"
