# VPS WebUI Long-Running Query Evaluation (2026-02-18)

## 1. Scope

This document records a real execution of a long-running WebUI-style query on VPS, then analyzes:

- session workspaces
- run logs
- transcript output
- generated artifacts
- reliability issues and improvements

Test date (UTC): `2026-02-18`

## 2. Test Prompt (Submitted)

Submitted prompt (verbatim intent): create a long-running capability demo, produce interim work products, send each interim via Gmail, send final via Gmail, then produce a comprehensive evaluation report and save it as an official numbered doc.

## 3. Execution Path

### 3.1 Route used

- API session create: `POST http://127.0.0.1:8002/api/v1/sessions` (on VPS)
- Stream execute: `ws://127.0.0.1:8002/api/v1/sessions/{session_id}/stream`

Notes:

- Public API HTTPS accepted REST, but public websocket path for `/api/v1/sessions/{id}/stream` returned `404` when tested externally.
- Direct VPS route (`127.0.0.1:8002`) was used to run the evaluation end-to-end.

### 3.2 Primary session IDs

- Chat session: `session_20260218_025507_ad6baf7c`
- Delegated coder lane: `vp_coder_primary`
- VP mission id observed: `vp-mission-c6f06bce51eb4838a682c39770f4eb1d`

### 3.3 Raw stream capture

- `/opt/universal_agent/tmp/vps_query_session_20260218_025507_ad6baf7c_20260218T025507Z.jsonl`

Captured early stream events include:

- delegation to CODIE lane
- plan creation with multi-phase tasks
- initial tool calls and directory checks

## 4. Output Inventory

## 4.1 Session workspace outputs (final run context)

Workspace:

- `/opt/universal_agent/AGENT_RUN_WORKSPACES/session_20260218_025507_ad6baf7c`

Key outputs:

- `run.log`
- `transcript.md`
- `trace.json`
- `trace_catalog.md`
- `work_products/media/ai_agent_ecosystem_infographic.png`
- `work_products/logfire-eval/trace_catalog.md`
- `work_products/logfire-eval/trace_catalog.json`
- `subagent_outputs/task:*/subagent_summary.md`
- `subagent_outputs/task:*/subagent_output.json`

## 4.2 Delegated capability demo outputs (vp workspace)

Directory:

- `/opt/universal_agent/AGENT_RUN_WORKSPACES/vp_coder_primary/work_products/capability_demo_20260218_025815`

Generated files:

- `research/01_intelligence_summary.md`
- `data/ai_adoption_trend.png`
- `data/framework_popularity.png`
- `data/capability_radar.png`
- `data/visualization_data_summary.json`
- `media/universal_agent_architecture.mmd`
- `media/universal_agent_architecture.svg`
- `media/ai_agent_ecosystem_2026.mmd`
- `media/ai_agent_ecosystem_2026.svg`
- `media/ai_agent_ecosystem_infographic.png`
- `generate_visualizations.py`

## 4.3 Persistent media artifact

- `/opt/universal_agent/artifacts/media/ai_agent_ecosystem_infographic_20260218_030523.png`

## 5. What Completed vs What Did Not

### Completed

- Long-running orchestration executed with many integrated tools/subagents.
- Artifact production worked (charts, mermaid diagrams, generated infographic, trace catalog).
- Transcript and memory indexing completed.
- Run log shows terminal summary:
  - `Execution complete — 320.596s | 70 tools | code exec`

### Did not complete as requested by the prompt

- No Gmail send actions were observed for interim or final work products.
- Run focus drifted from the original user goal into heartbeat-driven investigation of a prior `"database is locked"` notification.

## 6. Key Log Findings

From `session_20260218_025507_ad6baf7c/run.log` and `vp_coder_primary/run.log`:

- `Exit code 127` with `/bin/bash: uv: command not found`
- `Exit code 127` with `/bin/bash: sqlite3: command not found`
- multiple `<tool_use_error>Sibling tool call errored</tool_use_error>`
- `sqlite3.OperationalError: no such column: session_id` (query/schema mismatch in ad-hoc diagnostic command)
- notification-driven recursive investigation loop for `"database is locked"`
- heartbeat summary flagged `UA_HEARTBEAT_TIMEOUT` with delivery suppressed due no connected targets

## 7. Behavioral/Architecture Issues Observed

1. Prompt drift / priority inversion
- A heartbeat-triggered task overtook the intended user mission in the same session.

