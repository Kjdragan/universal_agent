"""Tests for the ZAI Fair-Usage (FUP) pressure-cut change set (2026-06-11).

ZAI was account-level Fair-Usage throttling UA (~47-60% of calls returning
``[1313] ... Fair Usage Policy`` 429s). This suite covers the frequency-cut
subset of the fix:

  - The convergence LLM clustering pass aborts the REST of a run on the first
    FUP signal instead of grinding through ~60 more doomed refine calls
    (``proactive_convergence._detect_clusters_llm_async`` circuit breaker).
  - The Mission Control Chief-of-Staff tier-2 cadence ceiling was raised
    300s -> 1800s to cut its ~12-13 opus-tier readouts/hour ~6x
    (``mission_control_intelligence_sweeper.SweeperConfig``).
  - The Chief-of-Staff ZAI-error branch records a throttle signal 429-shape FIRST
    (``mission_control_chief_of_staff._record_throttle``): a 429-shaped error
    records via ``record_429`` EVEN when its body also carries the ``[1313]``
    Fair-Usage text (verified 1058/1058 on the VPS), so routine throttle does NOT
    page the watchdog's CRITICAL FUP tier; ``record_fup_signal`` is reserved for a
    genuine NON-429 account-level cliff (e.g. a 403 suspension).

The duplicate-convergence-invoker removal from ``proactive_signals``
is covered in ``tests/unit/test_proactive_signals.py``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from universal_agent.services import (
    mission_control_chief_of_staff as cos,
    proactive_convergence as pc,
)
from universal_agent.services.mission_control_intelligence_sweeper import SweeperConfig

# ── Change 2: FUP circuit breaker in convergence clustering ──────────────


def _buckets(n: int) -> list[list[dict]]:
    """Fabricate ``n`` coarse buckets of 2-3 signature dicts each (the shape
    ``_detect_clusters_sql`` returns)."""
    out: list[list[dict]] = []
    for i in range(n):
        out.append(
            [
                {"video_id": f"v{i}a", "channel_name": f"ch{i}a", "video_title": f"t{i}a"},
                {"video_id": f"v{i}b", "channel_name": f"ch{i}b", "video_title": f"t{i}b"},
            ]
        )
    return out


def test_fup_signal_trips_breaker_and_skips_remaining_buckets(monkeypatch):
    """A [1313] Fair-Usage error on an early bucket must abort the run: the
    refine function is called only a handful of times (≈ concurrency), not once
    per bucket."""
    buckets = _buckets(12)
    monkeypatch.setattr(pc, "_detect_clusters_sql", lambda *a, **k: buckets)
    monkeypatch.setenv("UA_CONVERGENCE_LLM_CONCURRENCY", "2")

    calls = {"n": 0}

    async def fup_first_refine(bucket, *, min_channels):
        calls["n"] += 1
        # The FIRST refine raises a FUP-classified ([1313]) error; the rest would
        # succeed if they ran — but the breaker should stop them.
        if calls["n"] == 1:
            raise RuntimeError("HTTP 429: [1313] Fair Usage Policy — back off")
        return {"signatures": bucket, "thesis": "t", "signal_strength": 9.0}

    monkeypatch.setattr(pc, "_refine_cluster_with_llm", fup_first_refine)

    out = asyncio.run(
        pc._detect_clusters_llm_async(None, source_window_hours=72, min_channels=2)
    )

    # The run completes (no exception propagates) ...
    assert isinstance(out, list)
    # ... with no/partial clusters (the tripping bucket produced nothing, and the
    # rest were skipped before their LLM call).
    assert len(out) < len(buckets)
    # The breaker stopped the fan-out far short of one-call-per-bucket. With
    # concurrency 2, at most ~2-3 buckets start before the flag is observed.
    assert calls["n"] <= 3, f"breaker did not stop the fan-out ({calls['n']} refine calls)"


def test_non_fup_error_does_not_trip_breaker(monkeypatch):
    """A plain (non-FUP) error on one bucket must NOT abort the run — every other
    bucket is still processed and a partial result is returned."""
    buckets = _buckets(8)
    monkeypatch.setattr(pc, "_detect_clusters_sql", lambda *a, **k: buckets)
    monkeypatch.setenv("UA_CONVERGENCE_LLM_CONCURRENCY", "2")

    calls = {"n": 0}

    async def one_plain_failure_refine(bucket, *, min_channels):
        calls["n"] += 1
        # First bucket fails with a generic (non-FUP) error; others succeed.
        if bucket[0]["video_id"] == "v0a":
            raise RuntimeError("transient parse error, not fair-usage")
        return {"signatures": bucket, "thesis": "t", "signal_strength": 9.0}

    monkeypatch.setattr(pc, "_refine_cluster_with_llm", one_plain_failure_refine)

    out = asyncio.run(
        pc._detect_clusters_llm_async(None, source_window_hours=72, min_channels=2)
    )

    # Every bucket was attempted (no early abort) ...
    assert calls["n"] == len(buckets)
    # ... and the 7 non-failing buckets produced clusters.
    assert len(out) == len(buckets) - 1


def test_refine_cluster_reraises_only_on_fup():
    """The unit beneath the breaker: ``_refine_cluster_with_llm`` re-raises a FUP
    error (so the caller can trip the breaker) but still fails closed (returns
    None) for any other error.

    ``_refine_cluster_with_llm`` imports ``_call_llm`` inline from
    ``llm_classifier`` (so the breaker re-raise sees the live exception), so we
    patch it at its source module — the same seam the other clustering tests use.
    """
    bucket = _buckets(1)[0]

    fup_call = AsyncMock(side_effect=RuntimeError("[1313] Fair Usage Policy"))
    plain_call = AsyncMock(side_effect=RuntimeError("some other transient error"))

    with patch("universal_agent.services.llm_classifier._call_llm", fup_call):
        with pytest.raises(RuntimeError, match="1313"):
            asyncio.run(pc._refine_cluster_with_llm(bucket, min_channels=2))

    with patch("universal_agent.services.llm_classifier._call_llm", plain_call):
        # Non-FUP errors still fail closed.
        assert asyncio.run(pc._refine_cluster_with_llm(bucket, min_channels=2)) is None


# ── Change 3: Chief-of-Staff tier-2 cadence ceiling 300 -> 1800 -> 3600 ──


def test_sweeper_tier2_ceiling_default_is_3600(monkeypatch):
    """With no env override the tier-2 (Chief-of-Staff) ceiling is the
    3600s default — widened from 1800s in the 2026-06-12 LLM-efficiency pass
    (was 300s before 2026-06-11) — on BOTH the dataclass field and the
    ``from_env`` fallback."""
    monkeypatch.delenv("UA_MISSION_CONTROL_TIER2_CEILING_S", raising=False)
    # Dataclass field default.
    assert SweeperConfig().tier2_ceiling_seconds == 3600.0
    # from_env fallback (no env set).
    assert SweeperConfig.from_env().tier2_ceiling_seconds == 3600.0


def test_sweeper_tier2_ceiling_env_override_still_wins(monkeypatch):
    """The env override remains effective (operator can dial it back)."""
    monkeypatch.setenv("UA_MISSION_CONTROL_TIER2_CEILING_S", "600")
    assert SweeperConfig.from_env().tier2_ceiling_seconds == 600.0


# ── Change 4: Chief-of-Staff throttle recording (429-shape FIRST) ────────
#
# VERIFIED ON VPS 2026-06-11: 1058/1058 logged ZAI 429s carry the [1313]
# Fair-Usage text. So the throttle is a 429-SHAPED error whose body *also*
# matches `_is_fup_error`. A naive "FUP-text wins" rule would record EVERY
# routine throttle as a FUP signal and the watchdog's no-grace CRITICAL FUP tier
# would page continuously. The correct rule: 429-shape wins (retryable
# record_429) even when the [1313] text is present; record_fup_signal is
# reserved for a genuine NON-429 account-level cliff (e.g. a 403 suspension).


class _RecordingLimiter:
    """Records which throttle method was invoked (FUP vs 429), exclusively."""

    def __init__(self) -> None:
        self.fup_calls: list[tuple[str, str]] = []
        self.r429_calls: list[str] = []

    async def record_fup_signal(self, context: str = "", error_snippet: str = "") -> None:
        self.fup_calls.append((context, error_snippet))

    async def record_429(self, context: str = "") -> None:
        self.r429_calls.append(context)


def test_record_throttle_1313_texted_429_records_429_not_fup():
    """The dominant real case: a 429-shaped error whose body ALSO carries the
    [1313] Fair-Usage text records via record_429 (retryable) — NOT
    record_fup_signal — so routine throttle does not page the CRITICAL FUP tier."""
    limiter = _RecordingLimiter()
    exc_str = "HTTP 429: [1313] Fair Usage Policy violation"
    asyncio.run(cos._record_throttle(limiter, cos.SERVICE_SOURCE, exc_str))

    assert limiter.r429_calls == [cos.SERVICE_SOURCE]
    assert limiter.fup_calls == []  # 429-shape wins — NOT a FUP page


def test_record_throttle_non_429_account_cliff_records_fup():
    """A genuine NON-429 account-level Fair-Usage signal (e.g. a 403 suspension /
    [1313] with no 429 marker) is the real STOP cliff → record_fup_signal."""
    limiter = _RecordingLimiter()
    exc_str = "HTTP 403: [1313] account suspended — Fair Usage Policy"
    asyncio.run(cos._record_throttle(limiter, cos.SERVICE_SOURCE, exc_str))

    assert limiter.fup_calls == [(cos.SERVICE_SOURCE, exc_str)]
    assert limiter.r429_calls == []


def test_record_throttle_routes_plain_429_to_record_429():
    """A plain 429 (no FUP markers) records via the generic 429 path."""
    limiter = _RecordingLimiter()
    exc_str = "HTTP 429: too many requests, slow down"
    asyncio.run(cos._record_throttle(limiter, cos.SERVICE_SOURCE, exc_str))

    assert limiter.fup_calls == []
    assert limiter.r429_calls == [cos.SERVICE_SOURCE]


def test_record_throttle_ignores_non_throttle_error():
    """A non-throttle error records nothing on either path."""
    limiter = _RecordingLimiter()
    asyncio.run(cos._record_throttle(limiter, cos.SERVICE_SOURCE, "ValueError: bad json"))

    assert limiter.fup_calls == []
    assert limiter.r429_calls == []


# ── synthesize_readout: FUP-pause fallback + exhaustion reporting ──────────
# (MF-4 / MF-5 from the AIMD implementation review.)


class _PausedLimiter:
    """acquire() fail-fasts like the real limiter during a FUP pause."""

    def __init__(self) -> None:
        self.exhausted_notes = 0

    def acquire(self, context: str = "", model_tier=None):
        from universal_agent.rate_limiter import ZAIFupPauseError

        raise ZAIFupPauseError("ZAI FUP acquire-pause active for another 120s")

    def note_retry_exhausted(self) -> None:
        self.exhausted_notes += 1


class _Throttled429Limiter:
    """acquire() works; the call site sees 429s; backoff is instant."""

    def __init__(self) -> None:
        self.exhausted_notes = 0
        self.r429 = 0

    def acquire(self, context: str = "", model_tier=None):
        import contextlib

        @contextlib.asynccontextmanager
        async def _cm():
            yield

        return _cm()

    async def record_429(self, context: str = "") -> None:
        self.r429 += 1

    async def record_success(self) -> None:  # pragma: no cover — not reached
        pass

    async def record_fup_signal(self, context: str = "", error_snippet: str = "") -> None:
        raise AssertionError("1313-texted 429s must not record FUP")

    def get_backoff(self, attempt: int, model_tier=None) -> float:
        return 0.0

    def note_retry_exhausted(self) -> None:
        self.exhausted_notes += 1


def _readout_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    monkeypatch.setenv("UA_MISSION_CONTROL_COS_MAX_RETRIES", "2")


def test_synthesize_readout_falls_back_on_fup_pause(monkeypatch):
    """MF-4 pin: during the account-level acquire-pause, synthesize_readout
    honors its never-raise contract — fallback readout, no propagation."""
    _readout_env(monkeypatch)
    fake = _PausedLimiter()
    monkeypatch.setattr(cos.ZAIRateLimiter, "get_instance", classmethod(lambda cls: fake))

    readout, model = asyncio.run(cos.synthesize_readout({"generated_at_utc": "2026-06-11T15:00:00+00:00"}))
    assert readout.get("synthesis_status") != "ok"
    assert "pause" in str(readout.get("synthesis_error") or readout).lower()
    # Pause is not a 429-shaped exhaustion — no exhaustion note.
    assert fake.exhausted_notes == 0


def test_synthesize_readout_notes_exhaustion_on_429_failure(monkeypatch):
    """MF-5 pin: a readout that burns all retries on 429-shaped errors
    reports the exhaustion outcome to the limiter snapshot (the watchdog
    alarms on outcomes, not wire counts)."""
    _readout_env(monkeypatch)
    fake = _Throttled429Limiter()
    monkeypatch.setattr(cos.ZAIRateLimiter, "get_instance", classmethod(lambda cls: fake))

    class _FailingClient:
        class messages:  # noqa: N801 — mimic SDK shape
            @staticmethod
            async def create(**kwargs):
                raise RuntimeError("Error code: 429 - [1313] Fair Usage Policy")

    import anthropic

    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kw: _FailingClient())

    readout, model = asyncio.run(cos.synthesize_readout({"generated_at_utc": "2026-06-11T15:00:00+00:00"}))
    assert readout.get("synthesis_status") != "ok"
    assert fake.r429 == 2  # both retries throttled
    assert fake.exhausted_notes == 1  # exactly one exhaustion per saga
