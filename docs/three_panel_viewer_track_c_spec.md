# Three-Panel Viewer — Track C Implementation Spec

> **Status**: Spec for review. No code changes yet. Track C finishes
> what Track B intentionally deferred: it migrates the live writer-mode
> chat (WebSocket streaming + input composer) onto the new viewer
> route, retires `app/page.tsx`'s legacy viewer, deletes
> `chatWindow.ts` + `taskWorkspaceTarget.ts`, and redirects `/` to a
> dashboard landing.
>
> Track A shipped (`f9dcc65`). Track B shipped 8 commits up through
> `89ef647`. This is the third and final track.

## Why Track C exists

Track B left writer-mode (live chat with typing into an active session)
on the legacy root page (`app/page.tsx` rendering `/?session_id=...&run_id=...`)
because reimplementing WebSocket streaming was out of scope. That left
**15 call sites still using `chatWindow.ts`** to navigate to the legacy
viewer:

| File | Sites | Use |
|---|---|---|
| `web-ui/components/OpsDropdowns.tsx` | 4 | Live-attach to live agent sessions |
| `web-ui/components/dashboard/SessionsPage.tsx` | 4 | Rehydrate, Chat-with-Simone, new session |
| `web-ui/app/dashboard/page.tsx` | 6 | Various dashboard CTAs that open chat |
| `web-ui/app/dashboard/chat/page.tsx` | 1 | "Open" with no session = new |

Plus the legacy `chatWindow.ts` URL convention (`/?session_id=...`)
keeps an extra parallel surface alive that drifts from the new
contract over time. Track B's `SHIP_HANDOFF.md` already lists this as
"future Track C." This spec is that.

## Scope summary

| Layer | Track B status | Track C delivers |
|---|---|---|
| Read-only viewer route | ✅ exists at `/dashboard/viewer/<kind>/<id>` | Add live writer mode (WebSocket + input composer) |
| Producer URL building | ✅ migrated for read-only | Migrate remaining 15 writer-mode sites |
| `chatWindow.ts` | Kept (used by writer-mode) | **Deleted** |
| Legacy `app/page.tsx` viewer | Kept (writer-mode surface) | **Deleted; `/` redirects to `/dashboard`** |
| `taskWorkspaceTarget.ts` | Kept (render-gate) | **Deleted; render-gate inlined** |
| Compose / new-session flow | On legacy root | New `/dashboard/compose` route |

## Architecture

```mermaid
flowchart TD
  subgraph PRODUCERS [All 5 producer surfaces]
    P1[Task Hub] --> H[openViewer]
    P2[Sessions] --> H
    P3[Calendar] --> H
    P4[Proactive history] --> H
    P5[Chat dashboard] --> H
    P6[OpsDropdowns] --> H
    P7[Dashboard CTAs] --> H
  end

  H --> RESOLVE[POST /api/viewer/resolve]
  RESOLVE --> ROUTE[/dashboard/viewer/<kind>/<id>]

  subgraph VIEWER [New viewer route — read + write]
    ROUTE --> HYDRATE[GET /api/viewer/hydrate]
    HYDRATE --> HISTORY[Chat panel]
    HYDRATE --> LOGS[Logs panel]
    HYDRATE --> FILES[Files panel]
    ROUTE -.if is_live_session && role=writer.-> WS[WebSocket attach]
    WS --> STREAM[StreamingChat component]
    STREAM --> INPUT[Composer input]
  end

  subgraph COMPOSE [New session flow]
    P_NEW[New chat button] --> COMPOSE_OPEN[openCompose]
    COMPOSE_OPEN --> COMPOSE_ROUTE[/dashboard/compose]
    COMPOSE_ROUTE --> NEW_WS[WebSocket: open new session]
    NEW_WS --> ROUTE
  end

  ROOT[/] -.redirects.-> DASHBOARD[/dashboard]
```

## Open questions for you to react to

1. **Composer extraction**: should I extract the input composer + message
   list from `app/page.tsx` as a reusable `<StreamingChat>` component
   first (one large prep commit), or inline the move into the viewer
   route directly (one large commit, harder to review)? **My pick:
   extract first.** It produces a smaller diff per commit and a clean
   reusable surface.

2. **Compose route shape**: `/dashboard/compose` (new top-level) vs.
   `/dashboard/viewer/new` (special-cased target_id). My pick:
   **`/dashboard/compose`** — the viewer route is anchored on
   `target_id` and forcing a synthetic id is awkward.

3. **Feature flag for rollback safety**: writer-mode chat is the primary
   user surface. A regression there is felt immediately. My
   recommendation: gate the new writer mode behind
   `NEXT_PUBLIC_UA_VIEWER_LIVE_WRITER` (default OFF until validated),
   then flip it on after one or two days of test traffic. **Approve?**

4. **Legacy URL behavior after Track C**: should `/?session_id=...` keep
   working forever (redirected to the new route) or hard 404 after a
   sunset window? My pick: **forever-redirect** — bookmarked URLs
   in emails/Calendar events should not break. The cost is a tiny
   server-side redirect handler; the benefit is no broken bookmarks.

