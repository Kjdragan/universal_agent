"""Tests for R1: ZAI error code 1310 (weekly/monthly quota exhaustion)
auto-detection and L4 auto-pause.

Covers the seam across `rate_limiter.py` (classifier + retry-loop
short-circuit) and `services/zai_control.py` (the handler that parses the
reset timestamp and trips the L4 preset). See also:
- tests/unit/test_zai_control.py — handle_weekly_exhaustion writer-level tests
- tests/unit/test_zai_observability.py — httpx-lane detection
- tests/unit/test_zai_inference_health.py — the alerting condition
- tests/unit/test_capacity_governor.py — the dispatch-gating Check -1
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from universal_agent.rate_limiter import (
    ZAIRateLimiter,
    _is_429_error,
    _is_fup_error,
    _is_weekly_exhaustion,
    with_rate_limit_retry,
)
from universal_agent.services import zai_control

# Real 1310 body from the operator-reported incident (2026-07-18), wrapped
# the way ZAI SDK exception strings are shaped in this codebase's other
# fixtures (a bare "Error code: 429 - " prefix, see FUP_429_TEXT in
# test_rate_limiter_aimd.py). This literal is used ONLY for text-matching
# tests (classifier ordering) — its embedded timestamp is a point-in-time
# example and is NOT assumed to be in the future relative to wall-clock "now"
# when the suite runs, so parsing/TTL tests build their own body below.
WEEKLY_1310_TEXT = (
    "Error code: 429 - [1310][Weekly/Monthly Limit Exhausted. Your limit "
    "will reset at 2026-07-19 00:54:25][20260718191643db4f685336574732]"
)


def _future_1310_body(hours_ahead: float = 48.0) -> tuple[str, float]:
    """Build a 1310 body whose reset timestamp is ``hours_ahead`` hours from
    NOW (Beijing-local, Asia/Shanghai) — guaranteed in the future regardless
    of wall-clock date. Returns ``(body, expected_reset_epoch)``."""
    target = datetime.now(tz=ZoneInfo("Asia/Shanghai")) + timedelta(hours=hours_ahead)
    ts_str = target.strftime("%Y-%m-%d %H:%M:%S")
    body = (
        "Error code: 429 - [1310][Weekly/Monthly Limit Exhausted. Your limit "
        f"will reset at {ts_str}][20260718191643db4f685336574732]"
    )
    return body, target.timestamp()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Isolated rate-limiter snapshot + control file, fast retry knobs."""
    monkeypatch.setenv("UA_ZAI_INFERENCE_STATE_PATH", str(tmp_path / "zai_state.json"))
    monkeypatch.setenv("UA_ZAI_CONTROL_PATH", str(tmp_path / "zai_control.json"))
    monkeypatch.setenv("ZAI_MIN_INTERVAL", "0.0")
    monkeypatch.setenv("ZAI_OPUS_MIN_INTERVAL", "0")
    monkeypatch.setenv("ZAI_INITIAL_BACKOFF", "0.01")
    monkeypatch.setenv("ZAI_MAX_BACKOFF", "0.05")
    ZAIRateLimiter.reset_instance()
    zai_control._invalidate_cache()
    yield tmp_path
    ZAIRateLimiter.reset_instance()
    zai_control._invalidate_cache()


# ── Classifier ──────────────────────────────────────────────────────────────


def test_is_weekly_exhaustion_matches_real_1310_body():
    assert _is_weekly_exhaustion(WEEKLY_1310_TEXT) is True


def test_1310_body_is_not_fup_error_regression():
    """CONFIRMED bug documented in the fact pack: the 1310 body does NOT
    contain any FUP_KEYWORDS substring ("weekly/monthly limit" has a slash,
    not the "weekly limit" phrase; no "1313"). Pinned here so a future
    keyword-set change can't silently regress the classifier ordering this
    PR depends on."""
    assert _is_fup_error(WEEKLY_1310_TEXT) is False


def test_1310_body_also_looks_429_shaped():
    """The reason weekly-exhaustion MUST be checked before `_is_429_error`
    in `with_rate_limit_retry`: a 1310 exception string also contains
    '429'."""
    assert _is_429_error(WEEKLY_1310_TEXT) is True


def test_is_weekly_exhaustion_empty_string_false():
    assert _is_weekly_exhaustion("") is False


# ── False-positive regression (BLOCKER fix) ─────────────────────────────────
#
# The keyword set used to include a bare "1310" and "limit exhausted", both
# of which false-positive on real, non-1310 ZAI traffic: request ids embed a
# timestamp (any id minted at 13:10:xx contains "1310"), and context-length
# errors mention "131072" (which contains "1310" as a substring). The
# narrowed keyword set — exactly `{"[1310]", "weekly/monthly limit"}` — must
# NOT match any of these.


def test_ordinary_1313_fup_429_is_not_weekly_exhaustion():
    """A completely ordinary ZAI FUP-gradient 429 (code 1313, no 1310 at
    all) must never be misclassified as the weekly wall."""
    body = (
        "Error code: 429 - {'error': {'code': '1313', 'message': \"[1313][Your "
        "account's current usage pattern does not comply with the Fair Usage "
        'Policy, and your request frequency has been limited.]"}}'
    )
    assert _is_weekly_exhaustion(body) is False


def test_request_id_containing_1310_substring_is_not_weekly_exhaustion():
    """A request id minted at 13:10:15 embeds the substring "1310" inside a
    longer digit run — must NOT trip the weekly-exhaustion classifier."""
    body = (
        "Error code: 429 - [1313][Fair Usage Policy limited]"
        "[20260718131015abcdef]"
    )
    assert _is_weekly_exhaustion(body) is False


