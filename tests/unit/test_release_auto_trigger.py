"""Tests for the release-announcement auto-trigger (PR 6c)."""

from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent.services import release_auto_trigger
from universal_agent.services.dependency_upgrade import SmokeResult, UpgradeOutcome
from universal_agent.services.release_auto_trigger import (
    AutoUpgradeResult,
    ReleaseTrigger,
    auto_apply_release_triggers,
    auto_upgrade_enabled,
    extract_release_triggers,
    summarize_auto_upgrade_results,
)


# ── Env switch ───────────────────────────────────────────────────────────────


def test_auto_upgrade_default_is_on(monkeypatch):
    monkeypatch.delenv("UA_CSI_AUTO_UPGRADE_ON_RELEASE", raising=False)
    assert auto_upgrade_enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "no", "off", "FALSE", "OFF"])
def test_auto_upgrade_off_switch_values(monkeypatch, val):
    monkeypatch.setenv("UA_CSI_AUTO_UPGRADE_ON_RELEASE", val)
    assert auto_upgrade_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "yes", "on", ""])
def test_auto_upgrade_on_switch_values(monkeypatch, val):
    monkeypatch.setenv("UA_CSI_AUTO_UPGRADE_ON_RELEASE", val)
    assert auto_upgrade_enabled() is True


# ── extract_release_triggers ────────────────────────────────────────────────


def _release_action(package: str, version: str, post_id: str, *, anthropic: bool = True) -> dict:
    return {
        "post_id": post_id,
        "action_type": "release_announcement",
        "url": f"https://x.com/ClaudeDevs/status/{post_id}",
        "release_info": {
            "package": package,
            "version": version,
            "is_anthropic_adjacent": anthropic,
        },
    }


def test_extract_returns_empty_for_no_actions():
    assert extract_release_triggers([]) == []


def test_extract_filters_to_release_announcements_only():
    actions = [
        {"action_type": "digest"},
        {"action_type": "kb_update"},
        _release_action("anthropic", "0.75.0", "100"),
    ]
    triggers = extract_release_triggers(actions)
    assert len(triggers) == 1
    assert triggers[0].package == "anthropic"
    assert triggers[0].version == "0.75.0"


def test_extract_skips_actions_without_release_info():
    actions = [
        {"action_type": "release_announcement", "post_id": "1"},  # no release_info
        _release_action("anthropic", "0.75.0", "200"),
    ]
    triggers = extract_release_triggers(actions)
    assert len(triggers) == 1
    assert triggers[0].package == "anthropic"


def test_extract_dedupes_same_package_version_within_tick():
    actions = [
        _release_action("anthropic", "0.75.0", "100"),
        _release_action("anthropic", "0.75.0", "200"),  # same release, different post
    ]
    triggers = extract_release_triggers(actions)
    assert len(triggers) == 1


def test_extract_keeps_distinct_versions_of_same_package():
    actions = [
        _release_action("anthropic", "0.75.0", "100"),
        _release_action("anthropic", "0.76.0", "200"),
    ]
    triggers = extract_release_triggers(actions)
    versions = {t.version for t in triggers}
    assert versions == {"0.75.0", "0.76.0"}


def test_extract_filters_out_non_anthropic_adjacent_by_default():
    actions = [
        _release_action("openai", "1.0.0", "100", anthropic=False),
        _release_action("anthropic", "0.75.0", "200"),
    ]
    triggers = extract_release_triggers(actions)
    assert len(triggers) == 1
    assert triggers[0].package == "anthropic"


def test_extract_with_only_anthropic_adjacent_false_keeps_all():
    actions = [
        _release_action("openai", "1.0.0", "100", anthropic=False),
        _release_action("anthropic", "0.75.0", "200"),
    ]
    triggers = extract_release_triggers(actions, only_anthropic_adjacent=False)
    assert len(triggers) == 2


