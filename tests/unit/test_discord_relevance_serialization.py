"""Regression: the discord relevance sweep must classify sub-batches one ZAI call
at a time, never concurrently.

`run_relevance_sweep` previously fanned out sub-batches via `asyncio.gather`
(up to `max_workers` concurrent LLM calls), which trips ZAI's Fair-Usage limiter
(error 1313) on the shared rate-limited account. It now runs them sequentially
(and each `classify_batch` additionally holds the daemon-wide `ZAI_CALL_LOCK`).
This test fails if the concurrent fan-out is reintroduced.
"""

from __future__ import annotations

import asyncio

from discord_intelligence import relevance_filter


class _FakeDB:
    def __init__(self, messages):
        self._messages = messages
        self.marked = None

    def get_unfiltered_messages(self, limit):
        return self._messages[:limit]

    def mark_messages_meaningful(self, results):
        self.marked = results


async def test_run_relevance_sweep_serializes_sub_batches(monkeypatch):
    # 120 messages with max_batch_size=50 → 3 sub-batches; concurrent fan-out
    # would show max in-flight > 1.
    messages = [{"id": f"m{i}", "content": f"msg {i}"} for i in range(120)]
    state = {"current": 0, "max": 0}

    async def fake_classify_batch(batch, model=None):
        state["current"] += 1
        state["max"] = max(state["max"], state["current"])
        try:
            await asyncio.sleep(0.01)
            return [(m["id"], False) for m in batch]
        finally:
            state["current"] -= 1

    monkeypatch.setattr(relevance_filter, "classify_batch", fake_classify_batch)

    db = _FakeDB(messages)
    result = await relevance_filter.run_relevance_sweep(db, max_batch_size=50, max_workers=2)

    assert state["max"] == 1, (
        f"relevance sweep ran {state['max']} classify_batch calls concurrently; "
        "expected strictly sequential (concurrency 1)"
    )
    assert result["processed"] == 100  # max_batch_size * max_workers = fetch cap


def test_zai_call_lock_is_a_lock():
    from discord_intelligence.llm_gate import ZAI_CALL_LOCK

    assert isinstance(ZAI_CALL_LOCK, asyncio.Lock)