5. **WebSocket resume semantics**: when a user navigates between
   `/dashboard/viewer/run/run_a` and `/dashboard/viewer/session/<sid>`,
   should we tear down + reconnect the WebSocket, or share one socket
   keyed by session_id? My pick: **tear down + reconnect on every
   target change**. Keeps the lifecycle simple and matches what
   `app/page.tsx` does today (navigation = full reload).

6. **Migration ordering for the 15 writer-mode sites**:
   1. `OpsDropdowns.tsx` (4 sites) — high traffic, well-defined
   2. `app/dashboard/page.tsx` (6 sites) — many small CTAs
   3. `SessionsPage.tsx` writer paths (4 sites) — the trickiest
      because it includes Chat-with-Simone and new-session flows
   4. `app/dashboard/chat/page.tsx` new-session button (1 site)
   5. Final cleanup: delete legacy + redirect `/`

   **Confirm or reorder?**

7. **Scope cap**: Track C is bigger than Track B (~10 commits, ~2,000
   lines net) because we're moving WebSocket logic, not adding a
   read-only layer. Do you want me to do all 10, or stop after the
   live-writer foundation lands and re-evaluate before the producer
   migration?

## Detailed commit plan

### Commit C1 — Extract `<StreamingChat>` component (prep)

**No behavior change.** Pulls the streaming chat panel + input composer
out of `app/page.tsx` into a reusable component. The legacy root page
keeps working but now uses the extracted component internally.

Files:
- `web-ui/components/chat/StreamingChat.tsx` (NEW — ~400 lines)
- `web-ui/components/chat/Composer.tsx` (NEW — ~150 lines)
- `web-ui/components/chat/MessageList.tsx` (NEW — ~250 lines)
- `web-ui/app/page.tsx` (EDIT — replaces inline JSX with `<StreamingChat>`)
- `web-ui/components/chat/StreamingChat.test.tsx` (NEW)

Acceptance: legacy `/?session_id=...` URLs still work identically.
Unit tests verify the composer triggers the correct WebSocket sends.

### Commit C2 — Live writer mode in the viewer route

Adds writer-mode rendering to `/dashboard/viewer/<kind>/<id>` when
`is_live_session && role=writer`. Behind
`NEXT_PUBLIC_UA_VIEWER_LIVE_WRITER` flag (default OFF).

Files:
- `web-ui/app/dashboard/viewer/[targetKind]/[targetId]/page.tsx` (EDIT —
  conditionally render `<StreamingChat>` instead of read-only history)
- `web-ui/lib/viewer/openViewer.ts` (EDIT — `role: "writer"` already
  passes through; clarify behavior)

Acceptance: with flag ON, opening the viewer for a live session in
writer mode connects the WebSocket and renders the composer. With
flag OFF, the read-only Track B behavior is unchanged.

### Commit C3 — Compose route for new sessions

Adds `/dashboard/compose` for the "new session" flow that's currently
handled by `chatWindow.ts`'s `newSession: true` path.

Files:
- `web-ui/app/dashboard/compose/page.tsx` (NEW)
- `web-ui/lib/viewer/openCompose.ts` (NEW — sibling of openViewer)
- `web-ui/lib/viewer/openCompose.test.ts` (NEW)

Acceptance: `openCompose({ message, autoSend })` opens
`/dashboard/compose?message=...&auto_send=1`, the page opens a new
WebSocket session and seeds the composer with the message.

### Commits C4–C7 — Migrate the 15 writer-mode call sites

Each commit is one producer file. Each replaces all
`openOrFocusChatWindow(...)` calls with either `openViewer(...)` (for
live attach) or `openCompose(...)` (for new sessions).

| Commit | File | Sites |
|---|---|---|
| C4 | `web-ui/components/OpsDropdowns.tsx` | 4 |
| C5 | `web-ui/app/dashboard/page.tsx` | 6 |
| C6 | `web-ui/components/dashboard/SessionsPage.tsx` writer paths | 4 |
| C7 | `web-ui/app/dashboard/chat/page.tsx` new-session button | 1 |

Each commit deletes its `chatWindow` import once the file's last
caller is gone. After C7, no call site imports from `lib/chatWindow.ts`.

### Commit C8 — Delete `chatWindow.ts` + `taskWorkspaceTarget.ts`

Now safe — confirmed by `grep -rn "chatWindow\|taskWorkspaceTarget"
web-ui/` returning empty for non-test imports.

Files deleted:
- `web-ui/lib/chatWindow.ts`
- `web-ui/lib/chatWindow.test.ts`
- `web-ui/lib/taskWorkspaceTarget.ts`
- `web-ui/lib/taskWorkspaceTarget.test.ts`

Edits:
- `web-ui/app/dashboard/todolist/page.tsx` — replace
  `resolveTaskWorkspaceTarget` render-gate with a small inline check
  on `(item.links?.session_id || item.canonical_execution_session_id ||
   item.assigned_session_id || item.canonical_execution_run_id ||
   item.workflow_run_id)`.

