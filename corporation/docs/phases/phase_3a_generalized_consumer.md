# Phase 3a: Generalized Mission Consumer

**Status:** Not Started
**Priority:** Critical Path — unlocks all downstream phases
**Depends on:** Phase 2 (complete), Redis bus (deployed)

---

## Objective

Replace the bespoke tutorial bootstrap worker with a generalized mission consumer that can receive **any** delegation mission type from the Redis bus, route it to the appropriate handler, execute it, and return results. This consumer becomes the core runtime loop for every LOCAL_WORKER factory.

## Reference Implementation

The existing tutorial worker (`scripts/tutorial_local_bootstrap_worker.py`) is the reference pattern. Key patterns to preserve:
- Redis consumer group polling via `RedisMissionBus.consume()`
- Mission kind routing via `context.mission_kind`
- Retry/DLQ escalation via `fail_and_maybe_dlq()` + `_republish_retry_mission()`
- Registration heartbeat loop interleaved with mission polling
- Graceful shutdown on `KeyboardInterrupt`
- `--once` mode for testing

## Files to Create

### 1. `src/universal_agent/delegation/consumer.py` — Core Consumer Loop

```python
# Key classes and functions to implement:

class MissionHandler(Protocol):
    """Protocol for mission handlers."""
    mission_kind: str
    async def handle(self, envelope: MissionEnvelope, context: ConsumerContext) -> MissionResult: ...

@dataclass
class MissionResult:
    status: Literal["SUCCESS", "FAILED"]
    result: Any = None
    error: str | None = None

@dataclass
class ConsumerContext:
    factory_id: str
    factory_role: str
    worker_id: str
    workspace_dir: Path
    ops_token: str
    hq_base_url: str

class MissionConsumer:
    """Generalized mission consumer that polls Redis and dispatches to handlers."""
    
    def __init__(
        self,
        bus: RedisMissionBus,
        consumer_name: str,
        handlers: dict[str, MissionHandler],
        context: ConsumerContext,
        *,
        poll_seconds: float = 5.0,
        registration_interval_seconds: float = 60.0,
    ) -> None: ...
    
    def register_handler(self, handler: MissionHandler) -> None: ...
    
    async def run(self, *, once: bool = False) -> int:
        """Main consumer loop. Returns processed count."""
        ...
    
    async def _process_mission(self, consumed: ConsumedMission) -> MissionResult:
        """Dispatch to handler, handle retries/DLQ."""
        ...
    
    def _send_heartbeat(self) -> None:
        """POST registration to HQ factory/registrations endpoint."""
        ...
```

