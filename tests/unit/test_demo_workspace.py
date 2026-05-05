"""Tests for the demo workspace provisioner (PR 7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_agent.services.demo_workspace import (
    POLLUTION_INDICATORS,
    SMOKE_DEMO_DIRNAME,
    WorkspaceProvisionResult,
    demos_root,
    provision_demo_workspace,
    provision_smoke_workspace,
    verify_vanilla_settings,
    workspace_path,
)


def test_demos_root_reads_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "custom_demos"))
    assert demos_root() == (tmp_path / "custom_demos").resolve()


def test_demos_root_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("UA_DEMOS_ROOT", raising=False)
    assert demos_root() == Path("/opt/ua_demos")


def test_workspace_path_slugifies(tmp_path: Path):
    path = workspace_path("My Demo!@#", root=tmp_path)
    # safe: only alphanumerics, dashes, dots, underscores survive.
    assert path.name == "My-Demo"
    assert path.parent == tmp_path.resolve()


def test_workspace_path_rejects_pure_dot_dot(tmp_path: Path):
    # Pure '..' would escape if not blocked; provisioner must refuse it.
    with pytest.raises(ValueError):
        workspace_path("..", root=tmp_path)


def test_workspace_path_slugifies_traversal_attempt(tmp_path: Path):
    # '../etc' slugifies to '-etc' which strips to 'etc' — the slugifier IS
    # the protection. Verify the resulting path is still inside the root.
    path = workspace_path("../etc", root=tmp_path)
    assert path.parent == tmp_path.resolve()
    assert path.name and ".." not in path.parts


def test_workspace_path_rejects_empty():
    with pytest.raises(ValueError):
        workspace_path("")


def test_provision_creates_expected_files(tmp_path: Path):
    result = provision_demo_workspace("demo_test_one", root=tmp_path)
    assert isinstance(result, WorkspaceProvisionResult)
    assert result.workspace_dir.exists()
    assert (result.workspace_dir / "BRIEF.md").exists()
    assert (result.workspace_dir / "ACCEPTANCE.md").exists()
    assert (result.workspace_dir / "business_relevance.md").exists()
    assert (result.workspace_dir / "README.md").exists()
    assert (result.workspace_dir / "SOURCES").exists()
    assert result.settings_path.exists()
    assert result.is_smoke is False


def test_provision_writes_vanilla_settings(tmp_path: Path):
    result = provision_demo_workspace("demo_vanilla", root=tmp_path)
    settings = json.loads(result.settings_path.read_text(encoding="utf-8"))
    for marker in POLLUTION_INDICATORS:
        assert marker not in settings, f"marker {marker!r} leaked into scaffold"


def test_provision_refuses_to_overwrite_by_default(tmp_path: Path):
    provision_demo_workspace("demo_repeat", root=tmp_path)
    with pytest.raises(FileExistsError):
        provision_demo_workspace("demo_repeat", root=tmp_path)


def test_provision_with_overwrite_replaces(tmp_path: Path):
    first = provision_demo_workspace("demo_repeat", root=tmp_path)
    (first.workspace_dir / "stale_marker.txt").write_text("stale", encoding="utf-8")
    assert (first.workspace_dir / "stale_marker.txt").exists()

    second = provision_demo_workspace("demo_repeat", root=tmp_path, overwrite=True)
    # The stale marker must be gone after overwrite.
    assert not (second.workspace_dir / "stale_marker.txt").exists()
    assert (second.workspace_dir / "BRIEF.md").exists()


def test_provision_smoke_workspace_uses_dedicated_dir(tmp_path: Path):
    result = provision_smoke_workspace(root=tmp_path)
    assert result.workspace_dir.name == SMOKE_DEMO_DIRNAME
    assert result.is_smoke is True
    assert (result.workspace_dir / "smoke.py").exists()
    assert (result.workspace_dir / "README.md").exists()
    assert result.settings_path.exists()


def test_smoke_workspace_settings_are_vanilla(tmp_path: Path):
    result = provision_smoke_workspace(root=tmp_path)
    settings = json.loads(result.settings_path.read_text(encoding="utf-8"))
    for marker in POLLUTION_INDICATORS:
        assert marker not in settings


def test_verify_vanilla_settings_accepts_clean(tmp_path: Path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"effortLevel": "high", "permissions": {"allow": []}}), encoding="utf-8")
    # Must not raise.
    verify_vanilla_settings(p)


@pytest.mark.parametrize("marker", list(POLLUTION_INDICATORS))
def test_verify_vanilla_settings_rejects_each_pollution_marker(tmp_path: Path, marker: str):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({marker: {"some": "value"} if marker != "model" else "opus[1m]"}), encoding="utf-8")
    with pytest.raises(ValueError):
        verify_vanilla_settings(p)


def test_verify_vanilla_settings_rejects_invalid_json(tmp_path: Path):
    p = tmp_path / "settings.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        verify_vanilla_settings(p)


def test_verify_vanilla_settings_rejects_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        verify_vanilla_settings(tmp_path / "does_not_exist.json")


def test_smoke_workspace_smoke_py_is_runnable_python(tmp_path: Path):
    result = provision_smoke_workspace(root=tmp_path)
    smoke_path = result.workspace_dir / "smoke.py"
    text = smoke_path.read_text(encoding="utf-8")
    # Must compile cleanly — catches accidental syntax errors in the template.
    compile(text, str(smoke_path), "exec")


def test_provisioned_brief_files_are_placeholders(tmp_path: Path):
    """BRIEF/ACCEPTANCE/business_relevance ship as placeholders for Simone to fill in."""
    result = provision_demo_workspace("demo_placeholders", root=tmp_path)
    for fname in ("BRIEF.md", "ACCEPTANCE.md", "business_relevance.md"):
        text = (result.workspace_dir / fname).read_text(encoding="utf-8")
        assert "placeholder" in text.lower(), f"{fname} should mark itself as placeholder"
