# Data Schema Reference

## run_checkpoint.json

Session-level execution summary. One per workspace directory.

```json
{
  "session_id": "session_20260420_235331_87c77c3d",
  "timestamp": "2026-04-20T23:56:04.924334+00:00",
  "original_request": "...",
  "completed_tasks": ["..."],
  "artifacts": [{"path": "...", "description": "..."}],
  "tool_call_count": 24,
  "execution_time_seconds": 142.52,
  "goal_satisfaction": {
    "passed": true,
    "stage_status": "completed",
    "terminal": true,
    "observed": {
      "tool_names": ["Read", "Bash", "Write", ...],
      "tool_calls_total": 24,
      "auto_completed_after_delivery": false,
      "task_actions": [],
      "successful_vp_dispatches": []
    },
    "missing": [
      {"requirement": "lifecycle_mutation", "required": 1, "observed": 0, "message": "..."}
    ]
  }
}
```

Key fields for profiling:
- `tool_call_count`: Total tool calls in the session
- `execution_time_seconds`: Total wall-clock time
- `goal_satisfaction.passed`: Whether the session succeeded
- `goal_satisfaction.observed.tool_names`: Ordered list of tool names (countable)
- `goal_satisfaction.missing[]`: Unmet requirements (lifecycle gaps)
- `goal_satisfaction.observed.auto_completed_after_delivery`: Session was auto-completed

## trace.json

Detailed tool call trace with timing. One per workspace directory.

```json
{
  "run_id": "uuid",
  "query": "the original prompt",
  "start_time": "2026-04-21T00:01:52.635802",
  "end_time": "2026-04-21T00:03:22.342197",
  "total_duration_seconds": 89.706,
  "tool_calls": [
    {
      "name": "Bash",
      "id": "call_xxx",
      "time_offset_seconds": 17.559,
      "input_size_bytes": 941,
      "input": { "...": "..." }
    }
  ]
}
```

Key fields for profiling:
- `total_duration_seconds`: Total trace duration
- `tool_calls[].name`: Tool name
- `tool_calls[].time_offset_seconds`: Seconds from trace start to this call
- `tool_calls[].input_size_bytes`: Size of the input payload

**Derived metric**: Individual call duration = `next_call.offset - this_call.offset`
(last call = `total_duration_seconds - this_call.offset`)

## Context Pressure Score

```
pressure_score = tool_call_count * execution_time_seconds / 1000
```

| Score  | Level        | Meaning                              |
|--------|-------------|--------------------------------------|
| < 10   | Low          | Normal session                        |
| 10-50  | Moderate     | Complex but manageable                |
| > 50   | High         | Risk of context pressure / truncation |

## Session Types

| Directory Pattern           | Type    | Description                        |
|-----------------------------|---------|------------------------------------|
| `session_YYYYMMDD_*`        | Interactive | User-initiated chat sessions      |
| `run_daemon_simone_*`        | Daemon  | Automated Simone TODO/heartbeat runs |
| `run_session_hook_yt_*`      | Hook    | YouTube webhook triggers            |
| `run_session_hook_agentmail_*`| Hook   | Email webhook triggers              |
| `cron_*`                     | Cron    | Scheduled cron job runs             |
| `_daemon_archives/*`         | Archived| Completed daemon runs moved here    |
