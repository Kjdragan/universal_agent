# Phase 3a: Redis→SQLite Bridge Adapter

**Status:** Done (implemented 2026-03-06)
**Priority:** Critical Path — unlocks cross-machine delegation
**Depends on:** Phase 2 (complete), Redis bus (deployed), VP worker system (complete — Track B)

---

## Context: Why This Was Reframed

The original Phase 3a spec called for building a "generalized mission consumer" from scratch. A **2026-03-06 audit** discovered that the VP external worker system (`src/universal_agent/vp/`) — built as Track B HQ improvements — already provides a complete local mission consumer with:
- `VpWorkerLoop` — polls SQLite `vp_missions`, claims with leases, executes, heartbeats, finalizes
- `ClaudeCodeClient` (CODIE) + `ClaudeGeneralistClient` — execute missions via `ProcessTurnAdapter`
- Full mission lifecycle in `durable/state.py` — queue/claim/heartbeat/finalize/events

What's **missing** is a bridge between the cross-machine transport (Redis Streams) and the local execution engine (VP SQLite). Phase 3a is therefore reframed to building this thin bridge only.

**Architecture Decision (D-006):** Option B — Redis→SQLite bridge. Redis Streams for cross-machine, local VP SQLite for execution.

## Objective

Build a thin bridge adapter that consumes `MissionEnvelope` messages from the Redis bus and inserts them into the local VP SQLite `vp_missions` table, where the existing `VpWorkerLoop` picks them up and executes them. Results are bridged back from VP SQLite to a Redis results stream.

## Reference Implementations

**Redis consuming pattern** — tutorial worker (`scripts/tutorial_local_bootstrap_worker.py`):
- Redis consumer group polling via `RedisMissionBus.consume()`
- Retry/DLQ escalation via `fail_and_maybe_dlq()` + `_republish_retry_mission()`
- Graceful shutdown

**VP mission insertion pattern** — gateway VP dispatch (`src/universal_agent/gateway.py`):
- `_dispatch_external_vp_mission()` — inserts into SQLite via `dispatch_mission_with_retry()`
- Maps mission metadata to VP fields (constraints, budget, priority, idempotency_key)

**VP mission execution** — worker loop (`src/universal_agent/vp/worker_loop.py`):
- `VpWorkerLoop.run_forever()` — polls SQLite, claims, executes, finalizes
- Already handles heartbeats, lease management, error recovery

## Files to Create

### 1. `src/universal_agent/delegation/redis_vp_bridge.py` — Core Bridge

```python
# Key classes and functions to implement:

@dataclass
class BridgeConfig:
    """Configuration for the Redis→VP SQLite bridge."""
    poll_seconds: float = 5.0
    vp_id_map: dict[str, str]  # mission_kind → vp_id
    default_vp_id: str = "vp.general.primary"
    workspace_base: Path = Path("/opt/universal_agent/vp_workspaces")

MISSION_KIND_TO_VP: dict[str, str] = {
    "coding_task": "vp.coder.primary",
    "general_task": "vp.general.primary",
    "research_task": "vp.general.primary",
    # tutorial_bootstrap_repo still handled by tutorial worker directly
}

class RedisVpBridge:
    """Thin bridge: consumes Redis missions → inserts into VP SQLite."""
    
    def __init__(
        self,
        bus: RedisMissionBus,
        vp_db_conn: sqlite3.Connection,
        config: BridgeConfig,
    ) -> None: ...
    
    async def run(self, *, once: bool = False) -> int:
        """Main bridge loop. Polls Redis, inserts into VP SQLite."""
        # 1. RedisMissionBus.consume() → get MissionEnvelope
        # 2. Map mission_kind → vp_id via MISSION_KIND_TO_VP
        # 3. queue_vp_mission() into local SQLite
        # 4. Ack the Redis message
        # 5. On failure: retry/DLQ via RedisMissionBus patterns
        ...
    
    def _envelope_to_vp_mission(self, envelope: MissionEnvelope) -> MissionDispatchRequest:
        """Transform Redis MissionEnvelope → VP mission dispatch request."""
        # Maps: envelope.payload.task → mission_type
        #       envelope.payload.context → constraints/budget/metadata
        #       envelope.job_id → idempotency_key
        ...
```

**Routing logic:**
- Extract `mission_kind` from `envelope.payload.context.get("mission_kind")` or `envelope.payload.task` prefix
- Map to VP ID via `MISSION_KIND_TO_VP` (CODIE for coding, Generalist for everything else)
- If `mission_kind` is `tutorial_bootstrap_repo`, skip (handled by existing tutorial worker)
- Insert into VP SQLite via `queue_vp_mission()` from `durable/state.py`
- `VpWorkerLoop` (already running) will claim and execute

