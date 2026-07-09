"""Tests for the paper_to_podcast pre-run workspace hygiene.

``prepare_run_workspace`` clears the reused cron workspace's OUTPUT dir before a
run so the post-run "did we produce a podcast?" check is a true binary existence
check — while preserving the workspace root's ``.nlm_resume.json`` deploy-restart
checkpoint.
"""

from __future__ import annotations

from pathlib import Path

from universal_agent.services.paper_to_podcast_workspace import prepare_run_workspace


def _output_dir(workspace: Path) -> Path:
    return workspace / "work_products" / "paper_to_podcast"


class TestPrepareRunWorkspace:
    def test_clears_prior_run_output(self, tmp_path):
        """Stale files AND subdirs from a prior run are removed; dir left empty."""
        out = _output_dir(tmp_path)
        out.mkdir(parents=True)
        (out / "podcast_audio.m4a").write_bytes(b"\x00" * (200 * 1024))  # yesterday's
        (out / "manifest.json").write_text("{}", encoding="utf-8")
        (out / "FAILURE.txt").write_text("old", encoding="utf-8")
        sub = out / "causal_ml"
        sub.mkdir()
        (sub / "manifest.json").write_text("{}", encoding="utf-8")

        removed = prepare_run_workspace(tmp_path)

        assert removed == 4  # 3 files + 1 subdir at top level
        assert out.is_dir()
        assert list(out.iterdir()) == []  # a clean slate

    def test_preserves_resume_checkpoint_at_root(self, tmp_path):
        """The wipe must NOT touch .nlm_resume.json at the workspace root — a
        deploy-restart-interrupted run relies on it to adopt its notebook."""
        out = _output_dir(tmp_path)
        out.mkdir(parents=True)
        (out / "podcast_audio.m4a").write_bytes(b"\x00" * 1024)
        checkpoint = tmp_path / ".nlm_resume.json"
        checkpoint.write_text('{"notebook_id": "abc", "status": "polling"}', encoding="utf-8")

        prepare_run_workspace(tmp_path)

        assert checkpoint.is_file()  # survived
        assert checkpoint.read_text(encoding="utf-8").startswith('{"notebook_id"')
        assert list(out.iterdir()) == []  # but the output dir was cleared

    def test_absent_output_dir_is_created_empty(self, tmp_path):
        """First-ever run (no output dir yet): returns 0 and creates the dir."""
        out = _output_dir(tmp_path)
        assert not out.exists()

        removed = prepare_run_workspace(tmp_path)

        assert removed == 0
        assert out.is_dir()
        assert list(out.iterdir()) == []

    def test_empty_output_dir_is_noop(self, tmp_path):
        out = _output_dir(tmp_path)
        out.mkdir(parents=True)
        assert prepare_run_workspace(tmp_path) == 0
        assert out.is_dir()

    def test_never_raises_on_bad_path(self, tmp_path):
        """A cleanup problem must never bubble up and block the run."""
        # workspace_dir points at an existing *file*, so work_products/... can't
        # be created — the function must swallow the error and return 0.
        bogus = tmp_path / "not_a_dir"
        bogus.write_text("i am a file", encoding="utf-8")
        assert prepare_run_workspace(bogus) == 0
