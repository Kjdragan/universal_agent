"""Tests for the Phase 0 upgrade actuator (PR 6b)."""

from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent.services import dependency_upgrade
from universal_agent.services.dependency_upgrade import (
    SmokeResult,
    UpgradeOutcome,
    apply_upgrade,
    bump_pyproject_dep,
    build_upgrade_email,
    find_pyproject_dep,
    restore_pyproject,
)


# ── pyproject surgery ────────────────────────────────────────────────────────


PYPROJECT_SAMPLE = '''\
[project]
name = "universal-agent"
version = "0.1.0"
dependencies = [
    "anthropic>=0.75.0",
    "claude-agent-sdk>=0.1.51",
    "composio-claude-agent-sdk>=0.10.6,<1.0.0",
    "fastapi>=0.115.0",
    "langsmith[claude-agent-sdk,otel]>=0.7.22",
]
'''


def test_find_dep_locates_simple_dep():
    found = find_pyproject_dep(PYPROJECT_SAMPLE, "anthropic")
    assert found is not None
    spec, version = found
    assert spec == ">=0.75.0"
    assert version == "0.75.0"


def test_find_dep_locates_dep_with_upper_bound():
    found = find_pyproject_dep(PYPROJECT_SAMPLE, "composio-claude-agent-sdk")
    assert found is not None
    spec, version = found
    assert spec == ">=0.10.6,<1.0.0"
    assert version == "0.10.6"


def test_find_dep_normalizes_underscore_dash():
    """PEP 503 says claude_agent_sdk and claude-agent-sdk are the same package."""
    found = find_pyproject_dep(PYPROJECT_SAMPLE, "claude_agent_sdk")
    assert found is not None
    assert found[1] == "0.1.51"


def test_find_dep_returns_none_for_missing():
    assert find_pyproject_dep(PYPROJECT_SAMPLE, "nonexistent-package") is None


def test_find_dep_handles_extras_in_name():
    """langsmith[claude-agent-sdk,otel] should match 'langsmith'."""
    found = find_pyproject_dep(PYPROJECT_SAMPLE, "langsmith")
    assert found is not None


def test_bump_dep_writes_new_version_and_returns_diff(tmp_path: Path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT_SAMPLE, encoding="utf-8")
    backup_dir = tmp_path / "backups"

    from_v, to_v, diff = bump_pyproject_dep(
        pyproject,
        package="anthropic",
        target_version="0.76.0",
        backup_dir=backup_dir,
    )
    assert from_v == "0.75.0"
    assert to_v == "0.76.0"
    assert "anthropic>=0.76.0" in pyproject.read_text(encoding="utf-8")
    assert "anthropic>=0.75.0" not in pyproject.read_text(encoding="utf-8")
    assert diff and "0.75.0" in diff and "0.76.0" in diff
    # Backup was created.
    backups = list(backup_dir.glob("pyproject.*.bak"))
    assert len(backups) == 1
    assert PYPROJECT_SAMPLE == backups[0].read_text(encoding="utf-8")


def test_bump_dep_preserves_upper_bound(tmp_path: Path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT_SAMPLE, encoding="utf-8")

    bump_pyproject_dep(
        pyproject,
        package="composio-claude-agent-sdk",
        target_version="0.11.0",
        backup_dir=tmp_path / "backups",
    )
    text = pyproject.read_text(encoding="utf-8")
    # Both the new lower bound and the original upper bound must survive.
    assert ">=0.11.0,<1.0.0" in text


