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


def test_resolve_artifacts_dir_honors_env_override(
    monkeypatch, tmp_path: Path
) -> None:
    """An explicit UA_ARTIFACTS_DIR wins and is returned resolved — this is the
    bootstrap path (remote_deploy.sh pins it to <PROD_DIR>/artifacts)."""
    override = tmp_path / "explicit_artifacts"
    override.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("UA_ARTIFACTS_DIR", str(override))
    # repo_root is irrelevant when the env var is set; pin it elsewhere to prove
    # the env branch wins regardless.
    monkeypatch.setattr(artifacts_module, "repo_root", lambda: tmp_path / "unused")

    resolved = artifacts_module.resolve_artifacts_dir()
    assert resolved == override.resolve()


def test_resolve_artifacts_dir_default_is_repo_artifacts_not_literal_dir(
    monkeypatch, tmp_path: Path
) -> None:
    """With the env unset and a real <root>/artifacts present, the resolver
    returns <root>/artifacts — never a literal-named ``UA_ARTIFACTS_DIR`` dir.
    Regression pin for the literal-relative-dir orphan class."""
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("UA_ARTIFACTS_DIR", raising=False)
    monkeypatch.setattr(artifacts_module, "repo_root", lambda: tmp_path)

    resolved = artifacts_module.resolve_artifacts_dir()
    assert resolved == (tmp_path / "artifacts").resolve()
    assert resolved.name == "artifacts"
    assert resolved.name != "UA_ARTIFACTS_DIR"
