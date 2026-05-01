# /ship Handoff — Pending Commits on `feature/latest2`

> **Purpose**: Tell the next AI coder (or human operator) exactly what work
> is sitting on `feature/latest2` waiting for a `/ship`, and what each
> commit does, so the correct deploy can be triggered.

## Current state (as of this handoff)

- **Branch**: `feature/latest2`
- **Local + origin HEAD**: `89ef647`
- **origin/main HEAD**: `6a280c8`
- **Commits ahead of main**: 22 (mix of feature work + 4 prior `/ship`
  auto-commits that didn't make it through to main, plus 2 PR merges and
  2 unrelated fixes by other contributors).

## How to deploy

From `feature/latest2` working tree:

```
/ship
```

That single command (per `docs/HOW_TO_USE_SHIP.md`) handles:

1. Auto-commits any pending uncommitted changes on `feature/latest2`.
2. Pushes `feature/latest2`.
3. Merges `feature/latest2` → `develop`.
4. Fast-forwards `main` to `develop`.
5. Pushes `main`, which triggers the GitHub Actions production deploy.
6. Returns the working tree to `feature/latest2`.

**Do not** run `git merge`, `git push origin main`, `ssh`, or `rsync` by
hand. Use `/ship`.

## What's pending — two complete feature lines + cleanup

### 1. Stripe Link payments — Phases 3 & 4 (already complete, awaiting ship)

Built earlier in this session; some Phase 3/4 commits are sitting on
`feature/latest2` that have not yet propagated to `main`.

| Commit | Subject |
|---|---|
| `b7d2505` | Phase 4 — agent-purchaser sub-agent + checkout orchestration |
| `9c71c6e` | Phase 3 — MPP decode + pay routes + live-mode runbook |
| `ab50a13` | docs(link): canonical 013 integration doc |

**Master switch (`UA_ENABLE_LINK`) defaults OFF** — these commits are
fully inert in production until an operator provisions Link CLI auth in
Infisical and flips the flag. Safe to ship without any operator action.

Documentation:
- `docs/013_LINK_PAYMENTS_INTEGRATION.md` (canonical architecture + demo)
- `docs/link_payments_runbook.md` (operator runbook: bootstrap → live)

### 2. Three-Panel Viewer Centralization — Track A + Track B (NEW)

Eight commits (plus Track A) that fix the long-standing fragmentation in
the three-panel viewer's URL/identity contract. **Includes the immediate
production-bug fix for Task Hub workspace links.**

| Commit | Subject |
|---|---|
| `f9dcc65` | Track A — unbreak Task Hub workspace links (delete daemon_ strip + accept session_id on SessionsPage) |
| `5a9d0f3` | docs — Track B implementation spec |
| `2baf98a` | Track B Commit 1 — backend resolver + hydration + routes (50+ tests) |
| `989ff84` | Track B Commit 2 — openViewer helper + new viewer route |
| `2f1c32b` | Track B Commit 3 — migrate Task Hub workspace buttons (3 sites) |
| `728dc62` | Track B Commit 4 — migrate Sessions dashboard viewer paths |
| `0b22123` | Track B Commit 5 — migrate Calendar open_session action |
| `2fe6173` | Track B Commit 6 — migrate Proactive history rehydrate |
| `7ff6320` | Track B Commit 7 — migrate Chat dashboard run-only viewer |
| `89ef647` | Track B Commit 8 — completion notes + e2e contract test |

Track A is the **most important to ship** of all the pending work — it
fixes a daemon-id stripping bug that was silently breaking every Task Hub
"Workspace" link in production.

