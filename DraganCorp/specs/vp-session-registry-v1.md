# VP Session Registry v1

Defines the durable control-plane registry contract for persistent VP runtime sessions in Phase A.

## 1) Purpose

Provide deterministic lookup and lifecycle management for VP runtime identity, session continuity, and mission ownership.

## 2) Scope

Applies to persistent VP runtime lanes (starting with CODER VP) managed by Simone control plane.

## 3) Registry entities

## 3.1 `vp_sessions`

Canonical mapping for VP identity to runtime/session/workspace.

Minimum fields:

- `vp_id` (PK, e.g. `vp.coder.primary`)
- `runtime_id` (service/lane identity)
- `session_id` (provider/runtime session identifier)
- `workspace_dir` (stable workspace path)
- `status` (`idle|active|paused|degraded|recovering|retired`)
- `lease_owner` (control-plane lease holder)
- `lease_expires_at` (ISO-8601)
- `last_heartbeat_at` (ISO-8601)
- `last_error` (text)
- `metadata_json` (optional ext fields)
- `created_at`, `updated_at` (ISO-8601)

## 3.2 `vp_missions`

Tracks mission lifecycle and assignment to VP session.

Minimum fields:

- `mission_id` (PK)
- `vp_id` (FK -> `vp_sessions.vp_id`)
- `run_id` (optional link to runtime run)
- `status` (`queued|running|blocked|completed|failed|cancelled|timed_out`)
- `objective` (text)
- `budget_json` (serialized budget contract)
- `result_ref` (artifact/result pointer)
- `created_at`, `started_at`, `completed_at`, `updated_at`

## 3.3 `vp_events`

Immutable mission/session event stream.

Minimum fields:

- `event_id` (PK)
- `mission_id` (FK -> `vp_missions.mission_id`)
- `vp_id`
- `event_type`
- `payload_json`
- `created_at`

## 4) Required operations

1. `upsert_vp_session(...)`
2. `get_vp_session(vp_id)`
3. `list_vp_sessions(statuses=None)`
4. `acquire_vp_session_lease(vp_id, owner, ttl_seconds)`
5. `heartbeat_vp_session_lease(vp_id, owner, ttl_seconds)`
6. `release_vp_session_lease(vp_id, owner)`
7. `update_vp_session_status(vp_id, status, last_error=None)`
8. `upsert_vp_mission(...)`
9. `append_vp_event(...)`
10. `list_vp_missions(vp_id, statuses=None, limit=...)`

## 5) Invariants

1. One active row per `vp_id` in `vp_sessions`.
2. Every mission row must reference an existing `vp_id`.
3. Every event row must reference an existing mission.
4. `lease_owner` updates require owner match for heartbeat/release.
5. `vp_id + session_id` continuity must remain stable unless explicit recover/reset action occurs.

## 6) Recovery semantics

1. If lease expires, mark session `degraded`.
2. Recovery flow may allocate new `session_id`; when it does, write recovery event and update `updated_at`.
3. Mission ownership remains tied to `vp_id`; session replacement must preserve mission traceability.

## 7) Observability requirements

Emit at least:

- `vp.session.created`
- `vp.session.resumed`
- `vp.session.degraded`
- `vp.session.recovered`
- `vp.mission.dispatched`
- `vp.mission.completed`
- `vp.mission.failed`
- `vp.mission.fallback`

## 8) Security and governance

1. Only Simone/control-plane services may mutate registry records.
2. All mutation paths require authenticated internal channel.
3. Sensitive payloads in `metadata_json`/`payload_json` must avoid secrets.
