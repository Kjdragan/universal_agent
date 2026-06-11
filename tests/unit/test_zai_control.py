"""Tests for the ZAI emergency control plane (services/zai_control.py).

The load-bearing property is FAIL-OPEN: a missing/corrupt/unreadable control
file must yield normal operation (no pause, no overrides), never a raise and
never an accidental pause — because this code runs on the deploy/restore path
and cannot be live-tested first.
"""

from __future__ import annotations

import json

import pytest

from universal_agent.services import zai_control


@pytest.fixture(autouse=True)
def isolated_control(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_ZAI_CONTROL_PATH", str(tmp_path / "zai_control.json"))
    zai_control._invalidate_cache()
    yield tmp_path / "zai_control.json"
    zai_control._invalidate_cache()


# ── Fail-open ───────────────────────────────────────────────────────────────


def test_missing_file_is_normal(isolated_control):
    assert zai_control.read_control() == {}
    assert zai_control.is_globally_paused() == (False, {})
    assert zai_control.is_tier_paused("opus") is False
    assert zai_control.tier_cap_override("opus") is None


def test_corrupt_file_fails_open(isolated_control):
    isolated_control.write_text("{not valid json")
    zai_control._invalidate_cache()
    assert zai_control.read_control() == {}
    assert zai_control.is_globally_paused()[0] is False
    # A corrupt control file must NOT pause and must NOT crash.
    assert zai_control.effective_tier_cap("opus", ai_cap=3, tier_max=3) == 3


def test_non_dict_json_fails_open(isolated_control):
    isolated_control.write_text("[1, 2, 3]")
    zai_control._invalidate_cache()
    assert zai_control.read_control() == {}


def test_unreadable_file_fails_open(isolated_control, monkeypatch):
    isolated_control.write_text(json.dumps({"global_pause": {"active": True}}))
    zai_control._invalidate_cache()

    def boom(*a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr(type(isolated_control), "read_text", boom)
    # Read error → fail open (NOT paused), never raises.
    assert zai_control.read_control() == {}
    assert zai_control.is_globally_paused()[0] is False


# ── Round-trip + atomicity ──────────────────────────────────────────────────


def test_write_read_roundtrip(isolated_control):
    assert zai_control.write_control({"intervention_level": 2}) is True
    zai_control._invalidate_cache()
    data = zai_control.read_control()
    assert data["intervention_level"] == 2
    assert data["version"] == zai_control.CONTROL_VERSION
    assert "updated_at" in data
    assert not isolated_control.with_suffix(".json.tmp").exists()  # tmp cleaned


# ── Global pause + TTL ──────────────────────────────────────────────────────


def test_global_pause_active_and_ttl_expiry(isolated_control):
    zai_control.set_global_pause(True, ttl_seconds=1000, reason="test")
    zai_control._invalidate_cache()
    paused, info = zai_control.is_globally_paused()
    assert paused is True
    assert info["reason"] == "test"

    # A pause whose `until` is in the past auto-clears.
    data = zai_control.read_control()
    data["global_pause"]["until"] = zai_control._now() - 10
    zai_control.write_control(data)
    zai_control._invalidate_cache()
    assert zai_control.is_globally_paused()[0] is False


def test_global_pause_no_ttl_stays(isolated_control):
    zai_control.set_global_pause(True, ttl_seconds=0)  # 0 → no expiry
    zai_control._invalidate_cache()
    paused, info = zai_control.is_globally_paused()
    assert paused is True
    assert info.get("until") is None


def test_clear_global_pause(isolated_control):
    zai_control.set_global_pause(True, ttl_seconds=1000)
    zai_control._invalidate_cache()
    zai_control.set_global_pause(False)
    zai_control._invalidate_cache()
    assert zai_control.is_globally_paused()[0] is False


# ── Tier overrides + effective cap ──────────────────────────────────────────


def test_tier_cap_override_wins_over_aimd(isolated_control):
    zai_control.set_tier_overrides({"opus": {"cap": 1, "max": 1}})
    zai_control._invalidate_cache()
    # AIMD wants 3, override forces 1.
    assert zai_control.effective_tier_cap("opus", ai_cap=3, tier_max=3) == 1


def test_no_override_uses_aimd_cap(isolated_control):
    zai_control.set_tier_overrides({"opus": {"cap": 1}})
    zai_control._invalidate_cache()
    # sonnet has no override → AIMD cap stands.
    assert zai_control.effective_tier_cap("sonnet", ai_cap=4, tier_max=5) == 4


def test_override_clamped_to_min_one(isolated_control):
    isolated_control.write_text(json.dumps({"tier_overrides": {"opus": {"cap": 0}}}))
    zai_control._invalidate_cache()
    # cap coerced to ≥ 1.
    assert zai_control.effective_tier_cap("opus", ai_cap=3, tier_max=3) == 1


def test_override_cap_clamped_to_override_max(isolated_control):
    zai_control.set_tier_overrides({"sonnet": {"cap": 9, "max": 3}})
    zai_control._invalidate_cache()
    assert zai_control.effective_tier_cap("sonnet", ai_cap=2, tier_max=5) == 3


def test_unknown_tier_override_ignored(isolated_control):
    zai_control.set_tier_overrides({"bogus": {"cap": 1}, "opus": {"cap": 1}})
    zai_control._invalidate_cache()
    data = zai_control.read_control()
    assert "bogus" not in data.get("tier_overrides", {})
    assert "opus" in data.get("tier_overrides", {})


def test_clear_a_tier_override(isolated_control):
    zai_control.set_tier_overrides({"opus": {"cap": 1}})
    zai_control._invalidate_cache()
    zai_control.set_tier_overrides({"opus": None})  # clear
    zai_control._invalidate_cache()
    assert zai_control.tier_cap_override("opus") is None


# ── Tier pause ──────────────────────────────────────────────────────────────


def test_tier_pause_set_and_clear(isolated_control):
    zai_control.set_tier_pause({"opus": True, "mid": True})
    zai_control._invalidate_cache()
    assert zai_control.is_tier_paused("opus") is True
    assert zai_control.is_tier_paused("sonnet") is False
    zai_control.set_tier_pause({"opus": False})
    zai_control._invalidate_cache()
    assert zai_control.is_tier_paused("opus") is False
    assert zai_control.is_tier_paused("mid") is True


# ── Level presets (the ladder) ──────────────────────────────────────────────


@pytest.mark.parametrize("level", [0, 1, 2, 3, 4])
def test_apply_level_writes_expected_shape(isolated_control, level):
    state = zai_control.apply_level(level, reason="ladder-test")
    assert state["intervention_level"] == level
    if level == 0:
        assert zai_control.is_globally_paused()[0] is False
        assert state.get("tier_overrides") in ({}, None)
    if level == 2:
        # all tiers serialized
        for t in zai_control.TIERS:
            assert zai_control.effective_tier_cap(t, ai_cap=5, tier_max=5) == 1
    if level == 3:
        assert zai_control.is_tier_paused("opus") is True
        assert zai_control.is_tier_paused("mid") is True
        assert zai_control.is_tier_paused("sonnet") is False
    if level == 4:
        paused, _ = zai_control.is_globally_paused()
        assert paused is True


def test_level_4_has_default_ttl(isolated_control, monkeypatch):
    monkeypatch.setattr(zai_control, "DEFAULT_GLOBAL_PAUSE_TTL_SECONDS", 1800.0)
    state = zai_control.apply_level(4)
    until = state["global_pause"]["until"]
    assert until is not None and until > zai_control._now()


def test_unknown_level_is_noop(isolated_control):
    zai_control.apply_level(2)
    zai_control._invalidate_cache()
    before = zai_control.read_control()
    zai_control.apply_level(99)  # unknown
    zai_control._invalidate_cache()
    after = zai_control.read_control()
    assert after.get("intervention_level") == before.get("intervention_level") == 2


def test_clear_all_returns_to_normal(isolated_control):
    zai_control.apply_level(4)
    zai_control._invalidate_cache()
    zai_control.clear_all()
    zai_control._invalidate_cache()
    assert zai_control.is_globally_paused()[0] is False
    assert zai_control.current_state()["intervention_level"] == 0


def test_levels_cover_all_tiers():
    """The control module's TIERS must match the limiter's, or overrides/pauses
    would silently miss a tier."""
    from universal_agent.rate_limiter import TIERS as LIMITER_TIERS

    assert set(zai_control.TIERS) == set(LIMITER_TIERS)


def test_current_state_shape(isolated_control):
    zai_control.apply_level(3, reason="x")
    state = zai_control.current_state()
    assert set(state["tier_pause"]) == set(zai_control.TIERS)
    assert state["intervention_level"] == 3
    assert state["global_pause_active"] is False


# ── Enforcement integration: rate limiter ───────────────────────────────────
import asyncio  # noqa: E402

from universal_agent.rate_limiter import ZAIFupPauseError, ZAIRateLimiter  # noqa: E402


@pytest.fixture
def limiter(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_ZAI_INFERENCE_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("ZAI_MIN_INTERVAL", "0.0")
    ZAIRateLimiter.reset_instance()
    yield ZAIRateLimiter.get_instance()
    ZAIRateLimiter.reset_instance()


def test_limiter_acquire_blocked_by_global_pause(isolated_control, limiter):
    zai_control.set_global_pause(True, ttl_seconds=1000)
    zai_control._invalidate_cache()

    async def run():
        with pytest.raises(ZAIFupPauseError, match="globally paused"):
            async with limiter.acquire("t", model_tier="sonnet"):
                pass
        # Tierless (legacy) callers are blocked too — global means global.
        with pytest.raises(ZAIFupPauseError, match="globally paused"):
            async with limiter.acquire("t"):
                pass

    asyncio.run(run())


def test_limiter_acquire_blocked_by_tier_pause_only_that_tier(isolated_control, limiter):
    zai_control.set_tier_pause({"opus": True})
    zai_control._invalidate_cache()

    async def run():
        with pytest.raises(ZAIFupPauseError, match="tier 'opus' paused"):
            async with limiter.acquire("t", model_tier="opus"):
                pass
        # A different tier is unaffected.
        async with limiter.acquire("t", model_tier="sonnet"):
            pass

    asyncio.run(run())


def test_limiter_effective_cap_reflects_override(isolated_control, limiter):
    # No override → AIMD seed.
    assert limiter._effective_tier_cap("haiku") == limiter._tier_cap["haiku"]
    zai_control.set_tier_overrides({"haiku": {"cap": 1, "max": 1}})
    zai_control._invalidate_cache()
    assert limiter._effective_tier_cap("haiku") == 1


def test_limiter_acquire_fails_open_on_control_error(isolated_control, limiter, monkeypatch):
    def boom():
        raise RuntimeError("control subsystem broken")

    monkeypatch.setattr(zai_control, "is_globally_paused", boom)

    async def run():
        # Control read raising must NOT block acquire (fail open).
        async with limiter.acquire("t", model_tier="sonnet"):
            pass

    asyncio.run(run())


# ── Enforcement integration: observability hook ─────────────────────────────
import httpx  # noqa: E402

from universal_agent.services import zai_observability as zo  # noqa: E402


def test_hook_aborts_zai_request_when_paused(isolated_control):
    zai_control.set_global_pause(True, ttl_seconds=1000)
    zai_control._invalidate_cache()
    req = httpx.Request("POST", "https://api.z.ai/api/anthropic/v1/messages")
    with pytest.raises(zo.ZAIGloballyPausedError):
        zo._on_request_sync(req)

    async def run():
        with pytest.raises(zo.ZAIGloballyPausedError):
            await zo._on_request_async(req)

    asyncio.run(run())


def test_hook_leaves_non_zai_request_untouched_when_paused(isolated_control):
    zai_control.set_global_pause(True, ttl_seconds=1000)
    zai_control._invalidate_cache()
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    # Non-ZAI host: must pass through even during a global pause.
    zo._on_request_sync(req)
    assert zo._REQUEST_START_EXT_KEY in req.extensions


def test_hook_passes_when_not_paused(isolated_control):
    req = httpx.Request("POST", "https://api.z.ai/api/anthropic/v1/messages")
    zo._on_request_sync(req)  # no pause set → no raise
    assert zo._REQUEST_START_EXT_KEY in req.extensions


def test_hook_fails_open_on_control_error(isolated_control, monkeypatch):
    def boom():
        raise RuntimeError("control broken")

    monkeypatch.setattr(zai_control, "is_globally_paused", boom)
    req = httpx.Request("POST", "https://api.z.ai/api/anthropic/v1/messages")
    # Control read raising must NOT block the ZAI request (fail open).
    zo._on_request_sync(req)
    assert zo._REQUEST_START_EXT_KEY in req.extensions


def test_default_ttl_parse_never_raises(monkeypatch):
    """S1 regression: a malformed TTL env must NOT raise at import/parse time —
    the module's whole contract is 'never raise'. Falls back to 1800."""
    monkeypatch.setenv("UA_ZAI_GLOBAL_PAUSE_DEFAULT_TTL_SECONDS", "not-a-number")
    assert zai_control._default_ttl() == 1800.0
    monkeypatch.setenv("UA_ZAI_GLOBAL_PAUSE_DEFAULT_TTL_SECONDS", "900")
    assert zai_control._default_ttl() == 900.0
