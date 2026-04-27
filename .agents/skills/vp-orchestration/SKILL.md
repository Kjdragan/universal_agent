---
name: vp-orchestration
description: >
  Operate external primary VP agents through tool-first mission control (`vp_*` tools)
  with deterministic lifecycle handling and artifact handoff.
  USE when work should be delegated to an external VP runtime — such as when the user
  says "send this to the VP", "have the coder VP do this", "run this as a VP mission",
  "delegate to general VP", "kick off a VP task", or "have the external agent handle this".
  VP runtimes available: `vp.general.primary` (research/content/analysis) and
  `vp.coder.primary` (code/build/refactor in external project paths).
user-invocable: true
risk: medium
---

# VP Orchestration Skill

Use this skill whenever work should be delegated to external primary VP runtimes.

> **Critical guardrail:** When explicit VP intent is detected, the **first tool call in that turn
> MUST be `vp_dispatch_mission`**. Do not do discovery, search, or preflight checks first — call
> `vp_dispatch_mission` directly. This is enforced by the runtime; non-compliant turns are blocked.

---

## VP ID Selection

| VP Runtime | Use when |
|-----------|----------|
| `vp.general.primary` | Research, analysis, content, writing, summarization — tasks that do NOT require editing external repository files |
| `vp.coder.primary` | Coding, builds, refactors, scaffolding — tasks that need read/write access to external project paths |

Rules:

- Include budget and constraints when they are known.
- Provide an `idempotency_key` (stable string derived from the request) to make retries safe.
- Do not target UA internal repository paths for coder VP missions (see CODIE Guardrails below).

---

## Standard Lifecycle

```
vp_dispatch_mission  →  vp_wait_mission  →  vp_get_mission  →  vp_read_result_artifacts
```

1. **Dispatch** — call `vp_dispatch_mission` with `vp_id`, `objective`, and optional params.
   Immediately report the `mission_id` and `queued` status to the user.
2. **Wait** — call `vp_wait_mission` with a bounded `timeout_seconds` for short tasks.
   Prefer this over manual polling loops.
3. **Get final state** — on terminal status, call `vp_get_mission` for the full state and
   any `failure_detail` from events.
4. **Read artifacts** — if mission completed with `result_ref=workspace://...`, call
   `vp_read_result_artifacts` to get the file index + excerpts.

---

## Tool Reference

### `vp_dispatch_mission`

Dispatch an external VP mission through the internal VP ledger.

| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| `vp_id` | string | ✅ | `"vp.general.primary"` or `"vp.coder.primary"` |
| `objective` | string | ✅ | Full mission prompt/description |
| `mission_type` | string | | Default: `"task"` |
| `constraints` | object | | e.g. `{"max_tokens": 8000}` |
| `budget` | object | | e.g. `{"max_cost_usd": 1.0}` |
| `idempotency_key` | string | | Stable key for replay-safe dispatch; auto-generated if omitted |
| `priority` | int | | Default: `100` (lower = higher priority) |
| `reply_mode` | string | | Default: `"async"` |

Returns: `{ ok, mission_id, status, vp_id, queued_at, mission }`

### `vp_wait_mission`

Block and poll until mission reaches a terminal state or timeout.

| Parameter | Type | Notes |
|-----------|------|-------|
| `mission_id` | string | Required |
| `timeout_seconds` | int | Max 3600; default 300 |
| `poll_seconds` | int | 1–30; default 3 |

Returns: `{ ok, timed_out, mission }` — check `timed_out` before reading `mission.status`

### `vp_get_mission`

Get full mission state + event history (including `failure_detail`).

| Parameter | Type | Notes |
|-----------|------|-------|
| `mission_id` | string | Required |

Returns: `{ ok, mission, terminal, failure_detail, events }`

### `vp_list_missions`

List missions by vp_id and/or status.

| Parameter | Type | Notes |
|-----------|------|-------|
| `vp_id` | string | Optional filter |
| `status` | string | `"all"`, or comma-separated: `"queued,running"` |
| `limit` | int | 1–500; default 50 |

Returns: `{ ok, count, missions }`

### `vp_cancel_mission`

Request cancellation for a queued or running mission.

| Parameter | Type | Notes |
|-----------|------|-------|
| `mission_id` | string | Required |
| `reason` | string | Default: `"cancel_requested"` |

Returns: `{ ok, status: "cancel_requested", mission }`