### 2. `src/universal_agent/delegation/redis_vp_result_bridge.py` — Result Bridge

```python
class RedisVpResultBridge:
    """Monitors VP mission finalization → publishes results back to Redis."""
    
    def __init__(
        self,
        bus: RedisMissionBus,
        vp_db_conn: sqlite3.Connection,
        poll_seconds: float = 5.0,
    ) -> None: ...
    
    async def run(self) -> None:
        # Poll vp_missions for status IN ('completed', 'failed')
        # WHERE source = 'redis_bridge' AND result_published = 0
        # Transform to MissionResultEnvelope
        # Publish to Redis results stream
        # Mark result_published = 1
        ...
```

### 3. Entry point: `src/universal_agent/delegation/bridge_main.py`

```python
"""Run the Redis→VP SQLite bridge as a standalone process."""
# python -m universal_agent.delegation.bridge_main
# Or integrated as background task in LOCAL_WORKER gateway
```

## Files to Modify

### `src/universal_agent/delegation/schema.py`
- Add `mission_kind` as an optional top-level field on `MissionEnvelope` (backward compat: fall back to `payload.context.get("mission_kind")`)

### `src/universal_agent/durable/state.py`
- Add `source` column to `vp_missions` (value: `"redis_bridge"` or `"gateway"`) to distinguish bridge-inserted missions
- Add `result_published` flag for result bridge tracking

### `src/universal_agent/delegation/__init__.py`
- Export `RedisVpBridge`, `RedisVpResultBridge`, `BridgeConfig`

## Tests to Create

### `tests/delegation/test_redis_vp_bridge.py`

```python
# Test cases:
# 1. Bridge transforms MissionEnvelope → queue_vp_mission() call correctly
# 2. Mission kind routes to correct VP ID (coding_task → vp.coder.primary)
# 3. Unknown mission_kind defaults to vp.general.primary
# 4. tutorial_bootstrap_repo kind is skipped (handled by tutorial worker)
# 5. Redis message is acked after successful VP SQLite insertion
# 6. Redis message retry/DLQ on VP SQLite insertion failure
# 7. Bridge --once mode processes exactly one mission and exits
```

### `tests/delegation/test_redis_vp_result_bridge.py`

```python
# Test cases:
# 1. Completed VP mission → MissionResultEnvelope published to Redis
# 2. Failed VP mission → MissionResultEnvelope with error published
# 3. result_published flag prevents double-publish
# 4. Only redis_bridge-sourced missions are published (not gateway-local ones)
```

## Validation Commands

```bash
# Unit tests
uv run pytest tests/delegation/test_redis_vp_bridge.py -q
uv run pytest tests/delegation/test_redis_vp_result_bridge.py -q

# Integration: standalone bridge with --once
FACTORY_ROLE=LOCAL_WORKER UA_DELEGATION_REDIS_ENABLED=1 \
  python -m universal_agent.delegation.bridge_main --once

# End-to-end: HQ publishes → Redis → bridge → VP SQLite → VpWorkerLoop → result back on Redis
# (requires Redis running + HQ gateway + VpWorkerLoop running)
```

## Acceptance Criteria

- [x] `RedisVpBridge` class consumes Redis missions and inserts into VP SQLite
- [x] Mission kind routing maps to correct VP ID
- [x] `RedisVpResultBridge` publishes results back to Redis
- [x] Bridge runs as `python -m universal_agent.delegation.bridge_main`
- [x] Unit tests pass: `uv run pytest tests/delegation/ -q` — **20 passed**
- [x] DLQ escalation works for missions that fail to insert
- [x] `--once` mode works for scripted testing
- [x] Existing tutorial worker still works (unchanged — handles `tutorial_bootstrap_repo` directly)
- [ ] VP worker system picks up bridge-inserted missions and executes them (requires live integration test)

## Design Decisions

- **Bridge, not replace:** The bridge inserts into existing VP SQLite — no new execution engine needed.
- **Two bridges:** Inbound (Redis → SQLite) and outbound (SQLite → Redis results) are separate concerns.
- **Mission kind skip:** `tutorial_bootstrap_repo` is skipped by the bridge (existing tutorial worker handles it via its own Redis consumer in the same consumer group).
- **Source tracking:** `vp_missions.source = 'redis_bridge'` distinguishes bridge-inserted missions from gateway-local ones, so the result bridge only publishes results for cross-machine missions.
- **VpWorkerLoop unchanged:** The worker loop doesn't know or care where missions came from — it just polls SQLite.
