# 23. Agent Workspace Inspector Skill (Read-Only)

Date: 2026-02-11

## What this adds
The internal tool `inspect_session_workspace` lets the agent inspect its own session workspace safely, without write/delete access.

It is designed for debugging and run-review use cases:
- Read tail of `run.log`
- Read tail of `activity_journal.log`
- Preview `trace.json`
- Preview `heartbeat_state.json`
- Include `transcript.md` (enabled by default)
- List recent files in `work_products/` and `tasks/`

## Why this is safe
This tool is read-only and path-scoped.

Protection rules:
- If `session_id` is provided, it must match a safe ID format (no `../` traversal).
- Session lookup is constrained to `UA_WORKSPACES_DIR` (or repo default `AGENT_RUN_WORKSPACES`).
- If no `session_id` is provided, it can inspect the active workspace from `CURRENT_SESSION_WORKSPACE`.
- Output is size-limited (tail lines, bytes per file, recent file limit) to prevent runaway payloads.

## Default behavior
Call with no arguments:
- Uses active workspace (`CURRENT_SESSION_WORKSPACE`) when available.
- Includes `transcript.md` by default.

Call with `session_id`:
- Inspects that exact workspace under `UA_WORKSPACES_DIR`.

## Tool inputs
- `session_id` (optional string)
- `include_transcript` (optional bool, default `true`)
- `tail_lines` (optional int, default `120`, clamped)
- `max_bytes_per_file` (optional int, default `65536`, clamped)
- `recent_file_limit` (optional int, default `25`, clamped)

## Local vs VPS behavior
There is one code path, not two implementations.

Only environment differs:
- Local development typically uses local `UA_WORKSPACES_DIR` or repo `AGENT_RUN_WORKSPACES`.
- VPS runtime typically uses VPS `UA_WORKSPACES_DIR` (or repo default under `/opt/universal_agent`).

Because the logic is env-driven, behavior is consistent across both environments.

## Example call intent
- "Inspect this session and include transcript for debugging"
- `inspect_session_workspace({session_id: 'session_20260211_231748_2b12a9df', include_transcript: true})`

## Notes for operators
- This tool does not sync files between VPS and local machine.
- Use the VPS sync runbook for transfer workflows:
  - `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/22_VPS_Remote_Dev_Deploy_And_File_Transfer_Runbook_2026-02-11.md`