def test_context_length_error_containing_1310_substring_is_not_weekly_exhaustion():
    """"131072" (a common max-context-length figure) contains the substring
    "1310" — must NOT trip the weekly-exhaustion classifier."""
    body = "Error: maximum context length is 131072 tokens, request exceeds it"
    assert _is_weekly_exhaustion(body) is False


# ── with_rate_limit_retry short-circuit ─────────────────────────────────────


def test_1310_stops_after_one_attempt_no_retries(isolated):
    calls = {"n": 0}

    async def always_1310() -> str:
        calls["n"] += 1
        raise RuntimeError(WEEKLY_1310_TEXT)

    with pytest.raises(RuntimeError, match="1310"):
        asyncio.run(
            with_rate_limit_retry(
                always_1310, max_retries=5, context="t", model_tier="sonnet"
            )
        )
    assert calls["n"] == 1, "1310 must raise immediately — zero retries"


def test_1310_trips_pause_only_global_pause_with_parsed_ttl(isolated):
    """Pause-only (BLOCKER/SERIOUS fix): the 1310 auto-trip must set the
    global pause with the parsed TTL and leave tier_overrides/tier_pause
    completely untouched — an L4-style tier preset has no TTL of its own,
    so it would leave opus/mid permanently hard-stopped after the global
    pause self-clears at the reset."""
    body, expected_reset_epoch = _future_1310_body(hours_ahead=48.0)

    async def always_1310() -> str:
        raise RuntimeError(body)

    with pytest.raises(RuntimeError):
        asyncio.run(with_rate_limit_retry(always_1310, context="t"))

    paused, gp = zai_control.is_globally_paused()
    assert paused is True
    assert "zai_1310" in gp.get("reason", "")
    expected_until = expected_reset_epoch + 120
    assert gp["until"] == pytest.approx(expected_until, abs=5)

    cstate = zai_control.current_state()
    # No tier preset — tier caps/pauses are left exactly as they were.
    assert cstate["tier_pause"]["opus"] is False
    assert cstate["tier_pause"]["mid"] is False
    assert cstate["tier_overrides"] == {}


# ── handle_weekly_exhaustion: parsing + fallback + idempotence ──────────────


def test_beijing_parse_produces_correct_epoch(isolated):
    body, expected_reset_epoch = _future_1310_body(hours_ahead=72.0)
    zai_control.handle_weekly_exhaustion(body, source="test")
    weekly = zai_control.read_control().get("weekly_exhaustion") or {}
    assert weekly["reset_at_epoch"] == pytest.approx(expected_reset_epoch, abs=1)


def test_garbage_body_falls_back_to_default_ttl(isolated, monkeypatch):
    monkeypatch.setenv("UA_ZAI_1310_FALLBACK_TTL_SECONDS", "21600")
    before = zai_control._now()
    zai_control.handle_weekly_exhaustion("no timestamp in here at all", source="test")
    _, gp = zai_control.is_globally_paused()
    assert gp["until"] == pytest.approx(before + 21600, abs=5)


def test_reset_in_the_past_falls_back_to_default_ttl(isolated, monkeypatch):
    monkeypatch.setenv("UA_ZAI_1310_FALLBACK_TTL_SECONDS", "21600")
    past_body = (
        "[1310][Weekly/Monthly Limit Exhausted. Your limit will reset at "
        "2020-01-01 00:00:00][deadbeef]"
    )
    before = zai_control._now()
    zai_control.handle_weekly_exhaustion(past_body, source="test")
    _, gp = zai_control.is_globally_paused()
    assert gp["until"] == pytest.approx(before + 21600, abs=5)


def test_unparseable_body_stamps_future_reset_epoch_not_none(isolated, monkeypatch):
    """SERIOUS fix: when the reset timestamp can't be parsed, `reset_at_epoch`
    must be stamped as `now + fallback_ttl` — NOT `None`. The
    `zai_inference_health` alerting condition requires a reset time in the
    future to fire; a `None` stamp would silently make this trip
    unalertable forever (alert-silent fallback). See
    ``test_zai_inference_health.py`` for the end-to-end proof that this
    fallback stamp actually produces a critical finding."""
    monkeypatch.setenv("UA_ZAI_1310_FALLBACK_TTL_SECONDS", "21600")
    before = zai_control._now()
    zai_control.handle_weekly_exhaustion("no timestamp in here at all", source="test")
    weekly = zai_control.read_control().get("weekly_exhaustion") or {}
    assert weekly["reset_at_epoch"] is not None
    assert weekly["reset_at_epoch"] == pytest.approx(before + 21600, abs=5)
    assert weekly["reset_at_epoch"] > zai_control._now()


def test_idempotent_second_1310_does_not_extend_pause(isolated):
    first = zai_control.handle_weekly_exhaustion(WEEKLY_1310_TEXT, source="a")
    first_until = first["global_pause"]["until"]

    # A second, later-arriving 1310 (even with a different reset timestamp)
    # must be a no-op while the zai_1310 pause is already active.
    later_body = (
        "[1310][Weekly/Monthly Limit Exhausted. Your limit will reset at "
        "2026-07-26 00:54:25][ffffffff]"
    )
    second = zai_control.handle_weekly_exhaustion(later_body, source="b")
    assert second["global_pause"]["until"] == first_until


def test_never_raises_on_unwritable_control_file(isolated, monkeypatch, tmp_path):
    """Fail-open per the module-wide invariant, even with a bad path."""
    monkeypatch.setenv("UA_ZAI_CONTROL_PATH", "/proc/nonexistent_dir/control.json")
    zai_control._invalidate_cache()
    result = zai_control.handle_weekly_exhaustion(WEEKLY_1310_TEXT, source="test")
    # write fails silently inside write_control; handle_weekly_exhaustion
    # itself must not raise regardless of what it returns.
    assert result is None or isinstance(result, dict)