def test_bump_dep_only_changes_targeted_package(tmp_path: Path):
    """Bumping anthropic must NOT touch composio-claude-agent-sdk or langsmith."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT_SAMPLE, encoding="utf-8")

    bump_pyproject_dep(
        pyproject,
        package="anthropic",
        target_version="1.0.0",
        backup_dir=tmp_path / "backups",
    )
    text = pyproject.read_text(encoding="utf-8")
    assert "claude-agent-sdk>=0.1.51" in text
    assert "composio-claude-agent-sdk>=0.10.6,<1.0.0" in text
    assert "langsmith[claude-agent-sdk,otel]>=0.7.22" in text
    assert "anthropic>=1.0.0" in text


def test_bump_dep_raises_for_missing_package(tmp_path: Path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT_SAMPLE, encoding="utf-8")

    with pytest.raises(KeyError):
        bump_pyproject_dep(
            pyproject,
            package="not-a-real-package",
            target_version="1.0.0",
            backup_dir=tmp_path / "backups",
        )


def test_bump_dep_raises_for_missing_pyproject(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        bump_pyproject_dep(
            tmp_path / "missing.toml",
            package="anthropic",
            target_version="1.0.0",
        )


def test_restore_pyproject_recovers_from_latest_backup(tmp_path: Path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT_SAMPLE, encoding="utf-8")
    backup_dir = tmp_path / "backups"

    bump_pyproject_dep(
        pyproject,
        package="anthropic",
        target_version="0.76.0",
        backup_dir=backup_dir,
    )
    assert "0.76.0" in pyproject.read_text(encoding="utf-8")

    restored_from = restore_pyproject(pyproject, backup_dir)
    assert restored_from is not None
    assert pyproject.read_text(encoding="utf-8") == PYPROJECT_SAMPLE


def test_restore_pyproject_returns_none_when_no_backups(tmp_path: Path):
    assert restore_pyproject(tmp_path / "pyproject.toml", tmp_path / "no_backups") is None


# ── Orchestration with subprocess mocked out ────────────────────────────────


def _mk_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(PYPROJECT_SAMPLE, encoding="utf-8")
    return repo


def _mk_smoke(tmp_path: Path) -> Path:
    smoke = tmp_path / "smoke_workspace"
    smoke.mkdir()
    (smoke / "smoke.py").write_text("# stub\n", encoding="utf-8")
    return smoke


def test_apply_upgrade_full_success(monkeypatch, tmp_path: Path):
    repo = _mk_repo(tmp_path)
    smoke = _mk_smoke(tmp_path)

    monkeypatch.setattr(dependency_upgrade, "run_uv_sync", lambda *, repo_root=None: (True, ""))
    monkeypatch.setattr(
        dependency_upgrade,
        "run_zai_smoke",
        lambda *, repo_root=None: SmokeResult(name="zai_smoke", ok=True, return_code=0, stdout_excerpt="OK"),
    )
    monkeypatch.setattr(
        dependency_upgrade,
        "run_anthropic_native_smoke",
        lambda *, smoke_dir=None: SmokeResult(name="anthropic_native_smoke", ok=True, return_code=0, stdout_excerpt="ok"),
    )

    outcome = apply_upgrade(
        package="anthropic",
        target_version="0.80.0",
        repo_root=repo,
        smoke_dir=smoke,
    )
    assert outcome.overall_ok
    assert outcome.from_version == "0.75.0"
    assert outcome.to_version == "0.80.0"
    assert not outcome.rolled_back
    # File was actually edited.
    assert "anthropic>=0.80.0" in (repo / "pyproject.toml").read_text(encoding="utf-8")


def test_apply_upgrade_rolls_back_on_zai_smoke_failure(monkeypatch, tmp_path: Path):
    repo = _mk_repo(tmp_path)
    smoke = _mk_smoke(tmp_path)
    original_text = (repo / "pyproject.toml").read_text(encoding="utf-8")

    monkeypatch.setattr(dependency_upgrade, "run_uv_sync", lambda *, repo_root=None: (True, ""))
    monkeypatch.setattr(
        dependency_upgrade,
        "run_zai_smoke",
        lambda *, repo_root=None: SmokeResult(
            name="zai_smoke", ok=False, return_code=1, stderr_excerpt="ImportError"
        ),
    )
    monkeypatch.setattr(
        dependency_upgrade,
        "run_anthropic_native_smoke",
        lambda *, smoke_dir=None: SmokeResult(name="anthropic_native_smoke", ok=True),
    )

    outcome = apply_upgrade(
        package="anthropic",
        target_version="9.9.9",
        repo_root=repo,
        smoke_dir=smoke,
    )
    assert not outcome.overall_ok
    assert outcome.rolled_back
    assert outcome.rollback_reason == "zai_smoke_failed"
    # File was restored.
    assert (repo / "pyproject.toml").read_text(encoding="utf-8") == original_text


def test_apply_upgrade_rolls_back_on_anthropic_smoke_failure(monkeypatch, tmp_path: Path):
    repo = _mk_repo(tmp_path)
    smoke = _mk_smoke(tmp_path)
    original_text = (repo / "pyproject.toml").read_text(encoding="utf-8")

    monkeypatch.setattr(dependency_upgrade, "run_uv_sync", lambda *, repo_root=None: (True, ""))
    monkeypatch.setattr(
        dependency_upgrade,
        "run_zai_smoke",
        lambda *, repo_root=None: SmokeResult(name="zai_smoke", ok=True),
    )
    monkeypatch.setattr(
        dependency_upgrade,
        "run_anthropic_native_smoke",
        lambda *, smoke_dir=None: SmokeResult(
            name="anthropic_native_smoke", ok=False, return_code=2, stderr_excerpt="endpoint_mismatch"
        ),
    )

    outcome = apply_upgrade(
        package="claude-agent-sdk",
        target_version="9.9.9",
        repo_root=repo,
        smoke_dir=smoke,
    )
    assert outcome.rolled_back
    assert outcome.rollback_reason == "anthropic_smoke_failed"
    assert (repo / "pyproject.toml").read_text(encoding="utf-8") == original_text


def test_apply_upgrade_rolls_back_when_both_smokes_fail(monkeypatch, tmp_path: Path):
    repo = _mk_repo(tmp_path)
    smoke = _mk_smoke(tmp_path)

    monkeypatch.setattr(dependency_upgrade, "run_uv_sync", lambda *, repo_root=None: (True, ""))
    monkeypatch.setattr(
        dependency_upgrade,
        "run_zai_smoke",
        lambda *, repo_root=None: SmokeResult(name="zai_smoke", ok=False),
    )
    monkeypatch.setattr(
        dependency_upgrade,
        "run_anthropic_native_smoke",
        lambda *, smoke_dir=None: SmokeResult(name="anthropic_native_smoke", ok=False),
    )

    outcome = apply_upgrade(
        package="anthropic",
        target_version="9.9.9",
        repo_root=repo,
        smoke_dir=smoke,
    )
    assert outcome.rolled_back
    assert outcome.rollback_reason == "both_smokes_failed"


def test_apply_upgrade_rolls_back_on_uv_sync_failure(monkeypatch, tmp_path: Path):
    repo = _mk_repo(tmp_path)
    smoke = _mk_smoke(tmp_path)
    original_text = (repo / "pyproject.toml").read_text(encoding="utf-8")

    sync_calls: list[str] = []

    def stub_sync(*, repo_root=None):
        sync_calls.append("sync")
        return False, "Resolution failed: incompatible deps"

    monkeypatch.setattr(dependency_upgrade, "run_uv_sync", stub_sync)
    # Smokes should never be reached when sync fails.
    monkeypatch.setattr(
        dependency_upgrade,
        "run_zai_smoke",
        lambda *, repo_root=None: pytest.fail("zai smoke should not run after sync failure"),
    )
    monkeypatch.setattr(
        dependency_upgrade,
        "run_anthropic_native_smoke",
        lambda *, smoke_dir=None: pytest.fail("anthropic smoke should not run after sync failure"),
    )

    outcome = apply_upgrade(
        package="anthropic",
        target_version="9.9.9",
        repo_root=repo,
        smoke_dir=smoke,
    )
    assert not outcome.sync_ok
    assert outcome.rolled_back
    assert outcome.rollback_reason == "uv_sync_failed"
    assert (repo / "pyproject.toml").read_text(encoding="utf-8") == original_text


# ── Email building ──────────────────────────────────────────────────────────


def _success_outcome() -> UpgradeOutcome:
    return UpgradeOutcome(
        package="anthropic",
        from_version="0.75.0",
        to_version="0.76.0",
        diff="--- before\n+++ after\n-old\n+new",
        sync_ok=True,
        sync_stderr_excerpt="",
        zai_smoke=SmokeResult(name="zai_smoke", ok=True, return_code=0, stdout_excerpt="OK"),
        anthropic_smoke=SmokeResult(name="anthropic_native_smoke", ok=True, return_code=0, stdout_excerpt="ok"),
        rolled_back=False,
        started_at="2026-05-05T12:00:00+00:00",
        finished_at="2026-05-05T12:01:30+00:00",
    )


def _failure_outcome() -> UpgradeOutcome:
    return UpgradeOutcome(
        package="anthropic",
        from_version="0.75.0",
        to_version="9.9.9",
        diff="(rolled back)",
        sync_ok=True,
        sync_stderr_excerpt="",
        zai_smoke=SmokeResult(name="zai_smoke", ok=False, return_code=1, stderr_excerpt="ImportError on new API"),
        anthropic_smoke=SmokeResult(name="anthropic_native_smoke", ok=True),
        rolled_back=True,
        rollback_reason="zai_smoke_failed",
        started_at="2026-05-05T12:00:00+00:00",
        finished_at="2026-05-05T12:01:30+00:00",
    )


def test_build_upgrade_email_success_subject_includes_ok():
    subject, text, html = build_upgrade_email(_success_outcome())
    assert "OK" in subject
    assert "anthropic" in subject
    assert "0.75.0" in subject and "0.76.0" in subject
    assert "ZAI smoke" in text and "PASS" in text
    assert "Anthropic-native smoke" in text and "PASS" in text
    assert "ROLLED BACK" not in text
    assert "ready for /ship" in text


def test_build_upgrade_email_failure_explains_rollback_and_what_broke():
    subject, text, html = build_upgrade_email(_failure_outcome())
    assert "FAIL" in subject
    assert "ROLLED BACK" in text
    assert "zai_smoke_failed" in text
    assert "ImportError" in text
    # The failed smoke is highlighted; the passing smoke still shows.
    assert "ZAI smoke" in text and "FAIL" in text


def test_build_upgrade_email_html_escapes_diff():
    out = _success_outcome()
    out_with_html_chars = UpgradeOutcome(**{**out.__dict__, "diff": "<script>alert('x')</script>"})
    _, _, html = build_upgrade_email(out_with_html_chars)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ── Smoke result coverage ───────────────────────────────────────────────────


def test_anthropic_smoke_skips_when_workspace_missing(tmp_path: Path):
    """No /opt/ua_demos/_smoke yet → return a clear skip, not a crash."""
    nonexistent = tmp_path / "does_not_exist"
    result = dependency_upgrade.run_anthropic_native_smoke(smoke_dir=nonexistent)
    assert result.ok is False
    assert "smoke workspace missing" in result.skipped_reason


def test_anthropic_smoke_skips_when_smoke_py_missing(tmp_path: Path):
    smoke_dir = tmp_path / "smoke"
    smoke_dir.mkdir()  # exists but no smoke.py inside
    result = dependency_upgrade.run_anthropic_native_smoke(smoke_dir=smoke_dir)
    assert result.ok is False
    assert "smoke.py missing" in result.skipped_reason
