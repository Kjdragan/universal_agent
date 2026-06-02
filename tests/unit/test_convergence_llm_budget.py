"""Regression tests for the csi_convergence_sync timeout fix (2026-06-02).

The convergence detection ran one sequential opus-tier LLM refine per coarse
bucket; a recall window of dozens of buckets overran the 900s cron timeout every
run, flooding the operator's inbox with `[ERROR] Autonomous Task Failed`. The fix
parallelises the per-bucket refines (bounded concurrency) and time-boxes the LLM
phases (UA_CSI_CONVERGENCE_BUDGET_SECONDS) so the run always exits cleanly.
"""

from __future__ import annotations

import asyncio
import time

from universal_agent.services import proactive_convergence as pc


def test_detect_clusters_llm_runs_refines_concurrently(monkeypatch):
    """12 buckets × 0.1s refine at concurrency 6 must finish well under the 1.2s
    a sequential loop would take."""
    buckets = [[{"video_id": f"v{i}", "channel_name": f"c{i}"}] for i in range(12)]
    monkeypatch.setattr(pc, "_detect_clusters_sql", lambda *a, **k: buckets)

    async def slow_refine(bucket, *, min_channels):
        await asyncio.sleep(0.1)
        return {"signatures": bucket, "thesis": "t", "signal_strength": 9.0}

    monkeypatch.setattr(pc, "_refine_cluster_with_llm", slow_refine)
    monkeypatch.setenv("UA_CONVERGENCE_LLM_CONCURRENCY", "6")

    t0 = time.monotonic()
    out = asyncio.run(
        pc._detect_clusters_llm_async(None, source_window_hours=72, min_channels=2)
    )
    elapsed = time.monotonic() - t0

    assert len(out) == 12
    assert elapsed < 0.6, f"refines did not run concurrently (took {elapsed:.2f}s)"


def test_detect_clusters_llm_respects_deadline(monkeypatch):
    """A deadline already in the past must skip every refine and return empty —
    proving the budget can stop the LLM work before the cron timeout fires."""
    buckets = [[{"video_id": f"v{i}", "channel_name": f"c{i}"}] for i in range(20)]
    monkeypatch.setattr(pc, "_detect_clusters_sql", lambda *a, **k: buckets)
    calls = {"n": 0}

    async def counting_refine(bucket, *, min_channels):
        calls["n"] += 1
        return {"signatures": bucket, "thesis": "t", "signal_strength": 9.0}

    monkeypatch.setattr(pc, "_refine_cluster_with_llm", counting_refine)
    monkeypatch.setenv("UA_CONVERGENCE_LLM_CONCURRENCY", "2")

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

    assert captured.get("timeout") == 60.0
    assert captured.get("max_retries") == 1
