# Mission Envelope v1 (Factory Communication Contract)

Defines the minimum interoperable payload and lifecycle events for Simone control plane to dispatch work to cloned UA factory instances.

## 1) Dispatch payload schema

```json
{
  "mission_id": "string (globally unique)",
  "source_instance_id": "string",
  "target_instance_id": "string",
  "mission_type": "coding|research|automation|custom",
  "objective": "string",
  "constraints": {
    "allowed_tools": ["string"],
    "forbidden_tools": ["string"],
    "approval_required": ["string"]
  },
  "budget": {
    "max_runtime_minutes": 0,
    "max_llm_calls": 0,
    "max_cost_usd": 0
  },
  "autonomy_window": {
    "start_at": "ISO-8601",
    "end_at": "ISO-8601"
  },
  "heartbeat_interval_seconds": 300,
  "report_cadence": "milestone|interval|hybrid",
  "success_criteria": ["string"],
  "artifact_policy": {
    "required_outputs": ["string"],
    "storage_scope": "factory|shared|pushback"
  },
  "callback": {
    "status_url": "string",
    "auth_mode": "bearer",
    "auth_ref": "env://UA_INTERNAL_API_TOKEN"
  },
  "idempotency_key": "string",
  "trace_id": "string",
  "created_at": "ISO-8601"
}
```

## 2) Lifecycle events

Required events from factory -> Simone:

1. `mission.accepted`
2. `mission.progress`
3. `mission.blocked`
4. `mission.artifact`
5. `mission.completed`
6. `mission.failed`
7. `mission.cancelled`
8. `mission.timed_out`

## 3) Event payload minimum

```json
{
  "mission_id": "string",
  "event_type": "string",
  "status": "queued|running|blocked|completed|failed|cancelled|timed_out",
  "summary": "string",
  "artifact_refs": ["string"],
  "usage": {
    "runtime_minutes": 0,
    "llm_calls": 0,
    "estimated_cost_usd": 0
  },
  "trace_id": "string",
  "idempotency_key": "string",
  "emitted_at": "ISO-8601"
}
```

## 4) Reliability requirements

1. **At-least-once delivery** with idempotent receivers.
2. **Idempotency key required** for every dispatch and callback.
3. **Timeout + retry policy** must avoid infinite loops.
4. **Final state required** (`completed|failed|cancelled|timed_out`) for mission closure.

## 5) Safety requirements

1. Factory must honor `approval_required` controls from mission constraints.
2. Factory must stop on kill/cancel command from Simone.
3. Factory must emit `mission.blocked` when required input/approval is missing.

## 6) Observability requirements

1. Every mission and event must include `mission_id` + `trace_id`.
2. Progress events should include concise human-readable summaries.
3. Final event should include artifact index and next-action recommendation.
