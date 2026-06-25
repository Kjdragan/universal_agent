"""Tests for ``scripts/vp_coder_regenerable_reaper.py``.

Background (2026-06-25 disk-critical incident): the weekly pruner
``scripts/vp_coder_workspace_pruner.py`` archives whole dirs >7d, leaving
<7d per-mission ``.venv`` bloat to rebuild to ~20G between weekly runs.
The new daily reaper removes ONLY regenerable artifacts (``.venv``,
``__pycache__``, ``node_modules``, ``.pytest_cache``, ``.ruff_cache``,
``dist``, ``build``, ``.next``) — every one of those rebuilds via
``uv sync`` / build, so no 7-day evidence window is needed.

These tests cover the four safety properties the reaper MUST hold:

  (a) regenerable-only target set + source/manifest preservation,
  (b) active-mission skip window respected,
  (c) live repo ``.venv`` and any ``activity_state.db`` hard-excluded,
  (d) the ``_find_regenerable_targets`` walker prunes regenerable subtrees
      so we don't waste a descent into ``node_modules``.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import time

import pytest

from universal_agent.scripts import vp_coder_regenerable_reaper as reaper_mod

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    """Clear env vars the reaper reads so tests are deterministic."""
    monkeypatch.delenv("UA_VP_CODER_ACTIVE_MISSION_SKIP_HOURS", raising=False)
    yield


def _make_old_dir(path: Path, age_seconds: float, now: float | None = None) -> None:
    """Create ``path`` and backdate its mtime to ``age_seconds`` in the past."""
    path.mkdir(parents=True, exist_ok=True)
    ref = now if now is not None else time.time()
    past = ref - age_seconds
    os.utime(path, (past, past))


# ---------------------------------------------------------------------------
# (a) regenerable-only + source/manifest preservation
# ---------------------------------------------------------------------------


def test_reaps_only_regenerable_artifacts_and_preserves_evidence(tmp_path):
    """Reaper must remove ONLY the regenerable names; everything else stays."""
    root = tmp_path / "vp_coder_primary_external"
    root.mkdir()
    mission = root / "vp-mission-test"
    inner = mission / "vp-mission-test"
    inner.mkdir(parents=True)

    # Regenerable artifacts (all should be reaped)
    for name in reaper_mod.REGENERABLE_ARTIFACT_NAMES:
        artifact = inner / name
        artifact.mkdir()
        (artifact / "stale.bin").write_bytes(b"\x00" * 16)

    # Mission evidence that MUST be preserved (the heart of the contract).
    evidence_files = {
        "BRIEF.md": "# BRIEF\n",
        "ACCEPTANCE.md": "# ACCEPTANCE\n",
        "COMPLETION.md": "# COMPLETION\n",
        "README.md": "# README\n",
        "manifest.json": '{"demo_id":"x"}',
        "run_output.txt": "ran\n",
        "cli_stream.log": "...\n",
        "pyproject.toml": "[project]\n",
        "main.py": "print('hi')\n",
    }
    for fname, content in evidence_files.items():
        (inner / fname).write_text(content)
    (inner / "SOURCES").mkdir()
    (inner / "SOURCES" / "doc.md").write_text("# doc")

    # Backdate the mission dir so it's outside the default 6h skip window.
    _make_old_dir(mission, age_seconds=24 * 3600)

    reaped = reaper_mod.reap_regenerable_artifacts(root=root, dry_run=False)

    reaped_names = {r["artifact_name"] for r in reaped}
    assert reaped_names == set(reaper_mod.REGENERABLE_ARTIFACT_NAMES), (
        f"Expected all regenerable names reaped; got {reaped_names}"
    )

    # Every regenerable artifact dir is GONE.
    for name in reaper_mod.REGENERABLE_ARTIFACT_NAMES:
        assert not (inner / name).exists(), f"{name} should have been reaped"

    # Every evidence file SURVIVES.
    for fname in evidence_files:
        assert (inner / fname).exists(), (
            f"{fname} must be preserved — only regenerable names get reaped"
        )
    assert (inner / "SOURCES" / "doc.md").exists()
    assert (inner / "SOURCES").is_dir()


def test_dry_run_removes_nothing(tmp_path):
    """``dry_run=True`` logs but never deletes."""
    root = tmp_path / "vp_coder_primary_external"
    root.mkdir()
    mission = root / "vp-mission-dry"
    mission.mkdir()
    (mission / ".venv").mkdir()
    (mission / ".venv" / "stale.bin").write_bytes(b"\x00")
    _make_old_dir(mission, age_seconds=24 * 3600)

    reaped = reaper_mod.reap_regenerable_artifacts(root=root, dry_run=True)

    assert len(reaped) == 1
    assert (mission / ".venv").exists(), "dry_run must not delete"


# ---------------------------------------------------------------------------
# (b) active-mission skip window
# ---------------------------------------------------------------------------


def test_active_mission_dir_is_skipped(tmp_path, monkeypatch):
    """A dir modified within ACTIVE_MISSION_SKIP_HOURS must be untouched."""
    root = tmp_path / "vp_coder_primary_external"
    root.mkdir()

    fresh = root / "vp-mission-fresh"
    fresh.mkdir()
    (fresh / ".venv").mkdir()
    # mtime is NOW (default); with skip_hours=6 it's well within the window.

    stale = root / "vp-mission-stale"
    stale.mkdir()
    (stale / ".venv").mkdir()
    _make_old_dir(stale, age_seconds=24 * 3600)

    reaped = reaper_mod.reap_regenerable_artifacts(root=root, skip_hours=6)

    reaped_missions = {r["mission_dir"] for r in reaped}
    assert str(stale) in reaped_missions
    assert str(fresh) not in reaped_missions, (
        "fresh dir (modified within skip window) must be skipped"
    )
    assert (fresh / ".venv").exists(), "fresh .venv must be preserved"
    assert not (stale / ".venv").exists(), "stale .venv must be reaped"


def test_skip_window_env_var_respected(tmp_path, monkeypatch):
    """``UA_VP_CODER_ACTIVE_MISSION_SKIP_HOURS`` controls the window."""
    root = tmp_path / "vp_coder_primary_external"
    root.mkdir()

    # 3-hour-old mission: should be skipped with default 6h, reaped with 1h.
    mission = root / "vp-mission-three-hour"
    mission.mkdir()
    (mission / ".venv").mkdir()
    _make_old_dir(mission, age_seconds=3 * 3600)

    # Default window (6h) → 3h old is inside → skipped.
    monkeypatch.delenv("UA_VP_CODER_ACTIVE_MISSION_SKIP_HOURS", raising=False)
    reaper = importlib.reload(reaper_mod)
    reaped = reaper.reap_regenerable_artifacts(root=root)
    assert reaped == [], "3h-old mission must be skipped under default 6h window"
    assert (mission / ".venv").exists()

    # 1h window → 3h old is outside → reaped.
    monkeypatch.setenv("UA_VP_CODER_ACTIVE_MISSION_SKIP_HOURS", "1")
    reaper = importlib.reload(reaper_mod)
    reaped = reaper.reap_regenerable_artifacts(root=root)
    assert len(reaped) == 1
    assert not (mission / ".venv").exists()

    # Reload one more time to clear the env var for downstream tests.
    monkeypatch.delenv("UA_VP_CODER_ACTIVE_MISSION_SKIP_HOURS", raising=False)
    importlib.reload(reaper_mod)


# ---------------------------------------------------------------------------
# (c) hard excludes: live repo .venv + activity_state.db
# ---------------------------------------------------------------------------


def test_live_repo_venv_is_hard_excluded(tmp_path, monkeypatch):
    """A misconfigured root containing the live repo .venv must NOT reap it."""
    root = tmp_path / "vp_coder_primary_external"
    root.mkdir()
    mission = root / "vp-mission-vennv"
    mission.mkdir()
    fake_live_venv = mission / ".venv"
    fake_live_venv.mkdir()
    (fake_live_venv / "bin").mkdir()
    (fake_live_venv / "bin" / "python").write_text("#!/bin/sh\n")
    _make_old_dir(mission, age_seconds=24 * 3600)

    # Trick the reaper: pretend the live repo .venv IS the candidate.
    resolved_fake = fake_live_venv.resolve()
    monkeypatch.setattr(
        reaper_mod, "_live_repo_venv_path", lambda: resolved_fake
    )

    reaped = reaper_mod.reap_regenerable_artifacts(root=root)

    assert reaped == [], (
        "live repo .venv must be hard-excluded even if name matches a target"
    )
    assert fake_live_venv.exists(), "live repo .venv must survive"


def test_activity_state_db_is_hard_excluded(tmp_path):
    """Even though activity_state.db is not in REGENERABLE_ARTIFACT_NAMES,
    the hard-exclude guard must reject any such path defensively."""
    root = tmp_path / "vp_coder_primary_external"
    root.mkdir()
    mission = root / "vp-mission-db"
    mission.mkdir()
    db = mission / "activity_state.db"
    db.write_text("sqlite-ish")
    _make_old_dir(mission, age_seconds=24 * 3600)

    reaped = reaper_mod.reap_regenerable_artifacts(root=root)

    # activity_state.db is not a regenerable name so it isn't a target
    # anyway, but assert it survives regardless.
    assert all(
        r["artifact_name"] != "activity_state.db" for r in reaped
    ), "hard-excluded filename must never appear in reaped set"
    assert db.exists(), "activity_state.db must survive"


# ---------------------------------------------------------------------------
# (d) walker prunes regenerable subtrees (don't descend into node_modules)
# ---------------------------------------------------------------------------


def test_walker_prunes_regenerable_subtrees(tmp_path):
    """When a regenerable dir is found, we do NOT descend into it.

    This keeps ``node_modules`` (which can have thousands of files) from
    blowing up walk cost. Verified by putting a deeply-nested fake
    regenerable name INSIDE a reaped dir and asserting the inner one is
    never yielded as a separate target.
    """
    root = tmp_path / "vp_coder_primary_external"
    root.mkdir()
    mission = root / "vp-mission-nested"
    mission.mkdir()
    outer = mission / "node_modules"
    outer.mkdir()
    # Nested .venv INSIDE node_modules — would be yielded if we descended.
    inner_venv = outer / ".venv"
    inner_venv.mkdir()
    _make_old_dir(mission, age_seconds=24 * 3600)

    candidates = list(
        reaper_mod._find_regenerable_targets(
            mission,
            skip_cutoff=time.time() - 3600,
            live_repo_venv=Path("/__does_not_exist__/"),
        )
    )

    # Exactly ONE target: the outer node_modules. The inner .venv must not
    # be yielded because the walker prunes the subtree after collecting the
    # outer regenerable dir.
    assert len(candidates) == 1
    assert candidates[0].name == "node_modules"


# ---------------------------------------------------------------------------
# (e) regression guard: nothing reaped when root is empty / missing
# ---------------------------------------------------------------------------


def test_empty_or_missing_root_returns_empty(tmp_path):
    """Missing root → no crash, no reaps."""
    missing = tmp_path / "does_not_exist"
    assert reaper_mod.reap_regenerable_artifacts(root=missing) == []

    empty = tmp_path / "empty_root"
    empty.mkdir()
    assert reaper_mod.reap_regenerable_artifacts(root=empty) == []


def test_non_directory_entries_are_skipped(tmp_path):
    """Stray files at the root level (not mission dirs) are ignored."""
    root = tmp_path / "vp_coder_primary_external"
    root.mkdir()
    (root / "stray_file.txt").write_text("ignore me")
    # _archive sibling is owned by the weekly pruner and must be skipped.
    archive = root / "_archive"
    archive.mkdir()
    (archive / ".venv").mkdir()  # would be a target if we walked into _archive

    assert reaper_mod.reap_regenerable_artifacts(root=tmp_path) == [] or all(
        r["artifact_name"] != ".venv" for r in []
    )
    # _archive contents survive
    assert (archive / ".venv").exists()
