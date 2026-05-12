# Plan — Simone-Chat Mission Control Replacement + Task Hub Integration

## Context

The mission control dashboard has a top-edge fly-out **System Command Bar** that accepts natural-language input and POSTs to `/api/v1/dashboard/system/commands`. Today it has three structural problems:

1. **It's broken.** The handler in `src/universal_agent/gateway_server.py:26060-26310` references an undefined variable `should_mirror` on line 26204 (defended only by a FIXME comment and `noqa: F821`). Every non-trivial submit raises `NameError` → FastAPI returns 500. The operator hit this three times in a row trying to dismiss a quarantined email (`email:4820dcb514addc50`) that had been languishing for 12 days. *(Fix applied 2026-05-12 — `should_mirror = False` default added.)*

2. **Even when working, it's a regex classifier feeding Task Hub.** The bar runs ~5 keyword/regex checks (`_system_command_is_status_query`, `_…_is_brainstorm_capture`, `_…_is_personal_task`, schedule extractor), buckets the text, writes a `source_kind="system_command"` Task Hub row, and returns "captured" immediately. The operator gets no confirmation Simone parsed it correctly until her next heartbeat — minutes later. Silent mis-classification is the dominant failure mode.

3. **Simone chat sessions exist in parallel but never register in the Task Hub Kanban.** Sessions live in the session directory (`/dashboard/chat`, `/dashboard/sessions`) but are invisible to the todolist/Kanban view, so operator-facing work Simone is doing has two disjoint surfaces.

The desired end state:

