# 14. Current Session Runtime Behavior and Recovery Model (2026-02-10)

## 1) Purpose

This document explains how sessions behave **today** in Universal Agent when you:

- start fresh,
- switch between dashboard panels and chat,
- reconnect to an existing session,
- see multiple sessions listed,
- and decide whether to delete/keep sessions.

This is a factual state-of-system document, not a future design proposal.

---

## 2) Short Answer (Executive Summary)

- A session does **not** automatically stop just because you navigate away from one panel.
- A running turn can continue in the backend even if the chat UI detaches/disconnects.
- Returning to chat usually **reattaches** to a session, but chat bubbles are not fully reconstructed from transcript by default.
- Seeing multiple sessions is normal: session listing is workspace-backed and includes existing session directories.
- Rehydration is adapter/session reattachment to an existing workspace; it is **not** a full UI replay of prior conversation.

---

## 3) What Happens on a Fresh Start

## 3.1 Process stack

In full gateway mode, you run:

- Gateway server (`:8002`) — canonical session runtime.
- API server (`:8001`) — web-facing WS/REST bridge.
- Next web UI (`:3000`) — dashboard + chat surface.

## 3.2 Session creation

When no `session_id` is requested, WebSocket connection creates a new session.

- API WS endpoint: `src/universal_agent/api/server.py:414`
- If no `session_id` supplied, create session path: `src/universal_agent/api/server.py:441`

On gateway side, session creation provisions a workspace in `AGENT_RUN_WORKSPACES/session_*`.

- Create endpoint: `src/universal_agent/gateway_server.py:2791`
- In-process gateway create: `src/universal_agent/gateway.py:138`

---

## 4) Switching Panels: Does Session Stop?

## 4.1 Switching inside Dashboard routes

Changing between `/dashboard/*` panels does not inherently delete a session. Sessions are runtime objects plus workspace directories.

- Session runtime map: `src/universal_agent/gateway_server.py:317`
- Session listing uses workspace directories as source of truth in ops service: `src/universal_agent/ops_service.py:84`

## 4.2 Switching away from full chat page

The main chat page (`/`) opens WS and attaches either:

- requested `session_id` from URL, or
- stored session id in tab storage.

- Attach behavior on mount: `web-ui/app/page.tsx:1255`
- Session storage and auto-resume keying: `web-ui/lib/websocket.ts:100`

Important nuance:

- `HomePage` cleanup removes event listeners, but does not explicitly disconnect socket manager.
- If tab/process remains alive, socket may remain connected; if page/tab unloads, connection closes.

---

## 5) If a Turn Is Running and UI Detaches

A submitted execution turn is launched as background async task in gateway WS handler:

- `asyncio.create_task(run_execution(...))`: `src/universal_agent/gateway_server.py:4443`

That means:

- a running turn can continue server-side even if a client disconnects mid-run,
- output still lands in workspace files (`run.log`, `transcript.md`, work products),
- but disconnected UI misses live event stream while detached.

Connection count and active runs are tracked independently:

- connection counters: `src/universal_agent/gateway_server.py:1156`
- active run counters: `src/universal_agent/gateway_server.py:1164`

---

## 6) Returning to Chat: Resume/Reattach Behavior

## 6.1 What resume does

Reattach path:

- WS connect includes stored `session_id` if present: `web-ui/lib/websocket.ts:141`
- explicit attach call also supported: `web-ui/lib/websocket.ts:247`
- gateway stream resumes session if not already in memory: `src/universal_agent/gateway_server.py:4023`
- backend rehydrate from disk workspace if needed: `src/universal_agent/gateway.py:236`

## 6.2 What resume does **not** do

Current implementation does **not** fully rebuild chat bubble history from `transcript.md` into the in-memory UI store on attach.

- Message store is in-memory Zustand state: `web-ui/lib/store.ts:154`
- No transcript-to-messages replay path in chat bootstrap.

So:

- reconnect gives you live continuation from now forward,
- prior conversation may not appear as reconstructed bubbles unless still in same live page state.

---

## 7) Why You See Multiple Sessions

You can see multiple sessions because listing is directory-based + runtime-enriched.

- Ops list scans workspace directories: `src/universal_agent/ops_service.py:84`
- Session cards in chat launcher use this list: `web-ui/app/dashboard/chat/page.tsx:20`

Common causes:

- Previous sessions were not cleaned (`--clean-start` not used).
- “Open New Chat Surface” created another session (when no session_id attached).
- Legacy/session artifacts remain on disk and are intentionally visible.

This is expected behavior, not necessarily a fault.

---

## 8) Are We “Restarting From Files”?

Partially, yes, depending on state:

- If session is still in memory, reattach uses current in-memory session object.
- If not in memory but workspace exists, gateway recreates adapter from workspace dir and resumes.

Resume from workspace path:

- `src/universal_agent/gateway.py:244`
- adapter re-init: `src/universal_agent/gateway.py:255`

But this is runtime rebind/recover, not full conversational replay in the chat UI.

---

## 9) Current Deletion and Cleanup Controls

## 9.1 Delete one session

Now available directly in Chat Launcher via per-row `Delete` action.

- UI: `web-ui/app/dashboard/chat/page.tsx`
- API call: `DELETE /api/v1/ops/sessions/{id}?confirm=true`
- Gateway route: `src/universal_agent/gateway_server.py:3783`
- Ops delete implementation: `src/universal_agent/ops_service.py:188`

## 9.2 Clean all runtime session state

Use:

`./start_gateway.sh --clean-start`

This archives prior runtime/session state and starts fresh.

---

## 10) Direct Answers to Your Questions

## Q1: If we switch panels to dashboard, does session stop?

No. It does not automatically terminate. It may detach from one UI surface, but session/runtime/workspace persists unless explicitly deleted/reset.

## Q2: Is it running in the background?

If a turn is already executing, yes — it can continue in background (`asyncio.create_task` execution path). If no active turn, session remains idle/persistent.

## Q3: Do we resume when we go back to chat?

Yes, usually by stored session id or explicit `session_id` attach. Resume is connection/runtime reattachment, not full transcript replay into bubbles.

## Q4: Are past activities no longer visible?

Live stream history in chat bubbles may not be fully visible after detach/reattach because message state is in-memory. Files/logs remain in workspace and are still available.

## Q5: If more than one session is visible, are we rehydrating/restarting from workspace files?

Multiple visible sessions mostly means multiple workspace sessions exist. Rehydration occurs only when you attach to one; then gateway may restore adapter from that session’s workspace.

---

## 11) Practical Operating Guidance (Current Model)

- Use one “active mission” session at a time for cleaner UX.
- Delete stale sessions from Chat Launcher when done.
- Use `--clean-start` when you truly want a blank slate.
- Treat workspace files as authoritative audit trail; treat chat bubbles as live-stream view.

---

## 12) Known Gap (Current)

Missing today:

- deterministic full transcript replay into chat bubbles on reattach.

Present today:

- robust session reattach,
- workspace-backed persistence,
- live stream continuation,
- session continuity metrics and attach/resume counters.