**Routing logic:**
- Extract `mission_kind` from `envelope.payload.context.get("mission_kind")`
- Look up handler in `self.handlers[mission_kind]`
- If no handler found, ack the message and log a warning (don't DLQ unknown kinds — they may be for other consumers)
- Execute handler, publish `MissionResultEnvelope` to results stream
- On failure: retry/DLQ using existing `RedisMissionBus` patterns

### 2. `src/universal_agent/delegation/handlers/__init__.py`

Empty init to establish the handlers sub-package.

### 3. `src/universal_agent/delegation/handlers/tutorial_bootstrap.py`

Refactor the core `_process_job()` logic from `scripts/tutorial_local_bootstrap_worker.py` into a `MissionHandler` implementation:

```python
class TutorialBootstrapHandler:
    mission_kind = "tutorial_bootstrap_repo"
    
    def __init__(self, target_root: str = "/home/kjdragan/YoutubeCodeExamples") -> None: ...
    
    async def handle(self, envelope: MissionEnvelope, context: ConsumerContext) -> MissionResult:
        # Extract job params from envelope.payload.context
        # Download bundle from HQ
        # Execute create_new_repo.sh
        # Report result back to HQ
        ...
```

### 4. `src/universal_agent/delegation/handlers/coding_task.py`

Stub handler for future VP Coder delegation:

```python
class CodingTaskHandler:
    mission_kind = "coding_task"
    
    async def handle(self, envelope: MissionEnvelope, context: ConsumerContext) -> MissionResult:
        # Future: delegate to local VP Coder agent
        return MissionResult(status="FAILED", error="coding_task handler not yet implemented")
```

### 5. `src/universal_agent/delegation/handlers/system_update.py`

Stub handler for Phase 3d self-update:

```python
class SystemUpdateHandler:
    mission_kind = "system:update_factory"
    
    async def handle(self, envelope: MissionEnvelope, context: ConsumerContext) -> MissionResult:
        # Future: git pull, uv install, restart
        return MissionResult(status="FAILED", error="system:update_factory not yet implemented")
```

### 6. Entry point: `src/universal_agent/delegation/__main__.py`

```python
"""Run the generalized mission consumer as a standalone process."""
# python -m universal_agent.delegation
# Parses args, builds handlers, creates MissionConsumer, runs loop
```

### 7. Refactor `scripts/tutorial_local_bootstrap_worker.py`

After the generalized consumer works, refactor the tutorial worker to be a thin wrapper:
```python
# scripts/tutorial_local_bootstrap_worker.py
# Now just: instantiate MissionConsumer with TutorialBootstrapHandler + args, run
```

## Files to Modify

### `src/universal_agent/delegation/schema.py`
- Add `mission_kind` as an optional top-level field on `MissionEnvelope` (currently buried in `payload.context`)
- Keep backward compat: if `mission_kind` not set at top level, fall back to `payload.context.get("mission_kind")`

### `src/universal_agent/delegation/__init__.py`
- Export `MissionConsumer`, `MissionHandler`, `MissionResult`, `ConsumerContext`

## Tests to Create

### `tests/delegation/test_consumer.py`

```python
# Test cases:
# 1. Consumer dispatches to correct handler based on mission_kind
# 2. Unknown mission_kind is acked and logged (not DLQ'd)
# 3. Handler failure triggers retry republish
# 4. Handler failure exceeding max_retries sends to DLQ
# 5. Consumer --once mode processes exactly one mission and exits
# 6. Registration heartbeat fires at configured interval
# 7. Graceful shutdown on signal
```

### `tests/delegation/test_tutorial_bootstrap_handler.py`

```python
# Test cases:
# 1. Handler extracts job params from envelope correctly
# 2. Handler returns SUCCESS on successful execution
# 3. Handler returns FAILED with error on script failure
# 4. Handler respects timeout_seconds
```

## Validation Commands

```bash
# Unit tests
uv run pytest tests/delegation/test_consumer.py -q
uv run pytest tests/delegation/test_tutorial_bootstrap_handler.py -q

# Integration: standalone consumer with --once
FACTORY_ROLE=LOCAL_WORKER UA_DELEGATION_REDIS_ENABLED=1 \
  python -m universal_agent.delegation --once --transport redis

# End-to-end: HQ publishes → consumer picks up → result back
# (requires Redis running + HQ gateway)
```

## Acceptance Criteria

- [ ] `MissionConsumer` class exists and routes missions by `mission_kind`
- [ ] `TutorialBootstrapHandler` passes existing tutorial bootstrap e2e flow
- [ ] Consumer runs as `python -m universal_agent.delegation`
- [ ] Unit tests pass: `uv run pytest tests/delegation/ -q`
- [ ] Consumer registers with HQ on startup and sends periodic heartbeats
- [ ] DLQ escalation works for missions exceeding `max_retries`
- [ ] `--once` mode works for scripted testing
- [ ] Existing tutorial worker still works (backward compat or refactored to use consumer)

## Design Decisions

- **Async handlers:** Use `async def handle()` to allow future handlers to run async agent sessions.
- **Handler registry:** Simple `dict[str, MissionHandler]` — no dynamic discovery needed at this scale.
- **Unknown mission kinds:** Ack and skip (not DLQ) — another consumer in the group may handle different kinds.
- **Result publishing:** Consumer publishes `MissionResultEnvelope` to `ua:missions:delegation:results` stream (already supported by `RedisMissionBus.publish_result()`).
