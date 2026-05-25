"""Tests for the CSI X-API quota cooldown.

When the X API returns HTTP 402 (CreditsDepleted), the next ~24 hours
of scheduled CSI syncs should short-circuit before hitting the API and
NOT emit csi_sync_failed events (which would mail the operator every
fire). The FIRST 402 of a fresh cycle still alarms; subsequent fires
are silent until the cooldown expires.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from universal_agent.services.claude_code_intel import (
    _cooldown_active,
    _is_quota_exhausted_error,
    _quota_cooldown_hours,
    _quota_cooldown_path,
    _read_quota_cooldown,
    _write_quota_cooldown,
)


def test_402_error_string_detected() -> None:
    assert _is_quota_exhausted_error("X API request failed: HTTP 402: CreditsDepleted")
    assert _is_quota_exhausted_error("Got HTTP 402 from upstream")
    assert _is_quota_exhausted_error("creditsdepleted error from twitter")


def test_other_errors_not_detected() -> None:
    assert not _is_quota_exhausted_error("HTTP 500 internal server error")
    assert not _is_quota_exhausted_error("missing X_BEARER_TOKEN")
    assert not _is_quota_exhausted_error("")
    assert not _is_quota_exhausted_error("HTTP 401: Unauthorized")


def test_cooldown_path_per_handle(tmp_path: Path) -> None:
    p1 = _quota_cooldown_path(tmp_path, "bcherny")
    p2 = _quota_cooldown_path(tmp_path, "ClaudeDevs")
    assert p1 != p2
    assert p1.name == "quota_cooldown__bcherny.json"
    assert p2.name == "quota_cooldown__ClaudeDevs.json"
    # Leading @ stripped.
    p3 = _quota_cooldown_path(tmp_path, "@bcherny")
    assert p3 == p1


def test_write_and_read_cooldown_round_trip(tmp_path: Path) -> None:
    path = _quota_cooldown_path(tmp_path, "bcherny")
    until_iso = _write_quota_cooldown(path, reason="HTTP 402", hours=24.0)
    assert until_iso
    assert path.exists()
    record = _read_quota_cooldown(path)
    assert record is not None
    assert record["reason"] == "HTTP 402"
    assert record["cooldown_hours"] == 24.0
    assert record["until_iso"] == until_iso


def test_cooldown_active_returns_until_iso_when_in_window(tmp_path: Path) -> None:
    path = _quota_cooldown_path(tmp_path, "bcherny")
    _write_quota_cooldown(path, reason="HTTP 402", hours=24.0)
    record = _read_quota_cooldown(path)
    assert _cooldown_active(record) is not None


def test_cooldown_expired_returns_none(tmp_path: Path) -> None:
    """Cooldown record whose ``until_iso`` is in the past should not
    suppress subsequent syncs."""
    record = {
        "set_at_iso": "2026-05-01T00:00:00+00:00",
        "until_iso": "2026-05-02T00:00:00+00:00",  # well in the past
        "reason": "HTTP 402",
        "cooldown_hours": 24.0,
    }
    assert _cooldown_active(record) is None


def test_cooldown_missing_file_returns_none(tmp_path: Path) -> None:
    path = _quota_cooldown_path(tmp_path, "bcherny")
    # No file written.
    assert _read_quota_cooldown(path) is None
    assert _cooldown_active(None) is None


def test_cooldown_corrupt_file_returns_none(tmp_path: Path) -> None:
    path = _quota_cooldown_path(tmp_path, "bcherny")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not valid json {{", encoding="utf-8")
    assert _read_quota_cooldown(path) is None


def test_cooldown_hours_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UA_CSI_QUOTA_COOLDOWN_HOURS", "6")
    assert _quota_cooldown_hours() == 6.0
    monkeypatch.setenv("UA_CSI_QUOTA_COOLDOWN_HOURS", "not-a-number")
    assert _quota_cooldown_hours() == 24.0  # default fallback
    monkeypatch.delenv("UA_CSI_QUOTA_COOLDOWN_HOURS", raising=False)
    assert _quota_cooldown_hours() == 24.0
    # Floor at 0.5h so an accidental "0" doesn't disable the cooldown.
    monkeypatch.setenv("UA_CSI_QUOTA_COOLDOWN_HOURS", "0")
    assert _quota_cooldown_hours() == 0.5


def test_cooldown_record_with_malformed_until_iso_returns_none() -> None:
    record = {
        "set_at_iso": "2026-05-25T00:00:00+00:00",
        "until_iso": "not-a-timestamp",
        "reason": "HTTP 402",
    }
    assert _cooldown_active(record) is None