def test_extract_uses_dependency_currency_check_when_release_info_lacks_flag():
    """When release_info doesn't include is_anthropic_adjacent, fall back
    to dependency_currency.is_anthropic_adjacent."""
    actions = [
        {
            "post_id": "100",
            "action_type": "release_announcement",
            "release_info": {"package": "claude-code", "version": "2.1.116"},  # no flag
        },
    ]
    triggers = extract_release_triggers(actions)
    assert len(triggers) == 1
    assert triggers[0].package == "claude-code"


def test_extract_carries_handle_and_post_url():
    actions = [_release_action("anthropic", "0.75.0", "100")]
    triggers = extract_release_triggers(actions, handle="ClaudeDevs")
    assert triggers[0].handle == "ClaudeDevs"
    assert triggers[0].post_url == "https://x.com/ClaudeDevs/status/100"


def test_extract_skips_when_package_or_version_empty():
    actions = [
        {"action_type": "release_announcement", "release_info": {"package": "", "version": "1.0.0"}},
        {"action_type": "release_announcement", "release_info": {"package": "anthropic", "version": ""}},
    ]
    assert extract_release_triggers(actions) == []


# ── auto_apply_release_triggers ─────────────────────────────────────────────


def _stub_outcome(package: str, version: str, *, ok: bool = True) -> UpgradeOutcome:
    return UpgradeOutcome(
        package=package,
        from_version="0.0.0",
        to_version=version,
        diff="(stubbed)",
        sync_ok=ok,
        sync_stderr_excerpt="" if ok else "stubbed_failure",
        zai_smoke=SmokeResult(name="zai_smoke", ok=ok),
        anthropic_smoke=SmokeResult(name="anthropic_native_smoke", ok=ok),
        rolled_back=not ok,
        rollback_reason="" if ok else "stubbed",
        started_at="2026-05-05T00:00:00+00:00",
        finished_at="2026-05-05T00:01:00+00:00",
    )


def test_auto_apply_invokes_actuator_for_each_trigger(monkeypatch):
    captured: list[tuple[str, str]] = []

    def stub_apply(*, package, target_version, **kwargs):
        captured.append((package, target_version))
        return _stub_outcome(package, target_version, ok=True)

    monkeypatch.setattr(release_auto_trigger, "apply_upgrade", stub_apply)

    triggers = [
        ReleaseTrigger(package="anthropic", version="0.75.0", post_id="1"),
        ReleaseTrigger(package="claude-code", version="2.1.116", post_id="2"),
    ]
    results = auto_apply_release_triggers(triggers, enabled=True)
    assert captured == [("anthropic", "0.75.0"), ("claude-code", "2.1.116")]
    assert all(r.attempted for r in results)
    assert all(r.overall_ok for r in results)


def test_auto_apply_skips_when_disabled(monkeypatch):
    """enabled=False short-circuits without calling apply_upgrade."""

    def stub_apply(**kwargs):
        pytest.fail("apply_upgrade must not be called when disabled")

    monkeypatch.setattr(release_auto_trigger, "apply_upgrade", stub_apply)

    triggers = [ReleaseTrigger(package="anthropic", version="0.75.0", post_id="1")]
    results = auto_apply_release_triggers(triggers, enabled=False)
    assert len(results) == 1
    assert not results[0].attempted
    assert results[0].skipped_reason == "auto_upgrade_disabled_via_env"


def test_auto_apply_respects_env_default(monkeypatch):
    monkeypatch.setenv("UA_CSI_AUTO_UPGRADE_ON_RELEASE", "0")

    def stub_apply(**kwargs):
        pytest.fail("apply_upgrade must not be called when env disables auto-upgrade")

    monkeypatch.setattr(release_auto_trigger, "apply_upgrade", stub_apply)

    triggers = [ReleaseTrigger(package="anthropic", version="0.75.0", post_id="1")]
    results = auto_apply_release_triggers(triggers)  # no enabled= → reads env
    assert all(not r.attempted for r in results)


