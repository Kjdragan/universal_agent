# Three-Panel Viewer Centralization — Track B Implementation Spec

> **Status**: Spec for review. No code changes yet. Track A (the
> daemon_-strip bug + session_id query-param compatibility) shipped in
> commit `f9dcc65` as the immediate bleed-stopper. Track B is the proper
> architectural fix that ends the link-correctness whack-a-mole.

## Problem statement

The three-panel viewer (chat | logs | files) is reached from at least five
producers (Task Hub, Sessions dashboard, Calendar, Proactive history,
direct chat links). Each producer rebuilds the URL locally with implicit,
mutually-incompatible conventions:

- Some emit `?session_id=`, some emit `?sid=`. (Track A makes
  SessionsPage tolerant of both, but the split persists everywhere else.)
- Run-only viewer mode (no session_id, only run_id) is structurally
  broken: file helpers in `app/page.tsx` require session_id and return
  empty when only run_id exists, even though `api/server.py:1557`
  already supports run-based file access.
- Workspace path reconstruction relies on a chain of fallbacks
  (`active_run_workspace`, `run_manifest.json`, daemon glob, etc.) duplicated
  between `gateway_server.py:15890` and the UI's heuristics.
- History hydration is parsed client-side from `run.log` + `trace.json`
  in the browser (`app/page.tsx:1855`), while ops preview tails
  `activity_journal.log` server-side (`gateway_server.py:29241`). No
  single contract.
- The backend conflates *logical sessions* with *execution workspaces*:
  ops session listing treats top-level workspace dirs as sessions
  (`ops_service.py:366`) but workspace resolution recovers the real run
  workspace via live metadata + glob fallbacks elsewhere.

Every fix to one producer drifts the others. We need one identity model.

## Goal

Single canonical `SessionViewTarget` contract resolved server-side, a
dedicated viewer route, and one hydration endpoint that assembles all
three panels — replacing client-side URL building, client-side log
parsing, and ad-hoc query-param protocols.

## The identity model

```ts
type SessionViewTarget = {
  // What kind of thing the viewer is anchored to. Runs are the durable
  // anchor (a session can span many runs); a session_id without a run is
  // valid only for a still-live conversation.
  target_kind: "run" | "session";

  // Canonical id used in the viewer route. For target_kind="run", this is
  // a run_id (e.g. "run_abc123" or daemon-style "run_..."). For
  // target_kind="session", this is a logical session id (e.g.
  // "daemon_simone_todo", "vp_atlas_001").
  target_id: string;

  // Always populated when known (even for live sessions if a run has
  // started). Allows the viewer to switch between run-mode and
  // session-mode without re-resolving.
  run_id: string | null;
  session_id: string | null;

  // Absolute path to the workspace dir the viewer should browse. The
  // resolver runs the existing fallback chain server-side once and
  // returns the canonical answer. UI never reconstructs this.
  workspace_dir: string;

  // True if the session is currently active (websocket can attach).
  // False for archived runs. Drives "follow live" vs "static" UX.
  is_live_session: boolean;

  // Free-form provenance: which of the resolver inputs was the basis for
  // this answer ("run_catalog" | "active_run_workspace" |
  // "session_checkpoint" | "daemon_glob"). Used for diagnostics, not
  // routing. Logged in audit on resolve.
  source: string;

  // The absolute href the producer should navigate to. Producers MUST
  // use this verbatim — they MUST NOT build viewer URLs themselves.
  viewer_href: string;
};
```

**Why run-as-anchor**: a session can have multiple runs (especially Simone
sessions that get re-attached). A run is a single concrete execution
workspace. The viewer is fundamentally about one workspace at a time. So
runs are the durable identity; session_id is metadata.

## Architecture

```mermaid
flowchart LR
  subgraph PRODUCERS [Producers (Task Hub, Sessions, Calendar, Proactive, Chat links)]
    P1[Task Hub card] --> R[POST /api/viewer/resolve]
    P2[Sessions list row] --> R
    P3[Calendar event] --> R
    P4[Proactive history] --> R
    P5[Chat link] --> R
  end

  R -->|SessionViewTarget + viewer_href| PRODUCERS
  PRODUCERS -->|window.open viewer_href| ROUTE[/dashboard/viewer/:targetKind/:targetId]
  ROUTE -->|GET /api/viewer/hydrate?...| H[Hydration endpoint]
  H --> LEFT[Left panel: history (server-assembled)]
  H --> MID[Middle panel: logs (server-assembled)]
  H --> RIGHT[Right panel: workspace files]
  H --> READY[Readiness: pending | ready | failed]
```