### `vp_read_result_artifacts`

Read artifact files from a mission's `workspace://` result location.

| Parameter | Type | Notes |
|-----------|------|-------|
| `mission_id` | string | Required |
| `max_files` | int | 1–200; default 20 |
| `max_bytes` | int | 256–2,000,000; default 200,000 |

Returns: `{ ok, result_ref, workspace_root, files_indexed, files_total, artifacts }` where each artifact has `{ path, bytes, excerpt, excerpt_truncated }`

---

## Error Codes

| Code | Retryable | Meaning |
|------|-----------|---------|
| `validation_error` | No | Missing or invalid required parameter |
| `vp_db_locked` | ✅ Yes | SQLite lock contention — retry after short delay (~1s) |
| `dispatch_failed` | Depends | Generic dispatch error — check message |
| `not_found` | No | Mission ID does not exist |
| `cancel_failed` | No | Mission not found or not in a cancellable state |
| `artifact_location_unavailable` | No | `result_ref` is not a `workspace://` URI |
| `artifact_workspace_missing` | No | `result_ref` path does not exist on disk |
| `artifact_read_failed` | No | Generic artifact read error |

---

## Poll/Wait Policy

- **Always prefer `vp_wait_mission`** over building manual polling loops.
- Use `poll_seconds=2` to `5` and an explicit `timeout_seconds` that fits the expected task duration.
- If `timed_out=true`, report current state and give the user a next checkpoint time.
- Do not loop indefinitely — if timeout is hit twice, surface the situation to the user.

---

## Failure and Recovery

1. If dispatch returns `vp_db_locked` (`retryable=true`): wait ~1s and retry once.
2. If mission reaches `failed` status: call `vp_get_mission` and surface `failure_detail` before proposing a retry.
3. If mission is no longer needed: call `vp_cancel_mission` with a descriptive reason.
4. Never silently swallow `ok=false` — always surface the `error.code` and `error.message`.

---

## Artifact Handoff

- `result_ref=workspace://...` is the authoritative artifact location written by the VP worker.
- Use `vp_read_result_artifacts` for the artifact index + file excerpts.
- Summarize what was produced, where it lives, and what actions remain.
- Do not copy workspace files into UA repo paths without explicit user request.

---

## Constraint Keys Reference

When dispatching missions with `constraints`, use ONLY the documented keys below.
Unrecognized keys are logged as warnings and **silently ignored** by the VP worker.

### Path Constraints (where the VP works)

| Key | Use |
|-----|-----|
| `target_path` | **CANONICAL** — always prefer this key for specifying where CODIE should work |
| `path` | Alias for `target_path` |
| `repo_path` | Alias — use when referencing an existing git repository |
| `workspace_dir` | Alias — use when referencing a workspace directory |
| `project_path` | Alias — use when referencing a project root |

> **CAUTION:** Keys like `output_path`, `working_directory`, `dest_path` are now accepted
> as aliases but are **not standard**. Always prefer `target_path` as the canonical key.

### Task Constraints

| Key | Type | Notes |
|-----|------|-------|
| `tech_stack` | string | Hint for technology choices |
| `max_duration_minutes` | int | Soft timeout hint |
| `required_env_var` | string | Environment variable the task needs |
| `max_tokens` | int | Token budget hint |
| `repo_mutation_allowed` | bool | Enable mutations in approved codebase paths |

---

## New Project Scaffolding

When creating a new standalone repository or project, the mission objective MUST include:

1. A `target_path` constraint pointing to the desired project directory (e.g., `/home/kjdragan/lrepos/my-new-project`)
2. Instructions to initialize with `git init` and create a proper `pyproject.toml`
3. Instructions to use `uv` for dependency management (not pip)
4. Instructions to use Infisical for secrets (not `.env` files or `os.environ.get()`)

Example dispatch for new project creation:

```json
{
  "vp_id": "vp.coder.primary",
  "objective": "Create a new Python project at the target path with...",
  "constraints": {
    "target_path": "/home/kjdragan/lrepos/my-new-project",
    "tech_stack": "Python with google-genai SDK"
  }
}
```

---

## CODIE Guardrails

- Do **not** target UA internal repository or runtime paths for CODIE (coder VP) missions.
- Use only allowlisted external handoff/workspace paths for coder VP code execution.
- Never pass UA secrets, internal DB paths, or session tokens in the mission objective string.