2. Run log continuity confusion
- The active `run.log` for `session_20260218_025507_ad6baf7c` starts at heartbeat task context, while early mission state was only preserved in websocket capture and delegated workspace logs.

3. Missing runtime binaries in shell tools
- Agent attempted shell commands requiring `uv` and `sqlite3`, but these were unavailable in the execution PATH/context.

4. Public websocket route mismatch
- External `wss` attach to `/api/v1/sessions/{id}/stream` was not routable in current reverse-proxy path, while direct `127.0.0.1:8002` worked.

5. Mission acceptance criteria not enforced
- Gmail delivery requirements were in prompt, but not enforced as hard completion gates.

## 8. Recommended Fixes (Priority)

P1:

- Add mission guardrails: if user prompt includes required channels (e.g., Gmail), enforce required tool-call checkpoints before declaring complete.
- Isolate heartbeat executions from active user mission sessions (dedicated heartbeat session namespace or no-overlap lock).

P2:

- Ensure deterministic tool shell runtime PATH (`uv`, `sqlite3`) or add preflight capability checks + graceful fallback.
- Improve run lineage indexing: append-only per-turn logs (or turn-specific log files) to prevent context ambiguity.

P3:

- Fix public websocket routing for `/api/v1/sessions/{id}/stream` (or expose an officially supported external stream path and document it).
- Add structured “goal satisfaction” post-check before `query_complete`/terminal summary.

## 9. How To Review This Run Quickly

1. Session workspace:
- `/opt/universal_agent/AGENT_RUN_WORKSPACES/session_20260218_025507_ad6baf7c`

2. Delegated capability outputs:
- `/opt/universal_agent/AGENT_RUN_WORKSPACES/vp_coder_primary/work_products/capability_demo_20260218_025815`

3. Stream capture:
- `/opt/universal_agent/tmp/vps_query_session_20260218_025507_ad6baf7c_20260218T025507Z.jsonl`

4. Transcript:
- `/opt/universal_agent/AGENT_RUN_WORKSPACES/session_20260218_025507_ad6baf7c/transcript.md`

## 10. Conclusion

The system can execute a complex long-running multi-tool workflow and generate substantial intermediate artifacts on VPS. However, this run exposed a control-plane issue: heartbeat/system investigation can displace the user mission in-session, and required deliverables (Gmail sends) are not currently enforced as hard completion criteria. Addressing mission gating and heartbeat isolation is required before treating this path as fully production-ready.

## 11. Implementation Plan and Status (Executed 2026-02-18)

This section tracks the concrete implementation sequence used to close Section 8 gaps and push the system toward the happy path.

1. Mission completion guardrails (P1): Implemented.
- Added prompt-to-contract inference for required delivery channels (email/Gmail) and minimum tool-call checkpoints.
- Added structured post-run goal-satisfaction evaluation before terminal completion.
- If requirements are not met, the turn is marked failed with `goal_satisfaction_failed`, and `query_complete` includes structured failure details instead of declaring a successful mission completion.

2. Heartbeat isolation from active foreground runs (P1): Implemented.
- Added strict no-overlap lock in heartbeat scheduling using runtime metadata (`active_foreground_runs`, active UI connections).
- Added foreground cooldown gating (`UA_HEARTBEAT_FOREGROUND_COOLDOWN_SECONDS`, default 1800s) so heartbeat does not immediately re-enter user mission sessions.
- Scheduled windows consumed during lock states remain non-backfilling to preserve deterministic behavior.

3. Deterministic runtime shell prerequisites (P2): Implemented.
- Added runtime PATH normalization at API/gateway/engine boundaries to include stable binary locations.
- Added health endpoint visibility for runtime tool availability (`uv`, `sqlite3`).
- Deployment script now enforces prerequisites on VPS (`sqlite3` install, `uv` install/symlink) before dependency sync.

4. Run lineage indexing (P2): Implemented.
- Added append-only per-turn lineage files at `AGENT_RUN_WORKSPACES/<session>/turns/<turn_id>.jsonl`.
- Captures start/finalize events, run source, request preview, status, completion summary, and `run.log` byte offsets for deterministic per-turn traceability.

5. Public websocket route compatibility (P3): Implemented.
- Added API pass-through websocket endpoint at `/api/v1/sessions/{session_id}/stream`.
- Endpoint proxies the canonical gateway stream protocol, so deployments can route this path to API service without breaking external clients.

6. Verification: Implemented.
- Added focused tests for mission guardrails, heartbeat foreground lock, and turn-lineage/runtime counters.
- Ran targeted pytest subsets successfully during implementation.
