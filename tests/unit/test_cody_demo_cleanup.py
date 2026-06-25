"""Tests for the guarded Cody-demo scratch reclaim (services/cody_demo_cleanup.py).

The headline invariant under test is the HARD GUARD: heavy leaves are never
stripped until the demo is confirmed vault-attached (registered under the demos
root OR named in a vault ``## Demos`` bullet).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import time

import pytest

from universal_agent.services import cody_demo_cleanup as cdc

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def scratch_tree(tmp_path: Path) -> tuple[Path, Path, str]:
    """Build a fake scratch root with one aged, double-nested vp-mission dir.

    Returns (scratch_root, mission_dir, demo_id).
    """
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    demo_id = "my-cool-demo"
    mission = scratch_root / "vp-mission-test123"
    inner = mission / "vp-mission-test123"
    inner.mkdir(parents=True)

    # capabilities.md carries the demo_id (mirrors real layout).
    (inner / "capabilities.md").write_text(
        f"# Capabilities\n\ndemo_id: {demo_id}\nentity_slug: some-entity\n",
        encoding="utf-8",
    )
    # Audit files that must SURVIVE a reclaim.
    (inner / "run.log").write_text("ran fine\n", encoding="utf-8")
    (inner / "manifest.json").write_text(
        json.dumps({"demo_id": demo_id, "endpoint_hit": "zai"}), encoding="utf-8"
    )

    # Heavy leaves that are the reclaim targets.
    nm = inner / "project" / "node_modules"
    nm.mkdir(parents=True)
    (nm / "big.js").write_text("x" * 4096, encoding="utf-8")
    gitdir = inner / "project" / ".git"
    gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    # Age the mission dir so it clears the default 48h floor.
    old = time.time() - (72 * 3600)
    os.utime(mission, (old, old))
    os.utime(inner, (old, old))

    return scratch_root, mission, demo_id


@pytest.fixture()
def demos_root(tmp_path: Path) -> Path:
    root = tmp_path / "demos"
    root.mkdir()
    return root


@pytest.fixture()
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "entities").mkdir(parents=True)
    return root


def _attach_in_vault(vault_root: Path, entity_slug: str, demo_id: str) -> None:
    page = vault_root / "entities" / f"{entity_slug}.md"
    page.write_text(
        f"# {entity_slug}\n\n## Demos\n\n- `{demo_id}` — `/opt/ua_demos/{demo_id}` — ok\n",
        encoding="utf-8",
    )


# ── HARD GUARD: don't strip until vault-attached ─────────────────────────────


def test_guard_skips_when_not_vault_attached(scratch_tree, demos_root, vault_root):
    scratch_root, mission, demo_id = scratch_tree
    # Demo is NOT registered and NOT in the vault.
    report = cdc.reclaim_mission_scratch(
        mission,
        demos_root=demos_root,
        vault_root=vault_root,
        dry_run=False,
    )
    assert report.action == "skipped"
    assert report.reason == "not_vault_attached"
    assert report.demo_id == demo_id
    # Leaves must be untouched.
    assert (mission / "vp-mission-test123" / "project" / "node_modules").exists()
    assert (mission / "vp-mission-test123" / "project" / ".git").exists()


def test_guard_skips_when_not_vault_attached_via_scan(scratch_tree, demos_root, vault_root):
    """The scan-level entry point must also honor the guard end-to-end."""
    scratch_root, _mission, _demo_id = scratch_tree
    summary = cdc.reclaim_coder_mission_workspaces(
        root=scratch_root,
        demos_root=demos_root,
        vault_root=vault_root,
        dry_run=False,
        enabled=True,
    )
    assert summary["scanned"] == 1
    assert summary["by_action"].get("skipped") == 1
    assert summary["by_reason"].get("not_vault_attached") == 1
    assert summary["bytes_freed"] == 0


# ── Vault-attached: strip leaves, preserve audit files ───────────────────────


def test_strips_when_registered_under_demos_root(scratch_tree, demos_root, vault_root):
    scratch_root, mission, demo_id = scratch_tree
    (demos_root / demo_id).mkdir()  # registered → vault-attached

    report = cdc.reclaim_mission_scratch(
        mission,
        demos_root=demos_root,
        vault_root=vault_root,
        dry_run=False,
    )
    assert report.action == "stripped"
    assert report.demo_id == demo_id
    # Both heavy leaves gone...
    inner = mission / "vp-mission-test123"
    assert not (inner / "project" / "node_modules").exists()
    assert not (inner / "project" / ".git").exists()
    # ...but audit files preserved.
    assert (inner / "run.log").exists()
    assert (inner / "capabilities.md").exists()
    assert (inner / "manifest.json").exists()
    assert report.bytes_freed > 0


def test_strips_when_attached_in_vault_entity(scratch_tree, demos_root, vault_root):
    scratch_root, mission, demo_id = scratch_tree
    _attach_in_vault(vault_root, "some-entity", demo_id)  # vault bullet → attached

    report = cdc.reclaim_mission_scratch(
        mission,
        demos_root=demos_root,
        vault_root=vault_root,
        dry_run=False,
    )
    assert report.action == "stripped"
    inner = mission / "vp-mission-test123"
    assert not (inner / "project" / "node_modules").exists()


# ── Age floor ────────────────────────────────────────────────────────────────


def test_age_floor_skips_too_new(scratch_tree, demos_root, vault_root):
    scratch_root, mission, demo_id = scratch_tree
    (demos_root / demo_id).mkdir()  # attached...
    # ...but make it brand new.
    now = time.time()
    os.utime(mission, (now, now))

    report = cdc.reclaim_mission_scratch(
        mission,
        demos_root=demos_root,
        vault_root=vault_root,
        dry_run=False,
        min_age_hours=48,
        now=now + 60,
    )
    assert report.action == "skipped"
    assert report.reason.startswith("too_new")
    assert (mission / "vp-mission-test123" / "project" / "node_modules").exists()


# ── Dry run ──────────────────────────────────────────────────────────────────


def test_dry_run_reports_but_does_not_delete(scratch_tree, demos_root, vault_root):
    scratch_root, mission, demo_id = scratch_tree
    (demos_root / demo_id).mkdir()

    report = cdc.reclaim_mission_scratch(
        mission,
        demos_root=demos_root,
        vault_root=vault_root,
        dry_run=True,
    )
    assert report.action == "dry_run"
    assert report.bytes_freed > 0
    # Nothing actually removed.
    assert (mission / "vp-mission-test123" / "project" / "node_modules").exists()
    assert (mission / "vp-mission-test123" / "project" / ".git").exists()


# ── Feature disabled ─────────────────────────────────────────────────────────


def test_disabled_is_noop(scratch_tree, demos_root, vault_root):
    scratch_root, mission, demo_id = scratch_tree
    (demos_root / demo_id).mkdir()

    summary = cdc.reclaim_coder_mission_workspaces(
        root=scratch_root,
        demos_root=demos_root,
        vault_root=vault_root,
        enabled=False,
        dry_run=False,
    )
    assert summary["enabled"] is False
    assert summary["scanned"] == 0
    assert (mission / "vp-mission-test123" / "project" / "node_modules").exists()


# ── Misc ─────────────────────────────────────────────────────────────────────


def test_no_demo_id_is_skipped(tmp_path, demos_root, vault_root):
    scratch_root = tmp_path / "scratch"
    mission = scratch_root / "vp-mission-noMeta"
    (mission / "vp-mission-noMeta").mkdir(parents=True)
    (mission / "vp-mission-noMeta" / "project" / "node_modules").mkdir(parents=True)
    old = time.time() - (72 * 3600)
    os.utime(mission, (old, old))
    scratch_root.mkdir(parents=True, exist_ok=True)

    report = cdc.reclaim_mission_scratch(
        mission, demos_root=demos_root, vault_root=vault_root, dry_run=False
    )
    assert report.action == "skipped"
    assert report.reason == "no_demo_id"


def test_symlink_leaf_outside_scratch_is_not_targeted(tmp_path, demos_root, vault_root):
    """A heavy-leaf dir that is a symlink pointing OUT of the scratch must be skipped."""
    scratch_root = tmp_path / "scratch"
    mission = scratch_root / "vp-mission-symlink"
    inner = mission / "vp-mission-symlink"
    inner.mkdir(parents=True)
    (inner / "capabilities.md").write_text("demo_id: demo-symlink\n", encoding="utf-8")

    # External tree masquerading as node_modules via symlink.
    external = tmp_path / "external_nm"
    external.mkdir()
    (external / "stolen.js").write_text("y" * 100, encoding="utf-8")
    (inner / "node_modules").symlink_to(external, target_is_directory=True)

    (demos_root / "demo-symlink").mkdir()  # attached
    old = time.time() - (72 * 3600)
    os.utime(mission, (old, old))

    leaves = cdc.find_heavy_leaves(mission, cdc.DEFAULT_LEAVES)
    assert leaves == [], "symlinked leaf resolving outside scratch must not be targeted"
    assert external.exists(), "external target must not be deleted"


# ── CLI safety: must default to a dry run ────────────────────────────────────


def test_cli_defaults_to_dry_run_no_deletes(scratch_tree, demos_root, vault_root, capsys):
    """The CLI MUST default to a dry run (no deletes) — the safe contract.

    A regression here (CLI defaulting to live reclaim) would be a data-loss bug.
    """
    from universal_agent.scripts.cody_demo_cleanup import main

    scratch_root, mission, demo_id = scratch_tree
    (demos_root / demo_id).mkdir()  # attached

    rc = main(
        [
            "--root",
            str(scratch_root),
            "--demos-root",
            str(demos_root),
            "--vault-root",
            str(vault_root),
            "--min-age-hours",
            "0",
            "--json",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert '"dry_run": true' in out
    assert '"action": "dry_run"' in out
    # Nothing deleted.
    assert (mission / "vp-mission-test123" / "project" / "node_modules").exists()

