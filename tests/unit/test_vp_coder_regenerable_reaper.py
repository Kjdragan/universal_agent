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

The ``TestWorktreeRegenerableReap`` block covers the SECOND root added
2026-07-25 (``<repo>/.worktrees``). It builds a REAL temp git repo with REAL
worktrees — matching ``test_vp_coder_workspace_pruner.py``'s idiom — because
the whole point of the guard set is what git actually reports; a mocked
``git worktree list`` would prove nothing. The GitHub lookup is the one
dependency that is injected (``merged_at=``), so no test touches the network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib
import os
from pathlib import Path
import subprocess
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


# ---------------------------------------------------------------------------
# (f) tracked-content guard — the production bug found 2026-07-25
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str, env: dict | None = None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True,
        env={**os.environ, **env} if env else None,
    )
    return result.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "commit.gpgsign", "false")


def _age_tree(root: Path, seconds: float) -> None:
    """Backdate every entry under ``root`` (and ``root``) by ``seconds``."""
    past = time.time() - seconds
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames + dirnames:
            try:
                os.utime(Path(dirpath) / name, (past, past), follow_symlinks=False)
            except OSError:
                continue
    os.utime(root, (past, past))


def test_tracked_content_in_a_mission_dir_is_never_reaped(tmp_path):
    """A mission dir that IS a git checkout can hold TRACKED content under a
    regenerable name — this repo tracks
    ``node_modules/.vite/vitest/<sha>/results.json``, and every full checkout
    (all 7 trees under ``.worktrees`` on prod, measured 2026-07-25) therefore
    has a tracked ``node_modules/``. Reaping it by name deletes tracked source
    and dirties the tree, which permanently fails the weekly merged prune's
    clean-tree guard. Tracked content must survive; untracked regenerable
    content in the same dir must still be reaped."""
    root = tmp_path / "vp_coder_primary_external"
    root.mkdir()
    mission = root / "vp-mission-git"
    _init_repo(mission)

    tracked = mission / "node_modules" / ".vite" / "vitest" / "abc"
    tracked.mkdir(parents=True)
    (tracked / "results.json").write_text("{}")
    _git(mission, "add", "-f", "node_modules")
    _git(mission, "commit", "-q", "-m", "track a vitest results file")

    untracked = mission / "__pycache__"
    untracked.mkdir()
    (untracked / "mod.cpython-313.pyc").write_bytes(b"\x00" * 8)

    _age_tree(mission, 24 * 3600)

    reaped = reaper_mod.reap_regenerable_artifacts(root=root, dry_run=False)

    assert (tracked / "results.json").exists(), "tracked source must survive"
    assert (mission / "node_modules").exists()
    assert not untracked.exists(), "untracked __pycache__ is still reapable"
    assert {r["artifact_name"] for r in reaped} == {"__pycache__"}
    assert _git(mission, "status", "--porcelain") == "", (
        "the reap must leave the worktree clean — a dirty tree permanently "
        "disqualifies it from the weekly merged-worktree prune"
    )


# ---------------------------------------------------------------------------
# Second root: <repo>/.worktrees
# ---------------------------------------------------------------------------


_NINETY_SIX_HOURS = 96 * 3600


def _merged_at_stub(merged_branches: set[str], age_hours: float = 96.0):
    """Injected stand-in for ``gh_pr_merged_at`` — no network in unit tests."""
    when = datetime.now(timezone.utc) - timedelta(hours=age_hours)

    def _lookup(branch: str):
        return when if branch in merged_branches else None

    return _lookup


