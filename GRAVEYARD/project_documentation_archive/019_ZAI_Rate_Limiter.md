# ZAI API Rate Limiter

**Created:** 2026-01-26  
**Status:** Implemented  
**Location:** `src/universal_agent/rate_limiter.py`

---

## Overview

Centralized rate limiter for all ZAI API calls (via `api.z.ai/api/anthropic`). Ensures consistent rate limiting across all report generation components with adaptive backoff and logfire instrumentation.

## Problem Statement

The report generation pipeline makes parallel API calls from multiple components:
- `parallel_draft.py` — Drafts report sections concurrently
- `cleanup_report.py` — Cleans and normalizes drafted sections
- `generate_outline.py` — Creates report structure
- `corpus_refiner.py` — Extracts key facts from research

Without coordination, these components would:
1. Each have their own semaphore (no global limit)
2. Use SDK-level retries that conflict with app-level retries
3. Create "thundering herd" effects when rate limits hit
4. Generate 15+ retry attempts per request

## Solution Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    ZAIRateLimiter (Singleton)               │
├─────────────────────────────────────────────────────────────┤
│  • Global semaphore (configurable, default 3)               │
│  • Adaptive backoff floor (rises on 429s, decays on success)│
│  • Staggered release (50-200ms jitter)                      │
│  • Logfire instrumentation for monitoring                   │
└─────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
   parallel_draft      corpus_refiner       cleanup_report
   generate_outline
```

## Configuration

Environment variables (all optional):

| Variable | Default | Description |
|----------|---------|-------------|
| `ZAI_MAX_CONCURRENT` | `2` | Maximum parallel API requests |
| `ZAI_INITIAL_BACKOFF` | `1.0` | Initial backoff floor (seconds) |
| `ZAI_MAX_BACKOFF` | `30.0` | Maximum backoff cap (seconds) |
| `ZAI_MIN_INTERVAL` | `0.5` | Minimum seconds between request starts (prevents burst rate limits) |

## Usage

### Basic Usage (Recommended)

```python
from universal_agent.rate_limiter import ZAIRateLimiter

limiter = ZAIRateLimiter.get_instance()

async with limiter.acquire("my_context"):
    result = await client.messages.create(...)
    await limiter.record_success()
```

### With Rate Limit Handling

```python
from universal_agent.rate_limiter import ZAIRateLimiter

limiter = ZAIRateLimiter.get_instance()
MAX_RETRIES = 5

for attempt in range(MAX_RETRIES):
    async with limiter.acquire("my_context"):
        try:
            result = await client.messages.create(...)
            await limiter.record_success()
            break
        except Exception as e:
            if "429" in str(e).lower():
                await limiter.record_429("my_context")
                delay = limiter.get_backoff(attempt)
                await asyncio.sleep(delay)
            else:
                raise
```

### Convenience Wrapper

```python
from universal_agent.rate_limiter import with_rate_limit_retry

result = await with_rate_limit_retry(
    client.messages.create,
    model=MODEL,
    max_tokens=4000,
    messages=[...],
    context="section_drafting"
)
```

## Key Design Decisions

### 1. SDK Retries Disabled

All Anthropic clients use `max_retries=0`. The rate limiter handles retries with smarter backoff.

**Rationale:** SDK retries (0.4s, 0.8s, 1.6s) are too fast and don't coordinate with other requests. This caused 15+ retry attempts per request.

### 2. Adaptive Backoff Floor

The backoff floor starts at 1 second and adjusts:
- **On 429:** Floor increases by 1.5× (max 8s)
- **On success:** Floor decays by 0.9× (min 1s)

**Rationale:** If rate limits persist, longer waits are needed. If requests succeed, we can be more aggressive.

### 3. Staggered Acquisition

Each `acquire()` adds 50-200ms random jitter before yielding.

**Rationale:** Prevents all 3 concurrent requests from hitting the API at the exact same millisecond.

### 4. Singleton Pattern

One limiter instance shared across all components via `get_instance()`.

**Rationale:** Global coordination is required for effective rate limiting.

## Components Updated

| File | Changes |
|------|---------|
| `scripts/parallel_draft.py` | Removed local semaphore, uses limiter |
| `scripts/cleanup_report.py` | Uses limiter with adaptive backoff |
| `scripts/generate_outline.py` | Uses limiter with adaptive backoff |
| `tools/corpus_refiner.py` | Replaced local semaphore with limiter |

## Monitoring

Rate limit events are logged to Logfire:

- `zai_rate_limiter_initialized` — On startup with config
- `zai_rate_limit_hit` — On each 429, includes context and backoff state

Query in Logfire:
```
service.name:universal-agent AND message:zai_rate_limit_hit
```

## Testing

Verify imports work:
```bash
uv run python -c "from universal_agent.rate_limiter import ZAIRateLimiter; print('OK')"
```

Verify all updated modules import:
```bash
uv run python -c "
from universal_agent.scripts.parallel_draft import draft_report_async
from universal_agent.scripts.cleanup_report import cleanup_report_async
from universal_agent.scripts.generate_outline import generate_outline_async
from universal_agent.tools.corpus_refiner import refine_corpus
print('All modules import OK')
"
```

## Future Improvements

1. **Token bucket** — More sophisticated rate limiting based on tokens/minute
2. **Per-model limits** — Different limits for Haiku vs Sonnet
3. **Circuit breaker** — Fail fast if API is consistently unavailable
4. **Metrics export** — Prometheus metrics for rate limit frequency
