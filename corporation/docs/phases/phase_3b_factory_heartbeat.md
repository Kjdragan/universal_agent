# Phase 3b: Factory Heartbeat Protocol

**Status:** Not Started
**Priority:** High — required for fleet visibility
**Depends on:** Phase 3a (consumer loop provides the heartbeat sender host)

---

## Objective

Implement a periodic heartbeat protocol so that every factory (HQ and workers) keeps its registration fresh. The Corporation View already has stale detection UI (>5 min threshold) — this phase provides the data to make it accurate.

## Current State

- `_register_local_factory_presence()` runs once at gateway startup (`gateway_server.py` lifespan)
- Tutorial worker has its own heartbeat loop (`_post_registration()` every 60s) — works but is bespoke
- Corporation View checks `last_seen_at` for stale detection but nothing keeps it updated post-startup
- Factory registrations are **in-memory only** (`_factory_registrations` dict) — lost on gateway restart

## Files to Create

### 1. `src/universal_agent/delegation/heartbeat.py`

```python
"""Factory heartbeat sender — periodically refreshes registration with HQ."""

import asyncio
import logging
import socket
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class HeartbeatConfig:
    hq_base_url: str
    ops_token: str
    factory_id: str
    factory_role: str
    deployment_profile: str
    capabilities: list[str]
    interval_seconds: float = 60.0
    timeout_seconds: float = 15.0


class FactoryHeartbeat:
    """Sends periodic registration heartbeats to HQ."""

    def __init__(self, config: HeartbeatConfig) -> None:
        self._config = config
        self._last_sent_at: float = 0.0
        self._consecutive_failures: int = 0
        self._running = False

    def should_send(self) -> bool:
        return (time.time() - self._last_sent_at) >= self._config.interval_seconds

    def send_sync(self) -> bool:
        """Synchronous heartbeat POST. Returns True on success."""
        ...

    async def send_async(self) -> bool:
        """Async heartbeat POST. Returns True on success."""
        ...

    async def run_loop(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Run heartbeat loop until stop_event is set or cancelled."""
        ...

    @property
    def is_healthy(self) -> bool:
        return self._consecutive_failures < 3

    @property
    def last_sent_at(self) -> float:
        return self._last_sent_at
```

**Key behaviors:**
- POST to `{hq_base_url}/api/v1/factory/registrations` with current capabilities
- Include `heartbeat_latency_ms` (round-trip time of the POST itself)
- On failure: log warning, increment `_consecutive_failures`, backoff (double interval up to 5 min)
- On success: reset `_consecutive_failures`, reset interval to configured value
- Include `metadata.hostname`, `metadata.pid`, `metadata.uptime_seconds`

### 2. Integrate into `MissionConsumer` (from Phase 3a)

The `MissionConsumer._send_heartbeat()` method should delegate to `FactoryHeartbeat.send_sync()`. The heartbeat is interleaved with the mission polling loop (same pattern as tutorial worker).

## Files to Modify

### `src/universal_agent/gateway_server.py`

1. **Persistent registration store:** Replace in-memory `_factory_registrations` dict with SQLite-backed storage (use existing `_runtime_db` or a dedicated `factory_registry.db`).

```python
# New table schema:
# factory_registrations (
#   factory_id TEXT PRIMARY KEY,
#   factory_role TEXT,
#   deployment_profile TEXT,
#   source TEXT,
#   registration_status TEXT DEFAULT 'online',
#   heartbeat_latency_ms REAL,
#   capabilities TEXT,  -- JSON array
#   metadata TEXT,      -- JSON object
#   first_seen_at TEXT,
#   last_seen_at TEXT,
#   updated_at TEXT
# )
```

2. **Stale detection enforcement:** Add a background task or middleware that marks registrations as `stale` when `last_seen_at > now - 5 minutes`, and `offline` when `last_seen_at > now - 15 minutes`.

3. **HQ self-heartbeat:** Add a periodic self-registration refresh in the gateway lifespan (HQ should heartbeat itself too, so Corporation View shows HQ as alive).

### `web-ui/app/dashboard/corporation/page.tsx`

- Add a "Last Heartbeat" column showing time-since-last-heartbeat with color coding:
  - Green: < 2 min
  - Yellow: 2-5 min  
  - Red: > 5 min
- Show `heartbeat_latency_ms` inline

## Tests to Create

### `tests/delegation/test_heartbeat.py`

```python
# Test cases:
# 1. HeartbeatConfig validates required fields
# 2. send_sync() POSTs correct payload to HQ
# 3. Consecutive failures trigger backoff
# 4. Success resets consecutive failures and interval
# 5. is_healthy returns False after 3 consecutive failures
# 6. should_send() respects interval_seconds
```

### `tests/gateway/test_factory_registry_persistence.py`

```python
# Test cases:
# 1. Registration persists across gateway restart (if SQLite-backed)
# 2. Stale detection marks registrations correctly
# 3. Offline detection after 15 minutes of silence
# 4. Re-registration updates existing record (upsert by factory_id)
```

## Validation Commands

```bash
# Unit tests
uv run pytest tests/delegation/test_heartbeat.py -q
uv run pytest tests/gateway/test_factory_registry_persistence.py -q

# Live: start consumer → verify heartbeat appears in Corporation View
# POST /api/v1/factory/registrations should show updated last_seen_at
```

## Acceptance Criteria

- [ ] `FactoryHeartbeat` class exists and sends periodic registration POSTs
- [ ] `MissionConsumer` integrates heartbeat (interleaved with polling)
- [ ] Gateway HQ sends self-heartbeat periodically
- [ ] Registrations survive gateway restart (persistent store)
- [ ] Stale/offline detection works with correct thresholds
- [ ] Corporation View reflects live heartbeat freshness
- [ ] Unit tests pass