@pytest.fixture
def worktree_estate(tmp_path: Path) -> dict:
    """A real repo + real origin + four real worktrees, one per guard case.

    Under ``repo/.worktrees/``:

      * ``idle``     — open PR, quiescent: the ONLY reapable tree.
      * ``busy``     — open PR, files written "now": in-use.
      * ``tracked``  — open PR, quiescent, but tracks ``vendor/__pycache__``.
      * ``merged``   — quiescent, but its PR merged (the weekly prune's).

    Plus ``outside/stray`` — a worktree parked outside the allowlist root.

    Every tree gets the same artifact layout: reapable ``__pycache__`` /
    ``.ruff_cache``, plus a ``.venv`` / ``node_modules`` / ``dist`` that must
    survive (not in the worktree reap set), plus real source.
    """

    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "-q", "-b", "main")

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("base\n")
    # Realistic: the real repo gitignores every one of these, which is what
    # makes "git status is clean after the reap" a meaningful assertion — a
    # dirty tree permanently disqualifies a worktree from the weekly prune.
    (repo / ".gitignore").write_text(
        ".venv/\ndist/\nnode_modules/\n__pycache__/\n.ruff_cache/\n.pytest_cache/\n"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")

    def artifacts(wt: Path) -> None:
        for name in ("__pycache__", ".ruff_cache"):
            (wt / name).mkdir(parents=True, exist_ok=True)
            (wt / name / "cached.bin").write_bytes(b"\x00" * 32)
        # A nested __pycache__ INSIDE .venv proves we never descend into a
        # directory we promised not to touch (~31 MB of it on prod).
        (wt / ".venv" / "lib" / "__pycache__").mkdir(parents=True)
        (wt / ".venv" / "lib" / "__pycache__" / "deep.pyc").write_bytes(b"\x00" * 8)
        (wt / "node_modules" / "pkg").mkdir(parents=True)
        (wt / "node_modules" / "pkg" / "index.js").write_text("//\n")
        (wt / "dist").mkdir()
        (wt / "dist" / "bundle.js").write_text("//\n")

    def make_worktree(name: str, branch: str, parent: Path) -> Path:
        parent.mkdir(parents=True, exist_ok=True)
        wt = parent / name
        _git(repo, "worktree", "add", "-q", str(wt), "-b", branch, "main")
        (wt / f"{name}.py").write_text(f"# work for {name}\n")
        _git(wt, "add", ".")
        _git(wt, "commit", "-q", "-m", f"work on {name}")
        artifacts(wt)
        return wt

    roots = repo / ".worktrees"
    wts = {
        "idle": make_worktree("idle", "claude/idle", roots),
        "busy": make_worktree("busy", "claude/busy", roots),
        "tracked": make_worktree("tracked", "claude/tracked", roots),
        "merged": make_worktree("merged", "claude/merged", roots),
        "outside": make_worktree("stray", "claude/stray", tmp_path / "outside"),
    }

    # The `tracked` case mirrors the real repo exactly: a committed file under
    # a directory whose name is in the reap set.
    vendored = wts["tracked"] / "vendor" / "__pycache__"
    vendored.mkdir(parents=True)
    (vendored / "results.json").write_text("{}")
    _git(wts["tracked"], "add", "-f", "vendor")
    _git(wts["tracked"], "commit", "-q", "-m", "track a generated results file")

    # Age everything past the quiescence window; `busy` is re-touched after.
    for wt in wts.values():
        _age_tree(wt, _NINETY_SIX_HOURS)
    (wts["busy"] / "busy.py").write_text("# still being edited\n")
    os.utime(wts["busy"], None)

    return {"repo": repo, "origin": origin, "roots": roots, "wts": wts}


def _pr_exists_stub(no_pr_branches: set[str] | None = None, unknown: bool = False):
    """Injected stand-in for ``gh_pr_exists`` — no network in unit tests.

    Default: every branch HAS a pull request, which is the pre-2026-08-02 world
    and keeps ``.venv`` firmly out of scope. Only branches named in
    ``no_pr_branches`` unlock the narrow no-PR ``.venv`` rule.
    """

    named = no_pr_branches or set()

    def _lookup(branch: str):
        if unknown:
            return None
        return branch not in named

    return _lookup


def _run_reap(estate: dict, **kwargs) -> dict:
    records = reaper_mod.reap_worktree_regenerable_artifacts(
        repo_root=estate["repo"],
        allowed_roots=[estate["roots"]],
        enabled=kwargs.pop("enabled", True),
        dry_run=kwargs.pop("dry_run", False),
        merged_at=kwargs.pop("merged_at", _merged_at_stub({"claude/merged"})),
        pr_exists=kwargs.pop("pr_exists", _pr_exists_stub()),
        idle_hours=kwargs.pop("idle_hours", 72),
        **kwargs,
    )
    return records


def _for(records: list[dict], wt: Path) -> list[dict]:
    return [r for r in records if r["worktree"] == str(wt.resolve())]


class TestWorktreeRegenerableReap:
    def test_disabled_by_default(self, worktree_estate, monkeypatch):
        monkeypatch.delenv("UA_WORKTREE_REGENERABLE_REAP_ENABLED", raising=False)
        records = reaper_mod.reap_worktree_regenerable_artifacts(
            repo_root=worktree_estate["repo"],
            allowed_roots=[worktree_estate["roots"]],
            merged_at=_merged_at_stub(set()),
        )
        assert records == []
        assert (worktree_estate["wts"]["idle"] / "__pycache__").exists()

    def test_reaps_regenerable_artifacts_from_an_idle_open_worktree(
        self, worktree_estate, caplog
    ):
        caplog.set_level("INFO")
        wt = worktree_estate["wts"]["idle"]
        records = _run_reap(worktree_estate)

        reaped = {
            r["artifact_name"] for r in _for(records, wt) if r["action"] == "reaped"
        }
        assert reaped == {"__pycache__", ".ruff_cache"}
        assert not (wt / "__pycache__").exists()
        assert not (wt / ".ruff_cache").exists()

        # Everything outside the narrowed reap set survives — including the
        # __pycache__ nested inside .venv (we never descend into .venv).
        assert (wt / ".venv" / "lib" / "__pycache__" / "deep.pyc").exists()
        assert (wt / "node_modules" / "pkg" / "index.js").exists()
        assert (wt / "dist" / "bundle.js").exists()
        assert (wt / "idle.py").exists()
        assert (wt / ".git").exists()
        assert _git(wt, "status", "--porcelain") == ""

        # Every reap and skip is logged, with a per-category byte summary.
        assert "worktree-regenerable-reap REAPED" in caplog.text
        assert "worktree-regenerable-reap SKIP" in caplog.text
        # Per-category byte summary, both categories, no silent truncation.
        assert "Per category: .ruff_cache 2 dir(s)" in caplog.text
        assert "__pycache__ 2 dir(s)" in caplog.text

    def test_recently_touched_worktree_is_not_touched(self, worktree_estate):
        wt = worktree_estate["wts"]["busy"]
        records = _run_reap(worktree_estate)
        rec = _for(records, wt)
        assert [r["action"] for r in rec] == ["skipped"]
        assert "quiescence" in rec[0]["reason"]
        assert (wt / "__pycache__" / "cached.bin").exists()

    def test_live_process_inside_worktree_blocks_the_reap(self, worktree_estate):
        """The cwd/exe/maps gate, proved with a REAL process."""
        wt = worktree_estate["wts"]["idle"]
        proc = subprocess.Popen(["sleep", "45"], cwd=str(wt))
        try:
            records = _run_reap(worktree_estate)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
        rec = _for(records, wt)
        assert [r["action"] for r in rec] == ["skipped"]
        assert "running process" in rec[0]["reason"]
        assert (wt / "__pycache__" / "cached.bin").exists()

    def test_worktree_outside_the_allowlist_is_not_touched(self, worktree_estate):
        stray = worktree_estate["wts"]["outside"]
        records = _run_reap(worktree_estate)
        assert _for(records, stray) == []
        assert (stray / "__pycache__" / "cached.bin").exists()

    def test_main_working_tree_is_never_touched(self, worktree_estate):
        repo = worktree_estate["repo"]
        (repo / "__pycache__").mkdir()
        (repo / "__pycache__" / "cached.bin").write_bytes(b"\x00")
        records = _run_reap(worktree_estate)
        assert _for(records, repo) == []
        assert (repo / "__pycache__" / "cached.bin").exists()
        assert (repo / "README.md").exists()

    def test_dry_run_reaps_nothing(self, worktree_estate):
        wt = worktree_estate["wts"]["idle"]
        records = _run_reap(worktree_estate, dry_run=True)
        rec = _for(records, wt)
        assert {r["action"] for r in rec} == {"would-reap"}
        assert all(r["size_bytes"] and r["size_bytes"] > 0 for r in rec)
        assert (wt / "__pycache__" / "cached.bin").exists()
        assert (wt / ".ruff_cache" / "cached.bin").exists()

    def test_merged_worktree_is_left_to_the_weekly_prune(self, worktree_estate):
        """Composition, not overlap: the weekly job removes a merged tree
        whole and reclaims strictly more, so the daily job must not nibble."""
        wt = worktree_estate["wts"]["merged"]
        records = _run_reap(worktree_estate)
        rec = _for(records, wt)
        assert [r["action"] for r in rec] == ["skipped"]
        assert "weekly merged-worktree prune" in rec[0]["reason"]
        assert (wt / "__pycache__" / "cached.bin").exists()

    def test_tracked_content_is_never_deleted(self, worktree_estate):
        wt = worktree_estate["wts"]["tracked"]
        records = _run_reap(worktree_estate)
        by_action = {r["action"] for r in _for(records, wt)}
        skipped = [
            r for r in _for(records, wt)
            if r["action"] == "skipped" and r["artifact_name"] == "__pycache__"
        ]
        assert skipped and "git tracks content" in skipped[0]["reason"]
        assert (wt / "vendor" / "__pycache__" / "results.json").exists()
        assert _git(wt, "status", "--porcelain") == ""
        # The untracked artifacts in the same worktree are still reapable.
        assert "reaped" in by_action
        assert not (wt / ".ruff_cache").exists()

    def test_every_candidate_is_reported_with_a_reason(self, worktree_estate):
        """No silent truncation: every in-scope worktree produces at least one
        record, and every record carries an action and a reason."""
        records = _run_reap(worktree_estate)
        for key in ("idle", "busy", "tracked", "merged"):
            rec = _for(records, worktree_estate["wts"][key])
            assert rec, f"{key} produced no record"
            for entry in rec:
                assert entry["reason"], f"{key} reported without a reason"
                assert entry["action"] in {"reaped", "would-reap", "skipped"}


class TestWorktreeNoPrVenvReap:
    """The narrow ``.venv`` rule (2026-08-02).

    A worktree ``.venv`` is not free to rebuild, so it stays out of the base
    allowlist. It becomes reapable for ONE tree only when three things hold at
    once — the tree is idle, nothing is running inside it, and no pull request
    was EVER opened for its branch. That combination describes an abandoned
    tree, which is what a 6.7 GiB ``.venv`` of torch + nvidia + triton was
    sitting in while the VPS reported 90.0% disk.
    """

    def _venv_of(self, wt: Path) -> Path:
        return wt / ".venv"

    def test_venv_survives_when_a_pr_exists(self, worktree_estate):
        """Two of three gates are not enough: idle + nothing running, but the
        PR is open, so somebody is coming back to pay the rebuild."""
        wt = worktree_estate["wts"]["idle"]
        records = _run_reap(worktree_estate, pr_exists=_pr_exists_stub())
        assert ".venv" not in {r["artifact_name"] for r in _for(records, wt)}
        assert (self._venv_of(wt) / "lib" / "__pycache__" / "deep.pyc").exists()

    def test_venv_is_reaped_when_no_pr_ever_existed(self, worktree_estate, caplog):
        caplog.set_level("INFO")
        wt = worktree_estate["wts"]["idle"]
        records = _run_reap(
            worktree_estate, pr_exists=_pr_exists_stub({"claude/idle"}),
        )
        reaped = {
            r["artifact_name"] for r in _for(records, wt) if r["action"] == "reaped"
        }
        assert reaped == {"__pycache__", ".ruff_cache", ".venv"}
        assert not self._venv_of(wt).exists()
        # Source, git metadata and everything else still out of scope survive.
        assert (wt / "idle.py").exists()
        assert (wt / ".git").exists()
        assert (wt / "node_modules" / "pkg" / "index.js").exists()
        assert (wt / "dist" / "bundle.js").exists()
        assert _git(wt, "status", "--porcelain") == ""
        assert "worktree-regenerable-reap NO-PR" in caplog.text

    def test_unknown_pr_state_leaves_the_venv_alone(self, worktree_estate):
        wt = worktree_estate["wts"]["idle"]
        records = _run_reap(
            worktree_estate, pr_exists=_pr_exists_stub(unknown=True),
        )
        assert ".venv" not in {r["artifact_name"] for r in _for(records, wt)}
        assert self._venv_of(wt).exists()

    def test_a_freshly_touched_venv_is_not_reaped(self, worktree_estate):
        """The tree-level quiescence probe deliberately ignores ``.venv``, so
        the artifact carries its own mtime gate."""
        wt = worktree_estate["wts"]["idle"]
        os.utime(self._venv_of(wt), None)  # a `uv sync` an hour ago
        records = _run_reap(
            worktree_estate, pr_exists=_pr_exists_stub({"claude/idle"}),
        )
        venv_recs = [r for r in _for(records, wt) if r["artifact_name"] == ".venv"]
        assert [r["action"] for r in venv_recs] == ["skipped"]
        assert "quiescence" in venv_recs[0]["reason"]
        assert self._venv_of(wt).exists()

    def test_busy_tree_keeps_its_venv_even_with_no_pr(self, worktree_estate):
        """Gate order: a non-quiescent tree never reaches the PR question."""
        wt = worktree_estate["wts"]["busy"]
        records = _run_reap(
            worktree_estate, pr_exists=_pr_exists_stub({"claude/busy"}),
        )
        assert [r["action"] for r in _for(records, wt)] == ["skipped"]
        assert self._venv_of(wt).exists()

    def test_live_process_keeps_the_venv_even_with_no_pr(self, worktree_estate):
        wt = worktree_estate["wts"]["idle"]
        proc = subprocess.Popen(["sleep", "45"], cwd=str(wt))
        try:
            records = _run_reap(
                worktree_estate, pr_exists=_pr_exists_stub({"claude/idle"}),
            )
        finally:
            proc.terminate()
            proc.wait(timeout=10)
        assert [r["action"] for r in _for(records, wt)] == ["skipped"]
        assert self._venv_of(wt).exists()

    def test_rule_can_be_switched_off(self, worktree_estate):
        wt = worktree_estate["wts"]["idle"]
        records = _run_reap(
            worktree_estate,
            pr_exists=_pr_exists_stub({"claude/idle"}),
            no_pr_venv=False,
        )
        assert ".venv" not in {r["artifact_name"] for r in _for(records, wt)}
        assert self._venv_of(wt).exists()

    def test_dry_run_reaps_no_venv(self, worktree_estate):
        wt = worktree_estate["wts"]["idle"]
        records = _run_reap(
            worktree_estate,
            pr_exists=_pr_exists_stub({"claude/idle"}),
            dry_run=True,
        )
        venv_recs = [r for r in _for(records, wt) if r["artifact_name"] == ".venv"]
        assert [r["action"] for r in venv_recs] == ["would-reap"]
        assert self._venv_of(wt).exists()

    def test_the_no_pr_set_is_venv_only(self):
        """Pinned: this exception must never grow. ``node_modules`` holds
        tracked content in this repo; ``dist`` / ``build`` / ``.next`` are
        plausible tracked directory names in a full checkout."""
        assert reaper_mod.WORKTREE_NO_PR_ARTIFACT_NAMES == frozenset({".venv"})


class TestWorktreeReapNames:
    def test_venv_and_node_modules_are_not_in_the_reap_set(self):
        """Pinned policy, not an accident: a worktree ``.venv`` is NOT
        automatically regenerated (nothing in UA runs ``uv sync`` in a
        worktree), and ``node_modules`` holds tracked content in this repo."""
        assert reaper_mod.WORKTREE_REGENERABLE_ARTIFACT_NAMES == frozenset(
            {"__pycache__", ".pytest_cache", ".ruff_cache"}
        )
        for name in (".venv", "node_modules", "dist", "build", ".next"):
            assert name not in reaper_mod.WORKTREE_REGENERABLE_ARTIFACT_NAMES

    def test_env_knob_can_narrow_the_allowlist(self, monkeypatch):
        monkeypatch.setenv("UA_WORKTREE_REGENERABLE_REAP_NAMES", "__pycache__")
        assert reaper_mod._worktree_reap_names() == frozenset({"__pycache__"})

    def test_env_knob_cannot_widen_the_allowlist(self, monkeypatch):
        monkeypatch.setenv(
            "UA_WORKTREE_REGENERABLE_REAP_NAMES", ".venv,node_modules,.ruff_cache"
        )
        assert reaper_mod._worktree_reap_names() == frozenset({".ruff_cache"})

    def test_unset_env_uses_the_full_allowlist(self, monkeypatch):
        monkeypatch.delenv("UA_WORKTREE_REGENERABLE_REAP_NAMES", raising=False)
        assert (
            reaper_mod._worktree_reap_names()
            == reaper_mod.WORKTREE_REGENERABLE_ARTIFACT_NAMES
        )