## Backend changes

### 1. `src/universal_agent/viewer/resolver.py` — new module

```python
def resolve_session_view_target(
    *,
    session_id: str | None = None,
    run_id: str | None = None,
    workspace_dir: str | None = None,
    workspace_name: str | None = None,
) -> SessionViewTarget | None:
    """Accept any combination of identity hints; return the canonical target.

    Resolution order (the first match wins, but we always backfill the
    other fields if discoverable):

      1. run_id provided                  → run_catalog lookup
      2. workspace_dir provided           → reverse-lookup by path
      3. workspace_name provided          → AGENT_RUN_WORKSPACES/<name>
      4. session_id provided + has run_id → run_catalog lookup
      5. session_id provided, live        → daemon active_run_workspace
      6. session_id provided, archived    → daemon glob fallback

    Each branch records `source` so we can grep production logs and see
    which branch is hit most. Returns None if nothing resolves.
    """
```

This module **must consolidate all the existing fallback logic** currently
spread across `run_catalog.py`, `ops_service.py`, and `gateway_server.py`.
Those files keep their helpers but call `resolver.py` for the canonical
answer. Two duplicate code paths → one.

### 2. `src/universal_agent/api/viewer_routes.py` — new FastAPI router

| Method + Path | Body / Query | Returns |
|---|---|---|
| `POST /api/viewer/resolve` | `{session_id?, run_id?, workspace_dir?, workspace_name?}` | `SessionViewTarget` (200) or 404 |
| `GET /api/viewer/hydrate` | `?target_kind=run&target_id=run_…` | `{history, logs, workspace, readiness}` |

The hydration endpoint server-side assembles:

- **`history`**: parsed from `trace.json` (canonical) with fallback to
  `run.log`. Schema: `{messages: [{role, ts, content, sub_agent?, tool_calls?}], total_count, truncated_to}`.
- **`logs`**: normalized stream from `run.log` + `activity_journal.log`,
  deduped by event id, time-ordered. Schema: `{entries: [{ts, level, channel, message}], cursor}` — supports cursor-based incremental reads on poll.
- **`workspace`**: canonical root + listing.
  Schema: `{root: "/abs/path", entries: [{name, type, size, mtime, is_dir}], parent?}`.
- **`readiness`**: `{state: "pending"|"ready"|"failed", reason?, marker_ts?}` — derived from `run_manifest.json`, `run_checkpoint.json`, `session_checkpoint.json`, `sync_ready.json`. Drives the UI's "still loading…" vs "ready to render" state without any client polling heuristics.

### 3. Registration

`api/server.py` mounts the new router. `viewer_routes` is registered
before legacy session/file routes; the legacy routes stay (Track C will
remove them after the migration is complete).

## Frontend changes

### 1. `/dashboard/viewer/[targetKind]/[targetId]` — new route

Replaces the role of root `/?session_id=...&run_id=...&workspace=...`.
The component:

1. Reads `targetKind` + `targetId` from the route.
2. Calls `GET /api/viewer/hydrate?target_kind=...&target_id=...`.
3. Renders the three panels from the server response.
4. Polls hydrate every 2s while `readiness.state === "pending"` (driven
   by the backend's readiness signal — no client-side heuristics).
5. Once ready, attaches the websocket for live updates if
   `is_live_session === true`.

The existing root-page viewer (`app/page.tsx`) **stays in place** during
migration as a deprecated alias. It internally calls `/api/viewer/resolve`
on load and hard-redirects to the new route. After all producers have
migrated, root-page viewer code is deleted.

### 2. New helper: `web-ui/lib/viewer/openViewer.ts`

```ts
type OpenViewerInput = {
  session_id?: string | null;
  run_id?: string | null;
  workspace_dir?: string | null;
  workspace_name?: string | null;
  attachMode?: "default" | "tail";
  role?: "writer" | "viewer";
};

export async function openViewer(input: OpenViewerInput): Promise<void> {
  const target = await resolveSessionViewTarget(input);
  if (!target) {
    showToast({
      message: "Could not resolve a viewer target for this item.",
      level: "error",
    });
    return;
  }
  const url = new URL(target.viewer_href, window.location.origin);
  if (input.attachMode === "tail") url.searchParams.set("attach", "tail");
  if (input.role === "viewer") url.searchParams.set("role", "viewer");
  window.open(url.toString(), CHAT_WINDOW_NAME)?.focus();
}
```

This is the **single function every producer calls**. No more URL building
in component files. `resolveSessionViewTarget` posts to
`/api/viewer/resolve` and returns the typed `SessionViewTarget`.

### 3. Producers migrated to `openViewer()`

All five producers replace their inline URL building:

| Producer | Current call | New call |
|---|---|---|
| Task Hub completed card (`todolist/page.tsx:1057`) | `openOrFocusChatWindow({ ...target, attachMode: "tail", role: "viewer" })` | `openViewer({ session_id: item.assigned_session_id, run_id: item.canonical_execution_run_id, workspace_name: item.links?.workspace_name, attachMode: "tail", role: "viewer" })` |
| Sessions dashboard row (`SessionsPage.tsx:345`) | inline window.open with hand-built URL | `openViewer({ session_id })` |
| Chat dashboard run-only (`dashboard/chat/page.tsx:94`) | inline with `run_id` only | `openViewer({ run_id })` |
| Calendar event (`CalendarPage.tsx:653`) | `window.location.href = "/dashboard/sessions?sid="...` | `openViewer({ session_id })` |
| Proactive history (`proactive-task-history/page.tsx:248`) | inline | `openViewer({ session_id, run_id })` |

After the migration, **delete**:

- `web-ui/lib/chatWindow.ts` (replaced by `openViewer.ts`)
- `web-ui/lib/taskWorkspaceTarget.ts` (resolution is server-side now)
- The client-side log/trace parsers in `app/page.tsx:1855` (replaced by hydration endpoint)
- The mixed `sid` / `session_id` reader code (single canonical source on viewer route)

## Migration order

The five producers are migrated **one at a time**, each as its own commit.
Each migration:

1. Replaces the producer's inline URL-building with `openViewer(...)`.
2. Adds a Playwright test that clicks the producer's link and asserts the
   three panels populate.
3. Ships independently via `/ship`.

Order chosen by impact:

1. **Task Hub completed card** — most visible bug today, biggest payoff.
2. **Sessions dashboard row** — second-most-used path.
3. **Calendar event** — drops the legacy `?sid=` emitter.
4. **Proactive history** — low-traffic but high-value for ops audits.
5. **Chat dashboard run-only** — fixes the run-only-viewer broken state.

After all five are migrated, a final cleanup commit deletes the legacy
helpers and the root-page viewer fallback. Total: **8 commits over Track
B**, each independently shippable.

## Backwards compatibility

- The root-page viewer (`/?session_id=...`) keeps working for the entire
  migration. It internally calls `/api/viewer/resolve` and hard-redirects
  to the new route on load. Removed only in the cleanup commit, after
  every producer is migrated.
- `?sid=` URLs from existing bookmarks keep working. The Track A change to
  SessionsPage already accepts both; the new resolver also accepts both.
- The legacy file/log/trace endpoints stay in place during migration. The
  hydration endpoint is additive. Old endpoints are removed in the cleanup
  commit.

## Test matrix

### Backend unit tests (`tests/viewer/`)

- `test_resolver_run_id.py`: resolver returns correct target for known run_id.
- `test_resolver_session_id_with_runs.py`: session_id with multiple runs returns the latest.
- `test_resolver_session_id_live.py`: live daemon session resolves via `active_run_workspace`.
- `test_resolver_session_id_archived.py`: archived daemon session resolves via glob.
- `test_resolver_workspace_dir.py`: reverse lookup from path.
- `test_resolver_workspace_name.py`: lookup from basename.
- `test_resolver_returns_none_when_unknown.py`: bad inputs return None, never raise.
- `test_resolver_source_field.py`: `source` field correctly identifies the resolution branch.

### Backend API tests

- `test_viewer_resolve_endpoint.py`: 200/404 cases, viewer_href shape.
- `test_viewer_hydrate_pending.py`: returns `readiness=pending` before
  markers exist, with a `reason` populated.
- `test_viewer_hydrate_ready.py`: returns `readiness=ready` with all
  three panels populated when workspace artifacts land.
- `test_viewer_hydrate_failed.py`: returns `readiness=failed` for known
  failure markers.
- `test_viewer_hydrate_run_only.py`: works with target_kind=run when no
  session_id exists (the case that's broken today).
- `test_viewer_hydrate_no_card_data.py`: confirms hydration response
  never includes anything that could be card-like (paranoia carried over
  from Link payments work).

### Frontend tests

- `taskWorkspaceTarget.test.ts`: keep the existing tests as a regression
  guard, even though the helper is going away — Track A's daemon_ guard
  must not regress before the helper is deleted.
- `openViewer.test.ts`: mock `/api/viewer/resolve`, verify the helper
  posts the correct body, opens the right URL, handles 404 gracefully.
- Playwright `test_task_hub_workspace.spec.ts`: end-to-end click from a
  completed card → all three panels populate.
- Playwright `test_sessions_run_only.spec.ts`: open run-only viewer from
  the sessions dashboard, verify files panel populates (the case that's
  fully broken today).

## Open questions for you to react to

1. **Route shape**: I picked
   `/dashboard/viewer/[targetKind]/[targetId]` (e.g.
   `/dashboard/viewer/run/run_abc123` and
   `/dashboard/viewer/session/daemon_simone_todo`). Alternative:
   `/dashboard/viewer/[id]` with a discriminator query param. My pick is
   the first because it's bookmarkable + more REST-y. **OK with that?**

2. **Legacy URL sunset**: How long do `?sid=` and root `/?session_id=`
   URLs need to keep working? My default: **forever-redirect to the new
   route**, since the cost is one extra hop and the benefit is no broken
   bookmarks. Alternative: hard-deprecate after 30 days. **Your pick?**

3. **Hydration shape**: One fat endpoint (`/hydrate` returns all four
   sections) vs. four lean endpoints (`/hydrate/history`, `/hydrate/logs`,
   etc.). Fat endpoint is simpler for the UI and matches the three-panel
   layout exactly. Lean endpoints support partial refresh and lazy
   loading, but the panels are always shown together so there's no real
   benefit. **My pick: fat endpoint. Confirm?**

4. **Polling vs. websocket for readiness**: My default is the UI polls
   `/hydrate` every 2s while `readiness.state === "pending"`, then stops
   and switches to the existing chat websocket once ready. Alternative:
   add a readiness websocket. Polling is simpler and 2s is fine. **Your
   pick?**

5. **Migration ordering**: I proposed Task Hub → Sessions → Calendar →
   Proactive → Chat. Is there a producer you'd rather see fixed first,
   or one you'd rather defer to last because you don't use it?

6. **Cleanup scope**: After all five producers are migrated, the cleanup
   commit deletes `chatWindow.ts`, `taskWorkspaceTarget.ts`, the
   client-side trace/log parsers, and the root-page viewer fallback. Are
   any of those used by something I haven't found that you want to keep?

7. **Scope of this Track B work**: I've spec'd the full migration
   (resolver + endpoint + route + 5 producers + cleanup = 8 commits).
   Want me to do all 8, or stop after the resolver + endpoint + route +
   first 1-2 producers and re-evaluate?

## Effort estimate

- Backend resolver + hydration endpoint + tests: ~400 lines, 1 commit
- New viewer route + openViewer helper + tests: ~250 lines, 1 commit
- Per-producer migration: ~30-50 lines + 1 Playwright test each, 5 commits
- Final cleanup: ~150 lines deleted, 1 commit

**Total: ~8 commits, ~1500 lines net (with significant deletions).** Roughly the same scope as Phase 2b of the Link payments work.

---

**React to the open questions above and I'll start coding.** I'd
recommend doing the backend resolver + hydration endpoint as the first
commit since the rest depends on it; once that's in I can proceed
producer-by-producer at your pace.