def test_auto_apply_handles_actuator_keyerror(monkeypatch):
    """Package not in pyproject.toml — surface as skip, don't crash."""

    def stub_apply(*, package, target_version, **kwargs):
        raise KeyError(f"package {package!r} not found")

    monkeypatch.setattr(release_auto_trigger, "apply_upgrade", stub_apply)

    triggers = [ReleaseTrigger(package="not-in-pyproject", version="1.0.0", post_id="1")]
    results = auto_apply_release_triggers(triggers, enabled=True)
    assert len(results) == 1
    assert not results[0].attempted
    assert "package_not_in_pyproject" in results[0].skipped_reason


def test_auto_apply_handles_actuator_filenotfounderror(monkeypatch):
    """pyproject.toml missing — surface as skip, don't crash."""

    def stub_apply(**kwargs):
        raise FileNotFoundError("pyproject.toml not found")

    monkeypatch.setattr(release_auto_trigger, "apply_upgrade", stub_apply)

    triggers = [ReleaseTrigger(package="anthropic", version="0.75.0", post_id="1")]
    results = auto_apply_release_triggers(triggers, enabled=True)
    assert "pyproject_missing" in results[0].skipped_reason


def test_auto_apply_records_failure_outcome(monkeypatch):
    """Smoke fail → result.overall_ok is False but attempted is True."""

    def stub_apply(*, package, target_version, **kwargs):
        return _stub_outcome(package, target_version, ok=False)

    monkeypatch.setattr(release_auto_trigger, "apply_upgrade", stub_apply)

    triggers = [ReleaseTrigger(package="anthropic", version="9.9.9", post_id="1")]
    results = auto_apply_release_triggers(triggers, enabled=True)
    assert results[0].attempted is True
    assert results[0].overall_ok is False


def test_auto_apply_passes_through_paths(monkeypatch, tmp_path: Path):
    """repo_root, smoke_dir, backup_dir all forwarded to apply_upgrade."""
    repo_root = tmp_path / "repo"
    smoke_dir = tmp_path / "smoke"
    backup_dir = tmp_path / "backups"

    captured: dict = {}

    def stub_apply(**kwargs):
        captured.update(kwargs)
        return _stub_outcome(kwargs["package"], kwargs["target_version"], ok=True)

    monkeypatch.setattr(release_auto_trigger, "apply_upgrade", stub_apply)

    triggers = [ReleaseTrigger(package="anthropic", version="0.75.0", post_id="1")]
    auto_apply_release_triggers(
        triggers,
        repo_root=repo_root,
        smoke_dir=smoke_dir,
        backup_dir=backup_dir,
        enabled=True,
    )
    assert captured["repo_root"] == repo_root
    assert captured["smoke_dir"] == smoke_dir
    assert captured["backup_dir"] == backup_dir


# ── Summary helper ──────────────────────────────────────────────────────────


def test_summarize_counts_correctly():
    results = [
        AutoUpgradeResult(
            trigger=ReleaseTrigger(package="anthropic", version="0.75.0", post_id="1"),
            outcome=_stub_outcome("anthropic", "0.75.0", ok=True),
        ),
        AutoUpgradeResult(
            trigger=ReleaseTrigger(package="claude-code", version="2.1.116", post_id="2"),
            outcome=_stub_outcome("claude-code", "2.1.116", ok=False),
        ),
        AutoUpgradeResult(
            trigger=ReleaseTrigger(package="missing", version="1.0.0", post_id="3"),
            skipped_reason="package_not_in_pyproject: missing",
        ),
    ]
    summary = summarize_auto_upgrade_results(results)
    assert summary["trigger_count"] == 3
    assert summary["attempted_count"] == 2
    assert summary["succeeded_count"] == 1
    assert "package_not_in_pyproject: missing" in summary["skipped"]
    assert len(summary["results"]) == 3


def test_summarize_handles_empty_input():
    assert summarize_auto_upgrade_results([]) == {
        "trigger_count": 0,
        "attempted_count": 0,
        "succeeded_count": 0,
        "skipped": [],
        "results": [],
    }
