"""Tests for ZAIRateLimiter persistent snapshot + FUP detection.

Background: pre-P4, the rate limiter tracked 429s in singleton memory only.
The watchdog (running in heartbeat daemon subprocesses) couldn't observe it.
P4 adds an atomic JSON snapshot written after every record_* call and a
new `record_fup_signal()` path for ZAI Fair-Use-Policy / concurrency-cap
violations (the "back off NOW or get banned" tier).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from universal_agent.rate_limiter import (
    FUP_KEYWORDS,
    ZAIRateLimiter,
    _is_fup_error,
)


@pytest.fixture
def isolated_state_path(tmp_path, monkeypatch):
    """Point the snapshot at a tmp_path file so tests don't clobber prod state."""
    state_file = tmp_path / "zai_inference_state.json"
    monkeypatch.setenv("UA_ZAI_INFERENCE_STATE_PATH", str(state_file))
    ZAIRateLimiter.reset_instance()
    yield state_file
    ZAIRateLimiter.reset_instance()


@pytest.mark.asyncio
async def test_record_429_persists_snapshot(isolated_state_path: Path):
    limiter = ZAIRateLimiter.get_instance()
    await limiter.record_429(context="test")
    assert isolated_state_path.exists()
    snapshot = json.loads(isolated_state_path.read_text())
    assert snapshot["total_429s"] == 1
    assert snapshot["consecutive_429s"] == 1
    assert snapshot["last_429_at"] is not None  # epoch float


@pytest.mark.asyncio
async def test_record_success_persists_snapshot(isolated_state_path: Path):
    limiter = ZAIRateLimiter.get_instance()
    await limiter.record_success()
    assert isolated_state_path.exists()
    snapshot = json.loads(isolated_state_path.read_text())
    assert snapshot["total_requests"] == 1
    assert snapshot["last_success_at"] is not None


@pytest.mark.asyncio
async def test_record_fup_signal_persists_snapshot(isolated_state_path: Path):
    """ANY FUP signal is the critical-immediate failure — must be persisted
    so the watchdog can see it within one heartbeat regardless of process."""
    limiter = ZAIRateLimiter.get_instance()
    await limiter.record_fup_signal(
        context="csi_brief",
        error_snippet="HTTP 403: fair use policy violation — too many concurrent",
    )
    snapshot = json.loads(isolated_state_path.read_text())
    assert snapshot["total_fup_events"] == 1
    assert snapshot["last_fup_at"] is not None
    assert "fair use" in snapshot["last_fup_snippet"].lower()
    assert snapshot["last_fup_context"] == "csi_brief"


@pytest.mark.asyncio
async def test_snapshot_is_atomic_replace(isolated_state_path: Path):
    """Atomic write: never leave the file half-written. We use os.replace
    via a temp file. Verify no .tmp leftover after a record call."""
    limiter = ZAIRateLimiter.get_instance()
    await limiter.record_429(context="t")
    leftovers = list(isolated_state_path.parent.glob("*.tmp"))
    assert leftovers == []


@pytest.mark.asyncio
async def test_multiple_records_accumulate(isolated_state_path: Path):
    limiter = ZAIRateLimiter.get_instance()
    await limiter.record_429("a")
    await limiter.record_429("b")
    await limiter.record_429("c")
    snapshot = json.loads(isolated_state_path.read_text())
    assert snapshot["total_429s"] == 3


def test_fup_keyword_detector():
    """The detector must catch the obvious phrasings ZAI uses. Refine the
    keyword list once we see a real FUP response — for now match common
    fair-use-policy / concurrency-violation language."""
    # Should match
    assert _is_fup_error("HTTP 403: fair use policy violation")
    assert _is_fup_error("FUP triggered for account")
    assert _is_fup_error("Fair-use limit exceeded")
    assert _is_fup_error("Concurrency limit exceeded for this account")
    assert _is_fup_error("Account flagged for abuse pattern")
    assert _is_fup_error("Policy violation: code 1313")
    # Should NOT match — regular 429s are NOT FUP
    assert not _is_fup_error("HTTP 429: too many requests")
    assert not _is_fup_error("rate limit hit")
    assert not _is_fup_error("server error 500")
    assert not _is_fup_error("")


def test_fup_keywords_lowercase_only():
    """Sanity: keyword list is lowercase so the comparison works on lower()."""
    for kw in FUP_KEYWORDS:
        assert kw == kw.lower(), f"Keyword {kw!r} must be lowercase"
