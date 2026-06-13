"""Process-wide serialization gate for the discord daemon's ZAI/LLM calls.

The intelligence daemon runs four concurrent ``tasks.loop()``s on a single event
loop — triage (60 min), relevance sweep (5 min), event poll (30 min), audio
maintenance (6 h) — all sharing one rate-limited ZAI account. Without
coordination they can fire concurrent LLM calls (e.g. a relevance sweep
overlapping a triage batch) and trip ZAI's Fair-Usage limiter (error ``1313``).

This module exposes a single ``asyncio.Lock`` so every LLM call across the daemon
runs strictly one-at-a-time. It is the deliberate latency-for-rate-limit-headroom
tradeoff — the discord analog of the csi_watchlist classification serialization
(PR #961). Acquire it as the OUTERMOST context, outside the per-call
``ZAIRateLimiter.acquire()``:

    from .llm_gate import ZAI_CALL_LOCK
    async with ZAI_CALL_LOCK, limiter.acquire():
        response = await client.messages.create(...)

All call sites must acquire it in the same order (lock first) so there is no
deadlock; the lock is never held re-entrantly (no locked call invokes another).
"""

import asyncio

# At most one in-flight discord LLM call at a time, process-wide.
ZAI_CALL_LOCK = asyncio.Lock()
