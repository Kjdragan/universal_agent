"""Regression: csi_watchlist channel classification must run one LLM call at a time.

YouTube ingest (``add_channel`` + the ``/reclassify`` endpoint) can fan out many
concurrent ``_classify_channel_llm`` calls. FastAPI runs those request handlers in
parallel, so without a guard the LLM calls hit the rate-limited ZAI account
simultaneously and trip its Fair-Usage limiter (error ``1313``), each then
retrying and amplifying the storm. ``_classify_channel_llm`` holds a process-wide
concurrency-1 ``asyncio.Lock`` around the LLM call so classification is strictly
sequential ("one agent"). This test fails if that serialization regresses.
"""

from __future__ import annotations

import asyncio

from universal_agent.api.routers import csi_watchlist
import universal_agent.services.llm_classifier as llm_classifier


async def test_classify_channel_llm_serializes_concurrent_calls(monkeypatch):
    state = {"current": 0, "max": 0}

    async def fake_call_llm(*, system, user, max_tokens=100):
        state["current"] += 1
        state["max"] = max(state["max"], state["current"])
        try:
            # Hold the slot long enough that any parallelism would be observed.
            await asyncio.sleep(0.02)
            return '{"category": "other_signal"}'
        finally:
            state["current"] -= 1

    # _classify_channel_llm imports _call_llm from this module at call time, so
    # patching the module attribute is picked up by every call.
    monkeypatch.setattr(llm_classifier, "_call_llm", fake_call_llm)

    results = await asyncio.gather(
        *[csi_watchlist._classify_channel_llm(f"Channel {i}") for i in range(8)]
    )

    assert state["max"] == 1, (
        f"channel classification ran {state['max']} LLM calls concurrently; "
        "expected strictly serialized (concurrency 1)"
    )
    assert all(category == "other_signal" for category, _method in results)