Documentation:
- `docs/three_panel_viewer_track_b_spec.md` (status banner now lists all
  shipped commits and what's deferred to a possible Track C)

## Files added in pending commits

### Backend (Python)

```
src/universal_agent/viewer/__init__.py            (new package)
src/universal_agent/viewer/resolver.py            (Track B C1)
src/universal_agent/viewer/hydration.py           (Track B C1)
src/universal_agent/api/viewer_routes.py          (Track B C1)
src/universal_agent/api/server.py                 (edit: register viewer router)

src/universal_agent/tools/link_bridge.py          (Phase 3 edit: mpp_decode)
src/universal_agent/api/link_routes.py            (Phase 3 edit: /mpp/* + /checkout)
src/universal_agent/services/link_purchaser.py    (Phase 4: orchestration)
.claude/agents/agent-purchaser.md                 (Phase 4: sub-agent)
docs/link_payments_runbook.md                     (Phase 3: runbook)
docs/013_LINK_PAYMENTS_INTEGRATION.md             (Phase 3 doc)
```

### Frontend (TypeScript / Next.js)

```
web-ui/lib/viewer/types.ts                                          (Track B C2)
web-ui/lib/viewer/openViewer.ts                                     (Track B C2)
web-ui/lib/viewer/openViewer.test.ts                                (Track B C2)
web-ui/app/dashboard/viewer/[targetKind]/[targetId]/page.tsx        (Track B C2)

web-ui/lib/taskWorkspaceTarget.ts                                   (Track A edit)
web-ui/lib/taskWorkspaceTarget.test.ts                              (Track A edit)
web-ui/components/dashboard/SessionsPage.tsx                        (Track A + C4 edits)
web-ui/components/dashboard/CalendarPage.tsx                        (C5 edit)
web-ui/app/dashboard/todolist/page.tsx                              (C3 edit)
web-ui/app/dashboard/proactive-task-history/page.tsx                (C6 edit)
web-ui/app/dashboard/chat/page.tsx                                  (C7 edit)
```

### Tests (newly added)

```
tests/viewer/__init__.py
tests/viewer/test_viewer_resolver.py     (Track B C1)
tests/viewer/test_viewer_hydration.py    (Track B C1)
tests/viewer/test_viewer_routes.py       (Track B C1)
tests/viewer/test_viewer_e2e.py          (Track B C8)
```

### Documentation

```
docs/three_panel_viewer_track_b_spec.md
docs/013_LINK_PAYMENTS_INTEGRATION.md
docs/link_payments_runbook.md
SHIP_HANDOFF.md (this file)
```

## Risk assessment

- **Track A daemon-strip fix**: behavior change — Task Hub workspace
  buttons that previously dropped session_id now include it. The unit
  tests in `web-ui/lib/taskWorkspaceTarget.test.ts` already assert the
  correct behavior; this commit just makes the implementation honor
  what the tests already required.
- **Track B Commits 1–2**: pure additions (new package, new route, new
  tests). No existing producer references the new route yet, so behavior
  is unchanged until producer migrations land.
- **Track B Commits 3–7**: each producer call site is migrated
  individually. Per-commit rollback is clean if any one site misbehaves.
  Writer-mode (live WebSocket attach) was deliberately NOT migrated —
  those paths still use the legacy `chatWindow.ts` + root viewer.
- **Track B Commit 8**: docs + e2e test. Zero runtime change.
- **Link Phase 3/4**: master switch (`UA_ENABLE_LINK`) defaults OFF.
  Inert until operator-provisioned in Infisical.

## Smoke test checklist for after `/ship`

After production deploy completes, verify:

1. `GET https://app.clearspringcg.com/api/viewer/health` returns 200 with
   `{"ok": true, "subsystem": "viewer"}`.
2. From the Task Hub dashboard, click "Workspace" on any completed card.
   Expected: opens `/dashboard/viewer/<kind>/<id>` with all three panels
   populated (history, logs, files). **Previously this was silently
   broken** because `taskWorkspaceTarget.ts` was stripping `daemon_*`
   session ids.
3. From the Sessions dashboard, click any archived run-only row. Expected:
   opens the new viewer route with the run's workspace files visible.
   **Previously this was structurally broken** because the legacy file
   helpers required session_id and returned empty for run-only URLs.
4. From the Calendar, click "Open session" on any session-bearing event.
   Expected: opens the new viewer route, not the sessions list.
5. Link payments stays inert: `curl /api/link/health` returns
   `bridge_status.enabled: false` (master switch off).

## What's NOT in this handoff (NOT pending ship)

- **Writer-mode WebSocket migration**: live chat continues on legacy root
  page (`/?session_id=...`). A possible "Track C" effort would migrate
  this to the new viewer route. Not in scope for current `/ship`.
- **Live-mode Link payment activation**: requires manual operator
  provisioning in Infisical (`scripts/bootstrap_link_auth.sh`). Not
  bundled into the `/ship`.

## When you `/ship`

You will deploy:
- All 22 commits ahead of `main` (this includes the 2 Phase 3/4 Link
  commits, the Track A bleed-stopper, the 8 Track B commits, plus the
  earlier deploy auto-commits and other contributors' work that's
  already on the branch).
- The two unrelated `fix:` and `refactor:` commits already on
  `feature/latest2` from other contributors.

Once shipped, this handoff document can be deleted (or archived) — its
purpose is to bridge between AI sessions, not to live in the repo
permanently.
