# VP Mission Status Lifecycle

All VP missions move through these states in order:

```
queued  →  running  →  completed
                  ↘  failed
                  ↘  cancelled
```

**Terminal states** (vp_wait_mission stops polling): `completed`, `failed`, `cancelled`

---

## State Descriptions

| Status | Meaning |
|--------|---------|
| `queued` | Mission written to VP ledger; worker not yet picked it up |
| `running` | Worker has claimed and started the mission |
| `completed` | Worker finished successfully; `result_ref` contains output location |
| `failed` | Worker encountered an unrecoverable error; see `failure_detail` in `vp_get_mission` |
| `cancelled` | Cancellation was requested and accepted |
| `cancel_requested` | Cancellation written; not yet acknowledged by worker |

---

## `vp_get_mission` Response Shape

```json
{
  "ok": true,
  "terminal": true,
  "failure_detail": "string or null",
  "mission": {
    "mission_id": "uuid",
    "vp_id": "vp.general.primary",
    "status": "completed",
    "objective": "...",
    "mission_type": "task",
    "priority": 100,
    "idempotency_key": "...",
    "reply_mode": "async",
    "result_ref": "workspace:///opt/universal_agent/vp_handoff/session_abc123",
    "created_at": "2026-03-04T18:00:00+00:00",
    "started_at": "2026-03-04T18:00:02+00:00",
    "completed_at": "2026-03-04T18:03:45+00:00",
    "duration_seconds": 223.0
  },
  "events": [...]
}
```

---

## `vp_read_result_artifacts` Response Shape

```json
{
  "ok": true,
  "mission_id": "uuid",
  "result_ref": "workspace://...",
  "workspace_root": "/opt/universal_agent/vp_handoff/...",
  "files_indexed": 5,
  "files_total": 5,
  "artifacts": [
    {
      "path": "README.md",
      "bytes": 1234,
      "excerpt": "# Summary\n...",
      "excerpt_truncated": false
    }
  ]
}
```

Supported text extensions for excerpt reading: `.md`, `.txt`, `.json`, `.yaml`, `.yml`, `.csv`, `.log`, `.py`, `.ts`, `.js`, `.tsx`, `.jsx`, `.toml`, `.ini`, `.cfg`, `.html`, `.css`, `.xml`, `.sql`

---

## Common Work Patterns

### Research mission (vp.general.primary)

```
vp_dispatch_mission(vp_id="vp.general.primary", objective="...", mission_type="task")
  → report mission_id to user
vp_wait_mission(mission_id=..., timeout_seconds=600, poll_seconds=5)
  → if timed_out=false: vp_get_mission → vp_read_result_artifacts
  → if timed_out=true: report state, give checkpoint time
```

### Coder mission (vp.coder.primary)

```
vp_dispatch_mission(vp_id="vp.coder.primary", objective="...", constraints={"workspace": "/opt/projects/..."})
  → report mission_id to user
vp_wait_mission(mission_id=..., timeout_seconds=1800, poll_seconds=10)
  → if completed: vp_read_result_artifacts to index output
  → if failed: vp_get_mission to read failure_detail → surface to user
```

### Retry on lock contention

```
vp_dispatch_mission(...)
  → if error.code == "vp_db_locked" (retryable=true):
      wait 1 second
      vp_dispatch_mission(...)  # retry once
```
