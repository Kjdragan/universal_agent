"""Regression tests for the csi_convergence_sync timeout fix (2026-06-02) and the
batched judge (2026-06-13).

The convergence detection originally ran one sequential opus-tier LLM refine per
coarse bucket; a recall window of dozens of buckets overran the 900s cron timeout
every run. The 2026-06-13 batched judge replaces N per-bucket calls with one
structured-output call per CHUNK of ``UA_CONVERGENCE_JUDGE_BATCH_SIZE`` buckets
(default 20) — collapsing the call count — while a shared deadline
(UA_CSI_CONVERGENCE_BUDGET_SECONDS) still time-boxes the run.
"""

from __future__ import annotations

import asyncio
import time

from universal_agent.services import proactive_convergence as pc


def test_detect_clusters_llm_batches_buckets(monkeypatch):
    """The batched judge makes ceil(N / batch_size) LLM calls, not N — the
    call-count collapse. 12 buckets at batch_size 5 -> 3 chunk calls, all judged."""
    buckets = [
        [{"video_id": f"v{i}", "channel_name": f"c{i}a"},
         {"video_id": f"w{i}", "channel_name": f"c{i}b"}]
        for i in range(12)
    ]
    monkeypatch.setattr(pc, "_detect_clusters_sql", lambda *a, **k: buckets)
    monkeypatch.setenv("UA_CONVERGENCE_JUDGE_BATCH_SIZE", "5")
    monkeypatch.setenv("UA_CONVERGENCE_REFINE_CACHE_ENABLED", "0")

    chunk_calls = {"n": 0}

    async def fake_batched(chunk, *, min_channels):
        chunk_calls["n"] += 1
        return [{"signatures": b, "thesis": "t", "signal_strength": 9.0} for b in chunk]

    monkeypatch.setattr(pc, "_refine_clusters_batched", fake_batched)

    stats: dict = {}
    out = asyncio.run(
        pc._detect_clusters_llm_async(None, source_window_hours=72, min_channels=2, stats=stats)
    )
    assert len(out) == 12              # every bucket judged
    assert chunk_calls["n"] == 3       # 12 buckets / batch 5 = 3 calls, not 12
    assert stats["sonnet_calls_made"] == 3


def test_detect_clusters_llm_respects_deadline(monkeypatch):
    """A deadline already in the past must skip every refine and return empty —
    proving the budget can stop the LLM work before the cron timeout fires."""
    buckets = [[{"video_id": f"v{i}", "channel_name": f"c{i}"}] for i in range(20)]
    monkeypatch.setattr(pc, "_detect_clusters_sql", lambda *a, **k: buckets)
    calls = {"n": 0}

    async def counting_batched(chunk, *, min_channels):
        calls["n"] += 1
        return [{"signatures": b, "thesis": "t", "signal_strength": 9.0} for b in chunk]

    monkeypatch.setattr(pc, "_refine_clusters_batched", counting_batched)
    # Disable the refine cache so the deadline check is the sole gating
    # mechanism (the test verifies 0 LLM chunk calls on an expired deadline).
    monkeypatch.setenv("UA_CONVERGENCE_REFINE_CACHE_ENABLED", "0")

    out = asyncio.run(
        pc._detect_clusters_llm_async(
            None,
            source_window_hours=72,
            min_channels=2,
            deadline=time.monotonic() - 1.0,
        )
    )

    assert out == []
    assert calls["n"] == 0, f"deadline not enforced ({calls['n']} refine calls ran)"


def test_get_anthropic_client_bounds_timeout_and_retries(monkeypatch):
    """Every LLM call must carry a bounded timeout + retries so a stalled ZAI
    proxy can't hang a caller for the SDK-default ~10 min."""
    import anthropic

    from universal_agent.services import llm_classifier as llm

    captured: dict = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("UA_LLM_CALL_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("UA_LLM_CALL_MAX_RETRIES", raising=False)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeClient)

    asyncio.run(llm._get_anthropic_client())

    # Default raised 60s -> 180s on 2026-06-03: the 60s cap was shorter than the
    # ZAI/glm latency tail for the large convergence-triage prompt and stalled the
    # promoter. Still bounded (well under the SDK-default ~10 min).
    assert captured.get("timeout") == 180.0
    assert captured.get("max_retries") == 1
