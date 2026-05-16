"""Unit tests for scripts/backfill_demo_attachments.py (Phase 4 A2 backfill)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_agent.scripts import backfill_demo_attachments as backfill


def _write_demo_workspace(workspace: Path, *, demo_id: str, endpoint_hit: str = "anthropic_native") -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "manifest.json").write_text(
        json.dumps(
            {
                "demo_id": demo_id,
                "feature": demo_id.split("__")[0],
                "endpoint_required": "anthropic_native",
                "endpoint_hit": endpoint_hit,
                "acceptance_passed": True,
                "iteration": 1,
            }
        ),
        encoding="utf-8",
    )
    (workspace / "BRIEF.md").write_text("brief", encoding="utf-8")
    (workspace / "ACCEPTANCE.md").write_text("1. ok", encoding="utf-8")
    (workspace / "business_relevance.md").write_text("value", encoding="utf-8")


def _write_entity(vault: Path, slug: str) -> Path:
    entities = vault / "entities"
    entities.mkdir(parents=True, exist_ok=True)
    path = entities / f"{slug}.md"
    path.write_text(f"---\ntitle: {slug}\n---\n\n# {slug}\n\nBody.\n", encoding="utf-8")
    return path


def test_parse_entity_slug_default_convention() -> None:
    assert backfill._parse_entity_slug("custom-subagents__demo-1", mapping={}) == "custom-subagents"
    assert backfill._parse_entity_slug("webhooks__demo-3", mapping={}) == "webhooks"


def test_parse_entity_slug_uses_mapping_override() -> None:
    out = backfill._parse_entity_slug("e3rneinuzx__demo-1", mapping={"e3rneinuzx__demo-1": "ultrareview"})
    assert out == "ultrareview"


def test_parse_entity_slug_falls_back_to_workspace_name_for_unconventional_names() -> None:
    assert backfill._parse_entity_slug("oddball", mapping={}) == "oddball"


def test_parse_mappings_validates_syntax() -> None:
    with pytest.raises(SystemExit):
        backfill._parse_mappings(["bad-no-equals"])
    with pytest.raises(SystemExit):
        backfill._parse_mappings(["=missing-name"])
    with pytest.raises(SystemExit):
        backfill._parse_mappings(["missing-slug="])

    assert backfill._parse_mappings(["a=b", "c=d"]) == {"a": "b", "c": "d"}


def test_backfill_attaches_to_matching_entity(tmp_path: Path) -> None:
    demos = tmp_path / "demos"
    vault = tmp_path / "vault"
    workspace = demos / "skills__demo-1"
    _write_demo_workspace(workspace, demo_id="skills__demo-1")
    entity_path = _write_entity(vault, "skills")

    succeeded, attempted = backfill.backfill(
        demos_root=demos,
        vault_root=vault,
        mapping={},
        dry_run=False,
    )

    assert succeeded == 1
    assert attempted == 1
    body = entity_path.read_text(encoding="utf-8")
    assert "## Demos" in body
    assert "skills__demo-1" in body
    assert "anthropic_native" in body


def test_backfill_skips_when_no_manifest(tmp_path: Path) -> None:
    demos = tmp_path / "demos"
    vault = tmp_path / "vault"
    (demos / "broken__demo-1").mkdir(parents=True)
    _write_entity(vault, "broken")

    succeeded, attempted = backfill.backfill(
        demos_root=demos,
        vault_root=vault,
        mapping={},
        dry_run=False,
    )

    assert succeeded == 0
    assert attempted == 1


def test_backfill_skips_when_entity_missing(tmp_path: Path, caplog) -> None:
    demos = tmp_path / "demos"
    vault = tmp_path / "vault"
    workspace = demos / "missing-entity__demo-1"
    _write_demo_workspace(workspace, demo_id="missing-entity__demo-1")
    (vault / "entities").mkdir(parents=True)

    with caplog.at_level("WARNING"):
        succeeded, attempted = backfill.backfill(
            demos_root=demos,
            vault_root=vault,
            mapping={},
            dry_run=False,
        )

    assert succeeded == 0
    assert attempted == 1
    assert any("no entity page" in rec.message for rec in caplog.records)


def test_backfill_dry_run_does_not_modify_pages(tmp_path: Path) -> None:
    demos = tmp_path / "demos"
    vault = tmp_path / "vault"
    workspace = demos / "skills__demo-1"
    _write_demo_workspace(workspace, demo_id="skills__demo-1")
    entity_path = _write_entity(vault, "skills")
    original = entity_path.read_text(encoding="utf-8")

    succeeded, attempted = backfill.backfill(
        demos_root=demos,
        vault_root=vault,
        mapping={},
        dry_run=True,
    )

    assert succeeded == 1
    assert attempted == 1
    assert entity_path.read_text(encoding="utf-8") == original


def test_main_returns_zero_on_success(tmp_path: Path) -> None:
    demos = tmp_path / "demos"
    vault = tmp_path / "vault"
    workspace = demos / "skills__demo-1"
    _write_demo_workspace(workspace, demo_id="skills__demo-1")
    _write_entity(vault, "skills")

    rc = backfill.main([
        "--demos-root", str(demos),
        "--vault-root", str(vault),
    ])
    assert rc == 0


def test_main_returns_one_when_no_workspaces(tmp_path: Path) -> None:
    demos = tmp_path / "demos"
    demos.mkdir()
    rc = backfill.main([
        "--demos-root", str(demos),
        "--vault-root", str(tmp_path / "vault"),
    ])
    assert rc == 1
