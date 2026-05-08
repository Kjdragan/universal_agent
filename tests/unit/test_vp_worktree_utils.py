"""Unit tests for ``universal_agent.vp.worktree_utils``.

These tests exist because the 2026-05-07 incident was caused by a
tier-2 mission editing the deployed working tree in place and never
syntax-checking the result. ``worktree_utils`` is the building block
that prevents that class of regression. The most important
assertion in this file is ``test_syntax_check_catches_docstring_in_arglist``
— a deliberate mirror of the actual SyntaxError that mangled
``durable/state.py`` last week.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from universal_agent.vp.worktree_utils import (
    ArtifactLeakError,
    RepoNotFoundError,
    assert_no_artifacts,
    detect_repo_root,
    list_changed_py_files,
    provision_worktree,
    revert_changed_files,
    syntax_check_changed_py,
    teardown_worktree,
)


@pytest.fixture
def fresh_git_repo(tmp_path: Path) -> Path:
    """Initialise a git repo with one committed Python file on a base branch."""

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
    # Match the contract's base-branch convention.
    subprocess.run(
        ["git", "branch", "-M", "feature/latest2"], cwd=str(repo), check=True,
    )
    return repo


class TestDetectRepoRoot:
    def test_returns_repo_root_for_inside_path(self, fresh_git_repo: Path):
        nested = fresh_git_repo / "deep" / "nested"
        nested.mkdir(parents=True)
        assert detect_repo_root(start=nested) == fresh_git_repo.resolve()

    def test_raises_outside_repo(self, tmp_path: Path):
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        with pytest.raises(RepoNotFoundError):
            detect_repo_root(start=non_repo)


class TestProvisionAndTeardown:
    def test_provisions_worktree_on_new_branch(
        self, fresh_git_repo: Path, tmp_path: Path,
    ):
        result = provision_worktree(
            bot_name="codie",
            task_id="abc123",
            base_branch="feature/latest2",
            repo_root=fresh_git_repo,
            workspace_root=tmp_path / "ws",
        )
        assert result.succeeded, result.error
        assert result.branch == "codie/abc123"
        assert result.worktree_path.exists()
        assert (result.worktree_path / "hello.py").exists()
        # The worktree must be on the new branch, not the base.
        head = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(result.worktree_path), capture_output=True, text=True,
        )
        assert head.stdout.strip() == "codie/abc123"

    def test_provision_sanitizes_traversal_attempts(
        self, fresh_git_repo: Path, tmp_path: Path,
    ):
        result = provision_worktree(
            bot_name="../bad",
            task_id="../../escape",
            base_branch="feature/latest2",
            repo_root=fresh_git_repo,
            workspace_root=tmp_path / "ws",
        )
        # The slug should have been scrubbed of '..' segments.
        assert ".." not in result.branch
        assert ".." not in str(result.worktree_path)

    def test_teardown_removes_worktree(
        self, fresh_git_repo: Path, tmp_path: Path,
    ):
        result = provision_worktree(
            bot_name="codie",
            task_id="t1",
            base_branch="feature/latest2",
            repo_root=fresh_git_repo,
            workspace_root=tmp_path / "ws",
        )
        assert result.succeeded
        assert result.worktree_path.exists()
        ok = teardown_worktree(result.worktree_path, repo_root=fresh_git_repo)
        assert ok
        assert not result.worktree_path.exists()

    def test_teardown_idempotent_on_missing_path(
        self, fresh_git_repo: Path, tmp_path: Path,
    ):
        # Already-gone path must not raise.
        ok = teardown_worktree(tmp_path / "never_existed", repo_root=fresh_git_repo)
        assert ok is True


class TestSyntaxCheck:
    def test_passes_for_valid_module(self, tmp_path: Path):
        path = tmp_path / "ok.py"
        path.write_text("def f():\n    return 42\n")
        out = syntax_check_changed_py(tmp_path, [path])
        assert out.ok is True
        assert out.failures == []

    def test_catches_docstring_in_arglist(self, tmp_path: Path):
        """The exact regression that mangled durable/state.py on 2026-05-07.

        A docstring placed inside a function's parameter list is a
        SyntaxError. If this test ever stops failing for the broken
        case, it means we've silently weakened the syntax check.
        """

        path = tmp_path / "broken.py"
        path.write_text(
            'def make_state(\n'
            '    """A misplaced docstring inside the arglist."""\n'
            '    *, name: str,\n'
            ') -> dict:\n'
            '    return {"name": name}\n'
        )
        out = syntax_check_changed_py(tmp_path, [path])
        assert out.ok is False
        assert len(out.failures) == 1
        failed_path, msg = out.failures[0]
        assert failed_path == path.resolve()
        assert "SyntaxError" in msg

    def test_skips_non_python_files(self, tmp_path: Path):
        path = tmp_path / "notes.md"
        path.write_text("# not python")
        out = syntax_check_changed_py(tmp_path, [path])
        assert out.ok is True
        assert out.failures == []

    def test_reports_missing_files(self, tmp_path: Path):
        out = syntax_check_changed_py(tmp_path, [tmp_path / "ghost.py"])
        assert out.ok is False
        assert len(out.failures) == 1
        assert "does not exist" in out.failures[0][1]


class TestRevert:
    def test_reverts_modified_file_to_HEAD(
        self, fresh_git_repo: Path, tmp_path: Path,
    ):
        result = provision_worktree(
            bot_name="codie",
            task_id="rev",
            base_branch="feature/latest2",
            repo_root=fresh_git_repo,
            workspace_root=tmp_path / "ws",
        )
        assert result.succeeded
        target = result.worktree_path / "hello.py"
        original = target.read_text()
        target.write_text("def hello():\n    return 9999\n")
        assert target.read_text() != original
        ok = revert_changed_files(result.worktree_path, [target])
        assert ok
        assert target.read_text() == original

    def test_returns_true_for_empty_input(self, tmp_path: Path):
        assert revert_changed_files(tmp_path, []) is True

    def test_skips_paths_outside_worktree(
        self, fresh_git_repo: Path, tmp_path: Path,
    ):
        result = provision_worktree(
            bot_name="codie",
            task_id="rev2",
            base_branch="feature/latest2",
            repo_root=fresh_git_repo,
            workspace_root=tmp_path / "ws",
        )
        assert result.succeeded
        outside = tmp_path / "other.py"
        outside.write_text("x = 1\n")
        # Out-of-worktree path is silently filtered; nothing to revert.
        assert revert_changed_files(result.worktree_path, [outside]) is True


class TestArtifactsTripwire:
    def test_passes_clean_tree(self, tmp_path: Path):
        (tmp_path / "ok.py").write_text("x = 1\n")
        assert_no_artifacts(tmp_path)  # no exception

    def test_flags_py_bak(self, tmp_path: Path):
        (tmp_path / "leak.py.bak").write_text("noise")
        with pytest.raises(ArtifactLeakError):
            assert_no_artifacts(tmp_path)

    def test_flags_swp(self, tmp_path: Path):
        (tmp_path / ".module.py.swp").write_text("noise")
        with pytest.raises(ArtifactLeakError):
            assert_no_artifacts(tmp_path)

    def test_flags_orig(self, tmp_path: Path):
        (tmp_path / "merge.py.orig").write_text("noise")
        with pytest.raises(ArtifactLeakError):
            assert_no_artifacts(tmp_path)


class TestListChangedPyFiles:
    def test_includes_modified_and_untracked(
        self, fresh_git_repo: Path, tmp_path: Path,
    ):
        result = provision_worktree(
            bot_name="codie",
            task_id="diff",
            base_branch="feature/latest2",
            repo_root=fresh_git_repo,
            workspace_root=tmp_path / "ws",
        )
        assert result.succeeded
        # Modify an existing tracked file.
        (result.worktree_path / "hello.py").write_text(
            "def hello():\n    return 2\n",
        )
        # Add a new untracked Python file and a non-Python file.
        (result.worktree_path / "new_module.py").write_text("print('hi')\n")
        (result.worktree_path / "README.txt").write_text("docs only\n")

        changed = list_changed_py_files(result.worktree_path)
        names = sorted(p.name for p in changed)
        assert "hello.py" in names
        assert "new_module.py" in names
        assert "README.txt" not in names
