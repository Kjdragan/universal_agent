from __future__ import annotations

from pathlib import Path

from universal_agent import artifacts as artifacts_module


def test_resolve_artifacts_dir_prefers_default_root_over_legacy(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "UA_ARTIFACTS_DIR").mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("UA_ARTIFACTS_DIR", raising=False)
    monkeypatch.setattr(artifacts_module, "repo_root", lambda: tmp_path)

    resolved = artifacts_module.resolve_artifacts_dir()
    assert resolved == (tmp_path / "artifacts").resolve()


def test_resolve_artifacts_dir_uses_legacy_when_default_missing(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / "UA_ARTIFACTS_DIR").mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("UA_ARTIFACTS_DIR", raising=False)
    monkeypatch.setattr(artifacts_module, "repo_root", lambda: tmp_path)

    resolved = artifacts_module.resolve_artifacts_dir()
    assert resolved == (tmp_path / "UA_ARTIFACTS_DIR").resolve()
