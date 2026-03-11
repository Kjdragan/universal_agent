# To Do List Task Pipeline Audit (2026-03-11)

## Scope
Audit and repair the end-to-end flow for Task Hub tasks shown on `/dashboard/todolist`:
1. task creation
2. dispatch queue build
3. heartbeat claim/seize
4. execution/finalization
5. operator visibility (completed history + logs)

## Executive Summary
The pipeline issue was real:
- Heartbeats were running, but claim count remained zero in runtime events despite non-zero actionable counts.
- The queue logic could starve claimable tasks when non-claimable rows dominated the top ranks.
- The UI did not expose completed jobs or history/log links, and `Review` looked broken because it was mapped to a status mutation instead of run history.
- System command tasks were duplicated because IDs were random per submission.

Backend and frontend fixes were implemented, with regression tests added and passing.

## Current-State Evidence Collected
Data source: `AGENT_RUN_WORKSPACES/activity_state.db` (local runtime snapshot)

### Task status mix (at audit time)
- `parked`: 20
- `open`: 16
- `completed`: 6
- `in_progress`: 2

### Recent heartbeat completion events
Recent `activity_events.kind = autonomous_heartbeat_completed` records showed:
- `guard.actionable_count`: 11 (or 18 in older samples)
- `task_hub_claimed_count`: 0

This confirms the observed symptom: queue appears actionable, but no claims were made by heartbeat.

### Queue sample (latest build)
Top queue ranks included in-progress/ineligible rows before open rows. With the old claim logic (small top-window scan), this can starve actual claimable work.

## Existing Flow (as implemented)
1. User/system creates tasks via Task Hub upserts (`dashboard_system_command` and other paths).
2. `task_hub.rebuild_dispatch_queue()` scores and ranks all non-completed/non-parked tasks.
3. Heartbeat pre-step runs:
   - `task_hub.get_dispatch_queue(...)`
   - `task_hub.claim_next_dispatch_tasks(...)`
4. Claimed tasks are injected into heartbeat context for execution.
5. On heartbeat completion/failure, assignments are finalized and task statuses move through `open`/`in_progress`/`needs_review`/`completed`.
6. Dashboard reads queue/overview endpoints.

## Root Causes
1. **Claim starvation window**
   - `claim_next_dispatch_tasks()` previously inspected only top `limit * 6` rows from `get_dispatch_queue()`.
   - If those rows were mostly non-claimable (`needs_review`, `in_progress`, etc.), claimable `open` rows below that slice were never considered.

2. **Misleading freshness timestamp**
   - Queue rebuild updated `task_hub_items.updated_at` on every scoring pass.
   - UI therefore displayed “Updated less than a minute ago” repeatedly even without true lifecycle changes.

3. **Duplicate system command tasks**
   - System command task IDs were random (`scmd:<uuid>`), so repeated same command generated duplicates.

4. **Operator visibility gap in UI**
   - No completed-jobs panel.
   - No per-task history endpoint surfaced in UI.
   - `Review` button did status mutation, not actual review/history.

## Fixes Implemented

### 1) Claim logic fixed to query eligible/open directly
File: `src/universal_agent/task_hub.py`
- `claim_next_dispatch_tasks()` now:
  - rebuilds queue
  - reads latest `queue_build_id`
  - queries dispatch rows where `eligible=1 AND status='open'`
  - claims by rank from that set
- This removes top-slice starvation.

### 2) `updated_at` no longer touched by scoring-only rebuild
File: `src/universal_agent/task_hub.py`
- Rebuild scoring updates now skip `updated_at` mutation.
- `updated_at` remains lifecycle-meaningful.

### 3) Completed/history retrieval in Task Hub core
File: `src/universal_agent/task_hub.py`
- Added:
  - `list_completed_tasks()`
  - `get_task_history()`
  - `_session_id_from_agent_id()` for linking heartbeat assignments to session IDs.

### 4) New To Do API endpoints for completed/history + log/session links
File: `src/universal_agent/gateway_server.py`
- Added:
  - `GET /api/v1/dashboard/todolist/completed`
  - `GET /api/v1/dashboard/todolist/tasks/{task_id}/history`
- Added link helper:
  - `_task_history_links_for_session()` -> `session_href`, `run_log_href`, absolute `run_log_path`.

### 5) Duplicate system-command prevention and cleanup
File: `src/universal_agent/gateway_server.py`
- Added deterministic task signature ID:
  - `_system_command_task_id(...)` (hash-based)
- Added duplicate parking:
  - `_park_duplicate_system_command_tasks(...)`
- `dashboard_system_command` now:
  - uses deterministic task IDs
  - parks matching duplicates in active statuses
  - returns `duplicates_parked` telemetry.

### 6) To Do List UI now supports execution visibility and operator control
File: `web-ui/app/dashboard/todolist/page.tsx`
- Added completed jobs panel (latest finished work).
- Added task history panel with:
  - assignment history
  - evaluation trail
  - session/run log links
- `Review` now loads task history (actual review behavior).
- Added explicit `Mark Review` lifecycle action.
- Added heartbeat force controls:
  - global `Run Next Heartbeat`
  - per-task `Force Next Heartbeat`.

## Test Coverage Added/Updated
- `tests/unit/test_task_hub_lifecycle.py`
  - claim starvation regression
  - completed/history session-id coverage
- `tests/gateway/test_dashboard_system_commands.py`
  - deterministic system-command IDs
  - duplicate parking behavior
  - completed/history endpoints return links
- `tests/unit/test_todolist_dashboard_page.py`
  - updated page assertions for new UI/API behavior

### Test run result
Command:
`uv run pytest -q tests/unit/test_task_hub_lifecycle.py tests/gateway/test_dashboard_system_commands.py tests/unit/test_todolist_dashboard_page.py`

Result:
- `32 passed`

## Deployment / Runtime Note
The current runtime heartbeat event stream still shows historical `task_hub_claimed_count=0` records from before service reload. Code is fixed in repository; runtime must be restarted/deployed to pick up the new claim path.

## Recommended Post-Deploy Verification
1. Trigger a manual heartbeat wake (`mode=next`) from dashboard.
2. Confirm new `autonomous_heartbeat_completed` events show `task_hub_claimed_count > 0` when eligible open tasks exist.
3. Verify completed tasks appear in `/dashboard/todolist` Completed panel.
4. Click `Review` on completed item, confirm history renders and run log/session links resolve.
5. Submit same system command twice; verify same `task_id` and duplicate parking telemetry.