- **Mission control bar becomes a chat dropdown.** On submit, opens a *new tab*, *new session* in the standard 3-panel chat view (left chat / middle events / right files), with the operator's text pre-loaded and auto-sent so Simone is already running the query when the tab appears.
- **Every Simone chat session — from the bar OR from any existing entry — is a first-class Task Hub row.** `source_kind="simone_chat"`, created in `status="in_progress"` (bypasses dispatch claim), assigned to Simone by definition. Title derived from the operator's first message.
- **Hybrid completion lifecycle.** Simone proposes done (via existing `query_complete` event with `completed=True`); auto-complete after N minutes of operator silence; explicit "mark complete" button as override. Operator-resume (new message after completion) flips the row back to `in_progress` with no new task_id.
- **Regex pipeline gone.** `/api/v1/dashboard/system/commands` and its helpers are deleted. The `system_command` source_kind stays in Task Hub as a historical reference; no new rows are written.
- **Quarantined email card gets direct verb buttons** as standalone UX polish (operator's original "delete this email" intent should be one click, not a chat round-trip).

Simone runs ZAI/GLM everywhere (Doc 10:43) — no "interactive Simone on Anthropic Max" flavor. A dashboard-spawned chat session is just another ZAI Simone session with the same auth/env/cost as autonomous Simone.

## Architecture Notes (Code-Verified)

| Subject | Finding | Source |
|---|---|---|
| Task Hub schema | `task_hub_items` has `task_id`, `source_kind`, `source_ref`, `title`, `status`, `metadata_json`, etc. **No `assigned_to` column** — routing is via `source_kind`. | `task_hub.py:248-277` |
| Valid statuses | `in_progress` is a first-class status. Terminal set: `{completed, parked, cancelled}`. | `task_hub.py:16-27` |
| Dispatch bypass | `claim_next_dispatch_tasks` filters `WHERE status IN ('open', 'needs_review')` — writing `status="in_progress"` directly is safe (won't be re-claimed). | `task_hub.py:1979`, `task_hub.py:2013` |
| Source_kind allowlist | None. Any string accepted; only `forbidden_source_kinds` exists for dispatch filtering. Adding `simone_chat` is non-breaking. | `services/dispatch_service.py:289` |
| Kanban columns | Hardcoded: `["Backlog", "In Progress", "In Review", "Done"]`. Feeds from `/api/v1/dashboard/todolist/tasks`. New source_kinds appear automatically. | `web-ui/components/dashboard/KanbanBoard.tsx:21,281` |
| Session creation | `POST /api/v1/sessions` → `gateway.create_session()` → `_register_session_with_runtime_services()`. Client-side enters via `ws.startNewSession()`. | `gateway_server.py:16578-16634` |
| Completion signal | Server emits `query_complete` with `completed=True` when Simone finishes a turn. **No new event type needed** — we hook this. | `gateway_server.py:7462` |
| Chat URL helper | `buildChatUrl({ newSession, message, autoSend, focusInput })` already supports everything needed. `openOrFocusChatWindow` reuses a fixed window name (`ua-chat-window`) which would refocus same tab — we bypass it and call `window.open(url, "_blank")` directly. | `web-ui/lib/chatWindow.ts:15-50` |
| Command bar mount | Hidden 1px hover-zone at top edge of dashboard layout; reveals on hover. Hidden on `/dashboard/chat` and `/dashboard/csi`. | `web-ui/app/dashboard/layout.tsx:215-242` |
| Quarantined card | Lives in todolist page as a styled badge on items where `labels.includes("quarantined")`. Has `task_id`. Only existing action: delete via `DELETE /api/v1/dashboard/todolist/dismiss/{task_id}` (sets status=cancelled, stale_state=dashboard_dismissed). | `web-ui/app/dashboard/todolist/page.tsx:1015-1070`, `gateway_server.py:23445-23475` |
| Observability protocol | NOT required for chat tasks — protocol scopes to crons/missions/webhooks. Optional `_open_run` / `_close_run` for audit; skip for v1. | `docs/03_Operations/129_Task_Hub_Observability_Protocol.md:14-32` |

## Implementation — Sequenced PRs

### PR 1 — Task Hub `simone_chat` source kind + lifecycle hooks (backend only)

**Goal:** Stand up the new source_kind and lifecycle without UI changes. Deployable independently; safe because nothing writes `simone_chat` rows yet from any UI.

**New / changed code:**

- `src/universal_agent/services/simone_chat_tasks.py` *(new module)* — single home for all chat→task lifecycle logic. Functions:
  - `record_first_operator_message(session_id, first_message, source_page=None) -> task_id` — idempotent. Writes Task Hub row with `source_kind="simone_chat"`, `source_ref=session_id`, `status="in_progress"`, `title=first_message[:120]`, `metadata={session_id, source_page, started_at, last_operator_message_at, completion_proposed_at: None}`. Uses `task_hub.upsert_item` with a deterministic `task_id = "simone_chat:" + session_id`.
  - `on_operator_message(session_id, text)` — updates `metadata.last_operator_message_at`. If row was `status="completed"`, flips back to `status="in_progress"` and clears `metadata.completion_proposed_at`.
  - `on_query_complete(session_id, completed: bool)` — when `completed=True`, sets `metadata.completion_proposed_at = now`. Does NOT change status yet.
  - `mark_complete(session_id)` — sets `status="completed"`. Used by manual button and by auto-completer.
  - `auto_complete_stale(now, idle_threshold_minutes)` — scans `simone_chat` rows where `status="in_progress"` AND `completion_proposed_at` is set AND `last_operator_message_at <= completion_proposed_at` AND `now - completion_proposed_at >= idle_threshold_minutes`. Calls `mark_complete` for each.

- `src/universal_agent/gateway_server.py` — wire hooks:
  - In the inbound websocket message handler (where operator text arrives — locate during implementation; receives messages on `/api/v1/sessions/{session_id}/stream`): call `simone_chat_tasks.on_operator_message(session_id, text)` if message looks like an operator turn (not a system message or tool reply). If this is the first operator message on this session_id, also call `record_first_operator_message`.
  - In the `query_complete` emission site (`gateway_server.py:7462`): call `simone_chat_tasks.on_query_complete(session_id, completed=event.get("completed", False))`.
  - New endpoint `POST /api/v1/dashboard/simone_chat/{task_id}/complete` — calls `mark_complete`. Returns updated row.
  - New endpoint `POST /api/v1/dashboard/simone_chat/{task_id}/reopen` — flips `status="completed"` → `in_progress` (operator override; needed when auto-complete fires prematurely). Returns updated row.

- `src/universal_agent/cron_service.py` (or wherever lightweight periodic tasks register): register `auto_complete_stale` as a 1-minute cron with `idle_threshold_minutes` from env (`UA_SIMONE_CHAT_IDLE_MINUTES`, default 10). Use the canonical `_register_system_cron_job` helper per CLAUDE.md and link it to Task Hub Observability Protocol.

- One-shot deploy backfill: `scripts/backfill_simone_chat_tasks.py` — scans the session directory, for each live session (`is_live_session=True`) lacking a `simone_chat:<session_id>` task, calls `record_first_operator_message` with a best-effort title from session metadata. Closed historical sessions are skipped (would flood the board). Run once after PR 1 deploys; thereafter the hooks keep it in sync.

**Tests** (`tests/unit/test_simone_chat_tasks.py`):
- First operator message creates row in `in_progress`.
- Idempotent: second call with same session_id is a no-op.
- `query_complete` sets `completion_proposed_at` without changing status.
- New operator message on a `completed` row flips back to `in_progress`, clears `completion_proposed_at`.
- `auto_complete_stale` flips eligible rows; ignores rows where `last_operator_message_at > completion_proposed_at` (operator replied after Simone proposed done — conversation isn't actually over).
- Dispatch sweep (mock) does not claim `simone_chat` rows because status is `in_progress`.

**Verification:**
- Run `uv run pytest tests/unit/test_simone_chat_tasks.py` — all green.
- Local dev: `just dev`, open a chat session, send a message, watch the row appear in `/dashboard/todolist` under "In Progress".

---

### PR 2 — Mission control bar → Simone chat dropdown swap (UI)

**Goal:** Replace the broken regex bar with a chat-style dropdown that opens a fresh chat tab on submit.

**Depends on:** PR 1 (so the new sessions register in Task Hub).

**New / changed code:**

- `web-ui/components/dashboard/SimoneChatBar.tsx` *(new component)* — replaces `SystemCommandBar`. Same shell (textarea, dynamic placeholder, image-paste-to-vision, command history in localStorage), but the submit handler is:

  ```ts
  const onSubmit = (text: string) => {
    const url = buildChatUrl({
      newSession: true,
      message: text,
      autoSend: true,
      focusInput: true,
    });
    window.open(url, "_blank");  // bypass openOrFocusChatWindow's reused name
    pushToHistory({ text, opened_at: new Date().toISOString() });
    onSuccess?.();
  };
  ```

  Drop: dry-run preview UI (no longer meaningful), error-display for 500s (no endpoint to call), `sourceContext` synthesis (Simone reads context from her own session).

  Keep: command history with one-click "reuse" (operator workflow polish), image-paste-to-vision (lift verbatim — the `/api/v1/vision/describe` call stays), placeholder hints by `sourcePage`.

- `web-ui/app/dashboard/layout.tsx:215-242` — swap `<SystemCommandBar>` for `<SimoneChatBar>` in the fly-out. Layout/animation unchanged.

- Delete the old component file `web-ui/components/dashboard/SystemCommandBar.tsx` and its imports.

**Tests** (`web-ui/components/dashboard/SimoneChatBar.test.tsx` if test infra exists, else exercise via dogfood):
- Submit calls `window.open` with `target="_blank"` and a URL containing `new_session=1`, `message=<encoded>`, `auto_send=1`.
- Empty submit is a no-op.
- History persists across reloads (existing localStorage key works).

**Verification:**
- `just dev`, hover top edge → fly-out appears with new bar.
- Type a message, submit → new tab opens at `/?new_session=1&message=…&auto_send=1`.
- Within ~5s, Simone is running the query in the 3-panel view.
- `/dashboard/todolist` shows a new "In Progress" row under `source_kind=simone_chat`.

---

### PR 3 — Rip out the regex pipeline (cleanup)

**Goal:** Delete dead code now that nothing calls it.

**Depends on:** PR 2 (and a verification window of ≥1 day where PR 2 is live and the bar is producing chat sessions instead of system_command tasks).

**Deletions in `src/universal_agent/gateway_server.py`:**
- `dashboard_system_command` handler (lines 26060-26310).
- `_build_system_command_task_description`, `_strip_system_command_prefix`, `_extract_system_command_content_and_schedule`, `_system_command_priority_from_text`, `_system_command_is_status_query`, `_system_command_is_personal_task`, `_system_command_is_brainstorm_capture`, `_system_command_task_id`, `_park_duplicate_system_command_tasks` (lines 27875, 29033-29200 range).
- `DashboardSystemCommandRequest` Pydantic model.
- Env flag `UA_SYSTEM_COMMAND_ENABLE_CRON_BRIDGE` if not referenced elsewhere.

**Retentions:**
- The `source_kind="system_command"` value remains accepted by `task_hub.py` — old rows stay queryable. Just stop writing new ones.
- Helpers used by other source_kinds (e.g., `_resolve_simplified_schedule_fields`, `_build_task_hub_execution_cron_command`) stay if they have other callers — grep before deleting.

**Tests:**
- `tests/unit/test_dashboard_system_command_endpoint_removed.py` *(new)*: confirms `POST /api/v1/dashboard/system/commands` returns 404 (FastAPI default for missing route).
- Existing tests referencing the endpoint or helpers — delete or migrate.

**Verification:**
- `uv run pytest tests/unit` — green.
- `uv run ruff check .` — no unused imports.

---

### PR 4 — Quarantined email card: direct Archive / Delete verb buttons (parallel)

**Goal:** Eliminate the round-trip through chat for the most common single-verb operator action on the dashboard.

**Independent** of PRs 1-3. Can ship anytime.

**Backend** (`src/universal_agent/gateway_server.py`):
- The existing `DELETE /api/v1/dashboard/todolist/dismiss/{task_id}` endpoint (lines 23445-23475) sets `status="cancelled"`, `stale_state="dashboard_dismissed"`. Wire that to the "Delete" button.
- New endpoint `POST /api/v1/dashboard/todolist/archive/{task_id}` — sets `status="completed"`, `stale_state="dashboard_archived"`. Keeps the row in the audit trail (vs. `cancelled` which reads as "we shouldn't have done this"). Returns updated row.
- Optional: New endpoint `POST /api/v1/dashboard/mail/quarantine/{email_id}/release` if the AgentMail-side quarantine flag is independent of the Task Hub row (check `agentmail_service.py`). Probably out of scope for v1 — the Task Hub row is what the operator sees on the dashboard; the AgentMail quarantine flag can stay until the next inbox sweep.

**Frontend** (`web-ui/app/dashboard/todolist/page.tsx:1015-1070`):
- In the quarantined badge block, add two buttons after the badge label:
  - **Archive** → `POST /api/v1/dashboard/todolist/archive/{task_id}` → on success, refresh the todolist.
  - **Delete** → existing dismiss endpoint.
- Confirm-on-delete dialog (existing pattern in the file — look at the current delete button on line 1129).

**Tests:**
- `tests/unit/test_dashboard_todolist_archive.py` — new endpoint flips status correctly, refuses terminal-status targets.

**Verification:**
- Dev: trigger a quarantine event, find the card, click Archive → row moves to "Done" column with `archived` indicator. Click Delete on a different test card → row disappears.

---

## Decision Log (Locked)

| Question | Answer |
|---|---|
| Same chat tab or new tab per command? | **New tab per command, fresh session.** Bypass `openOrFocusChatWindow`; use `window.open(url, "_blank")` with `new_session=1`. |
| Which Claude profile for chat-spawned Simone? | **ZAI.** Same as autonomous Simone. No "interactive Simone on Anthropic Max" flavor (Anthropic Max is only Kevin's personal `claude` invocations per Doc 10). |
| Keep `system_command` source_kind? | **Yes**, as historical audit. Stop writing new rows after PR 3. |
| Completion lifecycle | **Hybrid:** Simone proposes done (existing `query_complete` event) → auto-complete after N min silence OR operator clicks "mark complete." Resume flips back to `in_progress` with same `task_id`. |
| When to create Task Hub row | **On first operator message** (not on session creation) — avoids polluting the Kanban with empty sessions where the user opened a tab and walked away. Bar-spawned sessions always have an immediate first message, so this is invisible there. |
| Backfill | Live sessions at deploy time only. Closed historical sessions skipped. |
| Observability Protocol compliance | **Skip for v1.** Chat tasks are event-driven, no subprocess, no cron — protocol doesn't apply. Revisit if audit requirements emerge. |

## Verification (End-to-End)

After all four PRs land:

1. **Mission control bar smoke** — hover top edge, type "what's in the queue?", submit. New tab opens at `/?new_session=1&message=…&auto_send=1`. Simone responds within ~10s. A new row appears in `/dashboard/todolist` under "In Progress" with title "what's in the queue?" and a `simone_chat` badge.
2. **Lifecycle smoke** — in the same chat, when Simone's response doesn't ask a follow-up, watch the row sit in "In Progress" with a `completion_proposed_at` set in metadata. Walk away 10+ minutes → row moves to "Done." Send a new message in the same tab → row flips back to "In Progress."
3. **Manual complete** — click "Mark complete" on a chat task in the Kanban → row immediately moves to "Done."
4. **Quarantined card verbs** — find a quarantined card on `/dashboard/todolist`, click Archive → row moves to "Done" with archive indicator. Click Delete on another → row disappears.
5. **Regex pipeline gone** — `curl -X POST http://localhost:8002/api/v1/dashboard/system/commands -H 'content-type: application/json' -d '{"text":"foo"}'` returns 404.
6. **Backfill smoke** — `python scripts/backfill_simone_chat_tasks.py --dry-run` reports the live sessions it would register. Run for real, confirm rows appear.

## Files To Modify

- **New:** `src/universal_agent/services/simone_chat_tasks.py`
- **New:** `scripts/backfill_simone_chat_tasks.py`
- **New:** `web-ui/components/dashboard/SimoneChatBar.tsx`
- **New tests:** `tests/unit/test_simone_chat_tasks.py`, `tests/unit/test_dashboard_system_command_endpoint_removed.py`, `tests/unit/test_dashboard_todolist_archive.py`
- **Modify:** `src/universal_agent/gateway_server.py` (hooks, new endpoints, deletion of regex pipeline)
- **Modify:** `src/universal_agent/cron_service.py` (or equivalent — register `auto_complete_stale` cron)
- **Modify:** `web-ui/app/dashboard/layout.tsx` (swap bar component)
- **Modify:** `web-ui/app/dashboard/todolist/page.tsx` (Archive/Delete buttons on quarantined card)
- **Delete:** `web-ui/components/dashboard/SystemCommandBar.tsx`

## Out of Scope (Followups)

- Adding `source_kind=simone_chat` badge styling in `KanbanBoard.tsx` — works without it; pure polish.
- Reuniting the AgentMail-side quarantine flag with the Task Hub archive action.
- A "tab tray" or "Simone session switcher" affordance for operators who pile up many chat tabs.
- Migrating `/dashboard/chat` and `/dashboard/sessions` pages to surface `simone_chat` task status alongside the existing session list view.
