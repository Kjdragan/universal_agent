# Heartbeat Debug Fixes (No-op Strictness, Text De-dupe, UI Visibility)

**Date**: 2026-02-05  
**Scope**: Heartbeat execution + gateway/web UI streaming reliability

## Why this change

During heartbeat evaluation, we observed three operational problems:

1. **No-op heartbeats were noisy**: the agent sometimes returned a checklist/summary plus `HEARTBEAT_OK`, which defeats suppression logic and creates confusing “heartbeat chatter”.
2. **Duplicate assistant text in streams**: the system could emit both streaming `text` chunks and a final aggregated `response_text` as another `text` event, causing repeated “wall of text” output.
3. **Checkbox semantics were inconsistent**: `HEARTBEAT.md` implied `[x]` meant “enabled”, but the heartbeat prompt treated unchecked items as “to do”.

Separately, the Web UI didn’t reliably show heartbeat activity because heartbeat runs in the gateway (`:8002`) and often had no connected gateway session stream to deliver events to.

## What was implemented

### 1) Single source of truth for checkbox meaning

Checkbox meaning is now explicitly encoded in the heartbeat prompt and in `memory/HEARTBEAT.md`:

- `- [ ]` = **ACTIVE / PENDING** (eligible to run if conditions match)
- `- [x]` = **COMPLETED / DISABLED** (do not run)

This eliminates the prior mismatch between documentation and runtime behavior.

### 2) Strict no-op contract for suppression

The heartbeat prompt now instructs the model:

- If and only if no tasks match, reply with **exactly** `HEARTBEAT_OK` (no additional text/markdown/explanation).

Additionally, token stripping is hardened with a defensive heuristic: if a known OK token appears along with obvious “no-op checklist” language, the heartbeat is treated as OK-only to avoid accidental unsuppressed no-op noise.

### 3) Final text marker + de-duplication rules

The execution engine now marks the final aggregated assistant text event as:

- `{"text": "...", "final": true}`

Then streaming layers apply a simple rule:

- If streaming `text` chunks have already been observed, **ignore** the `final: true` text event.

This prevents the common “streamed text + full replay” duplication pattern.

### 4) Heartbeat activity visibility in the Web UI

Heartbeat execution now broadcasts agent events (tool calls, results, status, etc.) onto the gateway session stream when a UI is connected.

In gateway mode, the UI API server (`:8001`) also maintains a passive subscription to the gateway session stream and forwards background broadcasts into the UI websocket, so heartbeats can be visible even when the user did not manually execute a query.

## Files touched (implementation map)

**Heartbeat behavior**
- `src/universal_agent/heartbeat_service.py`  
  - Updated default heartbeat prompt (checkbox semantics + strict `HEARTBEAT_OK` contract)
  - Hardened `_strip_heartbeat_tokens` for no-op suppression
  - Added broadcast of heartbeat execution events to session stream
  - Captured lightweight artifact metadata in `heartbeat_state.json` (`writes`, `work_products`, `bash_commands`)

**Text de-duplication**
- `src/universal_agent/execution_engine.py`  
  - Marks final aggregated assistant text as `final: true`
- `src/universal_agent/gateway_server.py`  
  - Filters `final: true` text when streaming chunks were already seen
- `src/universal_agent/api/gateway_bridge.py`  
  - Filters `final: true` text in the UI-facing bridge when streaming chunks were already seen
- `src/universal_agent/gateway.py`  
  - In `run_query`, prefers final text (overwrite) instead of always concatenating

**Checkbox documentation**
- `memory/HEARTBEAT.md`  
  - Updated comment to match the runtime semantics above

**Ops/UI observability**
- `src/universal_agent/gateway_server.py`  
  - `/api/v1/heartbeat/last` now returns `busy` for the selected session
- `web-ui/components/OpsPanel.tsx`  
  - Heartbeat card now shows: running/busy, delivered/suppressed, and recent artifact paths
- `web-ui/types/agent.ts`  
  - `TextEventData` includes optional `final?: boolean`

## How to verify

### A) No-op heartbeat is silent

1. Ensure `memory/HEARTBEAT.md` contains no matching active `[ ]` items for the current time.
2. Trigger a heartbeat wake (or wait for schedule).
3. Expected:
   - Model output is exactly `HEARTBEAT_OK`.
   - If a UI is connected, you may still see minimal status/progress events, but you should not see long no-op summaries treated as alerts.

### B) No duplicated assistant output

1. Run a query in the UI that streams assistant text.
2. Expected:
   - You do not get the same assistant response appended twice.
   - Tool calls should “break the wall of text” as before (tool-call event forces stream flush).

### C) UI shows heartbeat activity and artifacts

1. Run gateway + UI (`./start_gateway.sh`) and keep the Web UI open.
2. Trigger a heartbeat (`POST /api/v1/heartbeat/wake`) or wait for schedule.
3. Expected:
   - Activity/Logs panel shows tool calls and status as the heartbeat executes.
   - Ops Panel → Heartbeat shows busy/running state, last summary, suppression reason (if any), and recent artifact paths.

