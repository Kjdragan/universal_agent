"""Per-tier AIMD controller tests for ZAIRateLimiter.

Covers the adversarially-reviewed controller semantics:
- `_AdaptiveGate`: live-read dynamic cap, permit-transfer wakes, decrease
  blocks new admissions until drained, no debt bookkeeping (the reviewed
  debt-counter design could deadlock a tier — pinned here).
- Multiplicative decrease at most once per congestion EVENT (>=1 success
  since the last decrease), not per wall-clock window.
- Additive increase only on a sustained clean streak, gated by quiet /
  cooldown / FUP freeze / max bound.
- FUP cliff: all tiers slam to min, increases freeze, acquires fail fast
  (`ZAIFupPauseError`) for the pause window.
- Gradient saturation: 429s persisting at minimum cap escalate to the cliff.
- `with_rate_limit_retry` dispatch against REAL ZAI wire behavior
  (verified 2026-06-11: ZAI's standard throttle is a 429 whose body carries
  code 1313 + Fair-Usage text): 1313-texted 429s are the GRADIENT (retry),
  non-429 FUP text is the CLIFF (stop), and backoff sleeps release the slot.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from universal_agent.rate_limiter import (
    TIERS,
    ZAIFupPauseError,
    ZAIRateLimiter,
    _AdaptiveGate,
    _get_state_path,
    with_rate_limit_retry,
)

FUP_429_TEXT = (
    "Error code: 429 - {'error': {'code': '1313', 'message': \"[1313][Your "
    "account's current usage pattern does not comply with the Fair Usage "
    'Policy, and your request frequency has been limited.]"}}'
)
FUP_CLIFF_TEXT = "[1313] account suspended for Fair Usage Policy violation"


@pytest.fixture
def limiter(tmp_path, monkeypatch):
    """Fresh singleton, fast knobs, isolated snapshot."""
    monkeypatch.setenv("UA_ZAI_INFERENCE_STATE_PATH", str(tmp_path / "zai_state.json"))
    monkeypatch.setenv("ZAI_MIN_INTERVAL", "0.0")
    monkeypatch.setenv("ZAI_INITIAL_BACKOFF", "0.01")
    monkeypatch.setenv("ZAI_MAX_BACKOFF", "0.05")
    monkeypatch.setenv("ZAI_TIER_INCREASE_STREAK", "3")
    monkeypatch.setenv("ZAI_TIER_INCREASE_QUIET_SECONDS", "0")
    monkeypatch.setenv("ZAI_TIER_INCREASE_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("ZAI_FUP_ACQUIRE_PAUSE_SECONDS", "0.3")
    ZAIRateLimiter.reset_instance()
    yield ZAIRateLimiter.get_instance()
    ZAIRateLimiter.reset_instance()


# ── _AdaptiveGate ──────────────────────────────────────────────────────────


def test_gate_enforces_live_cap():
    cap = {"v": 2}
    gate = _AdaptiveGate(lambda: cap["v"])
    peak = 0
    active = 0

    async def one() -> None:
        nonlocal peak, active
        await gate.acquire()
        try:
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
        finally:
            gate.release()

    async def run() -> None:
        await asyncio.gather(*(one() for _ in range(6)))

    asyncio.run(run())
    assert peak <= 2


def test_gate_decrease_blocks_until_drained_then_increase_readmits():
    cap = {"v": 3}
    gate = _AdaptiveGate(lambda: cap["v"])
    order: list[str] = []

    async def run() -> None:
        # Fill all 3 slots.
        for _ in range(3):
            await gate.acquire()
        cap["v"] = 1  # decrease: plain int write, no debt
        waiter = asyncio.ensure_future(gate.acquire())
        await asyncio.sleep(0.01)
        assert not waiter.done()  # 3 holders >= cap 1 → blocked
        gate.release()  # 2 holders — still >= 1
        await asyncio.sleep(0.01)
        assert not waiter.done()
        gate.release()  # 1 holder — still >= 1
        await asyncio.sleep(0.01)
        assert not waiter.done()
        gate.release()  # 0 holders < 1 → waiter admitted
        await asyncio.sleep(0.01)
        assert waiter.done()
        order.append("admitted")
        # Increase while one holder: next acquire admits immediately.
        cap["v"] = 2
        await gate.acquire()
        gate.release()
        gate.release()

    asyncio.run(run())
    assert order == ["admitted"]


def test_gate_never_deadlocks_at_min_cap_after_idle_slam():
    """Pin for the reviewed debt-counter deadlock: a cap decrease applied
    while the gate was never used must leave exactly `new cap` admissions
    available (no phantom debt swallowing permits)."""
    cap = {"v": 4}
    gate = _AdaptiveGate(lambda: cap["v"])
    cap["v"] = 1  # "FUP slam" before first use
    completed = 0

    async def one() -> None:
        nonlocal completed
        await gate.acquire()
        try:
            await asyncio.sleep(0.005)
            completed += 1
        finally:
            gate.release()

    async def run() -> None:
        await asyncio.wait_for(asyncio.gather(*(one() for _ in range(5))), timeout=5)

    asyncio.run(run())
    assert completed == 5  # all complete, serially through the 1 slot


# ── AIMD transitions ───────────────────────────────────────────────────────


def test_multiplicative_decrease_once_per_congestion_event(limiter):
    async def run() -> None:
        assert limiter._tier_cap["haiku"] == 4
        await limiter.record_429("t", model_tier="haiku")  # event 1 → halve
        assert limiter._tier_cap["haiku"] == 2
        # Same event continues (no success in between): NO further halving,
        # however many 429s the retry saga spreads out.
        for _ in range(4):
            await limiter.record_429("t", model_tier="haiku")
        assert limiter._tier_cap["haiku"] == 2
        # A success closes the event; the next 429 is a new event → halve.
        await limiter.record_success(model_tier="haiku")
        await limiter.record_429("t", model_tier="haiku")
        assert limiter._tier_cap["haiku"] == 1  # min bound

    asyncio.run(run())


def test_additive_increase_after_clean_streak(limiter):
    async def run() -> None:
        assert limiter._tier_cap["sonnet"] == 2
        for _ in range(3):  # ZAI_TIER_INCREASE_STREAK=3 in fixture
            await limiter.record_success(model_tier="sonnet")
        assert limiter._tier_cap["sonnet"] == 3
        # Streak resets after an increase; two more successes ≠ another bump.
        for _ in range(2):
            await limiter.record_success(model_tier="sonnet")
        assert limiter._tier_cap["sonnet"] == 3

    asyncio.run(run())


def test_increase_respects_max_bound(limiter):
    async def run() -> None:
        for _ in range(50):
            await limiter.record_success(model_tier="opus")
        assert limiter._tier_cap["opus"] <= limiter._tier_max["opus"] == 3

    asyncio.run(run())


def test_fup_slams_freezes_and_pauses(limiter):
    async def run() -> None:
        # Climb sonnet first so the slam is observable.
        for _ in range(3):
            await limiter.record_success(model_tier="sonnet")
        assert limiter._tier_cap["sonnet"] == 3

        await limiter.record_fup_signal("cliff-test", FUP_CLIFF_TEXT)
        assert all(limiter._tier_cap[t] == limiter._tier_min[t] for t in TIERS)

        # Acquire fail-fasts during the pause...
        with pytest.raises(ZAIFupPauseError):
            async with limiter.acquire("cliff-test", model_tier="sonnet"):
                pass
        # ...for legacy (tierless) callers too — the cliff is account-level.
        with pytest.raises(ZAIFupPauseError):
            async with limiter.acquire("cliff-test"):
                pass

        # Increases are frozen even after a clean streak.
        await asyncio.sleep(0.35)  # pause (0.3s) expires; freeze (1800s) does not
        for _ in range(5):
            await limiter.record_success(model_tier="sonnet")
        assert limiter._tier_cap["sonnet"] == limiter._tier_min["sonnet"]

        # And acquires work again at min caps after the pause.
        async with limiter.acquire("cliff-test", model_tier="sonnet"):
            pass

    asyncio.run(run())


def test_gradient_saturation_escalates_to_cliff(limiter, monkeypatch):
    async def run() -> None:
        # Drive haiku to min via one halve + a success + another event...
        # simpler: opus seeds at cap 1 == min already.
        assert limiter._tier_cap["opus"] == limiter._tier_min["opus"] == 1
        for _ in range(6):  # ZAI_TIER_SATURATION_429S default 6, within 10s
            await limiter.record_429("sat-test", model_tier="opus")
        assert limiter._total_fup_events == 1
        assert limiter._acquire_pause_until > 0
        with pytest.raises(ZAIFupPauseError):
            async with limiter.acquire("sat-test", model_tier="opus"):
                pass

    asyncio.run(run())


# ── with_rate_limit_retry dispatch ─────────────────────────────────────────


def test_fup_texted_429_is_gradient_not_cliff(limiter):
    """ZAI's standard throttle (429 + 1313 body) must be retried as a
    rate-limit, NOT treated as the account cliff."""
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError(FUP_429_TEXT)
        return "ok"

    result = asyncio.run(
        with_rate_limit_retry(flaky, context="t", model_tier="sonnet")
    )
    assert result == "ok"
    assert calls["n"] == 3
    assert limiter._total_fup_events == 0  # no cliff recorded
    assert limiter._tier_total_429s["sonnet"] == 2
    assert limiter._total_succeeded_after_retry == 1


def test_non_429_fup_is_cliff_no_retry(limiter):
    calls = {"n": 0}

    async def doomed() -> str:
        calls["n"] += 1
        raise RuntimeError(FUP_CLIFF_TEXT)

    with pytest.raises(RuntimeError, match="account suspended"):
        asyncio.run(with_rate_limit_retry(doomed, context="t", model_tier="sonnet"))
    assert calls["n"] == 1  # never retried
    assert limiter._total_fup_events == 1


def test_retries_exhausted_counts_and_raises(limiter):
    async def always_429() -> str:
        raise RuntimeError(FUP_429_TEXT)

    with pytest.raises(RuntimeError, match="429"):
        asyncio.run(
            with_rate_limit_retry(
                always_429, max_retries=2, context="t", model_tier="haiku"
            )
        )
    assert limiter._total_429s_exhausted == 1


def test_backoff_sleep_releases_the_slot(limiter, monkeypatch):
    """While one call sleeps in backoff, another call on the SAME cap-1
    tier must be admitted — the slot is not held through the sleep."""
    monkeypatch.setattr(
        limiter, "get_backoff", lambda attempt, model_tier=None: 0.2
    )
    overlap = {"seen": False}

    async def failing_then_ok() -> str:
        raise RuntimeError(FUP_429_TEXT)

    async def quick() -> str:
        overlap["seen"] = True
        return "ok"

    async def run() -> None:
        saga = asyncio.ensure_future(
            with_rate_limit_retry(
                failing_then_ok, max_retries=2, context="a", model_tier="opus"
            )
        )
        await asyncio.sleep(0.05)  # saga is now inside its 0.2s backoff sleep
        # opus cap is 1; this acquire succeeds only if the slot was released.
        await asyncio.wait_for(
            with_rate_limit_retry(quick, context="b", model_tier="opus"),
            timeout=0.15,
        )
        with pytest.raises(RuntimeError):
            await saga

    asyncio.run(run())
    assert overlap["seen"]


def test_max_total_seconds_budget_stops_retry_saga(limiter, monkeypatch):
    monkeypatch.setattr(
        limiter, "get_backoff", lambda attempt, model_tier=None: 10.0
    )

    async def always_429() -> str:
        raise RuntimeError(FUP_429_TEXT)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="429"):
            await asyncio.wait_for(
                with_rate_limit_retry(
                    always_429,
                    max_retries=5,
                    context="t",
                    model_tier="mid",
                    max_total_seconds=0.5,
                ),
                timeout=2.0,  # would be ~40s without the budget
            )

    asyncio.run(run())


# ── snapshot ───────────────────────────────────────────────────────────────


def test_snapshot_carries_tier_state_and_identity(limiter):
    async def run() -> None:
        await limiter.record_429("snap-test", model_tier="sonnet")

    asyncio.run(run())
    snap = json.loads(_get_state_path().read_text())
    assert snap["pid"] == os.getpid()
    assert "process_name" in snap and "singleton_created_at" in snap
    assert set(snap["tiers"]) == set(TIERS)
    sonnet = snap["tiers"]["sonnet"]
    assert sonnet["total_429s"] == 1
    assert sonnet["cap"] == 1  # halved 2 → 1 by the first congestion event
    assert sonnet["decrease_count"] == 1
    assert snap["total_429s_exhausted"] == 0