### Commit C9 — Retire `app/page.tsx` viewer; `/` → `/dashboard`

The legacy root viewer now has no producers pointing at it (after
C2-C7), and Track B's read-only viewer + Track C's writer viewer have
fully replaced its functionality.

Files:
- `web-ui/app/page.tsx` (REPLACED — becomes a tiny redirect to
  `/dashboard` for non-deeplinked visits, OR a back-compat resolver
  that takes legacy `?session_id=` URLs, calls `/api/viewer/resolve`,
  and redirects to the canonical viewer route)
- `web-ui/lib/store.ts` (EDIT IF NEEDED — only if exclusive to legacy)

Acceptance:
- New visitors to `/` land on `/dashboard`
- Bookmarked `/?session_id=...&run_id=...` URLs redirect to the new
  viewer route via the resolver
- Bundle size measurably smaller; Lighthouse scores improve

### Commit C10 — Spec status update + e2e contract test

Mirrors Track B Commit 8: marks the spec complete, lists all 10
commit hashes, ships an e2e Playwright test that exercises the full
read + write flow through the new viewer route.

Files:
- `docs/three_panel_viewer_track_c_spec.md` (this file — status banner)
- `tests/viewer/test_viewer_writer_e2e.py` or Playwright equivalent
- `tests/viewer/test_viewer_compose_e2e.py`

## Test matrix

### Backend
- No backend changes. The existing `/api/viewer/resolve` and
  `/api/viewer/hydrate` from Track B Commit 1 already support
  `is_live_session` and the `viewer_href` shape we need.

### Frontend
- `StreamingChat.test.tsx` — composer dispatches correct WebSocket
  messages; renders streaming events; handles disconnect/reconnect.
- `openCompose.test.ts` — produces correct URL with message + autoSend
  params; SSR-safe.
- `viewer/[targetKind]/[targetId]/page.test.tsx` — live writer mode
  flag toggle, fallback to read-only when flag OFF.

### Playwright (end-to-end)
- Open Task Hub completed card → all three panels populated (already
  in Track B but reasserted).
- Click "Live attach" on a daemon session → WebSocket connects,
  composer accepts input, response streams.
- Click "New session" from Sessions dashboard → compose route loads,
  message seeds the composer.
- Bookmarked `/?session_id=...` URL → redirects to new viewer route.

## Risk assessment

- **C1 (extract component)**: low risk. No behavior change. If the
  extraction misses anything, the legacy page breaks immediately and
  we revert one commit.
- **C2 (writer mode)**: medium risk, but **flag-gated**. Default OFF
  until validated. We can flip the flag in production via Infisical
  without a redeploy.
- **C3 (compose route)**: low risk. New route, doesn't touch
  existing surfaces.
- **C4–C7 (producer migrations)**: per-commit rollback. Each producer
  is migrated independently and tested independently.
- **C8 (delete helpers)**: zero behavior change since C7 removed the
  last caller. Static-grep verifies no dangling imports.
- **C9 (retire app/page.tsx)**: highest risk because it touches the
  root URL. Mitigation: keep the resolver-redirect alive forever for
  bookmarked URLs; the only thing that changes is "first-time visit
  to `/` lands on `/dashboard` instead of an empty chat."

## Effort estimate

- C1: ~700 lines (extracted component + tests). Low complexity, careful
  diff hygiene.
- C2: ~150 lines (viewer route conditional + flag wiring).
- C3: ~250 lines (compose route + helper + tests).
- C4–C7: ~30-80 lines each (per-producer migrations).
- C8: ~150 lines deleted.
- C9: ~100 lines (root replaced with redirect/resolver).
- C10: ~200 lines (e2e tests + spec status update).

**Total: ~10 commits, ~1,800 lines net (with significant deletions in
C8/C9).** About 25% bigger than Track B. Most of the volume is in C1's
component extraction and the e2e tests in C10.

## What this gives you

After Track C ships:

1. **One viewer surface**: `/dashboard/viewer/<kind>/<id>` is the
   single read + write surface for sessions and runs. No parallel
   `/?session_id=...` URL drift.
2. **One URL builder**: every producer calls `openViewer()` or
   `openCompose()`. No producer constructs viewer URLs locally.
3. **One source of identity truth**: the backend resolver decides
   target_kind / target_id / workspace_dir / viewer_href. Frontend
   never computes these.
4. **A radically smaller `app/page.tsx`**: 3,080 lines → ~50 lines
   (just the redirect).
5. **Deleted legacy surfaces**: `chatWindow.ts`, `taskWorkspaceTarget.ts`,
   plus the trace/log parsers that were only used by `app/page.tsx`.

## React to the open questions and I'll start coding

I'd recommend starting with C1 (extract `<StreamingChat>`) as soon as
you're aligned, since it's the foundation for everything else. The
extraction commit itself produces no behavior change, so it's a safe
first ship that proves the contract before C2 introduces the
writer-mode flag.

When you're ready, just say "go" or push back on any of the seven
questions above.
