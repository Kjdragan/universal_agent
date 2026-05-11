# Local Dev / Prod Separation Initiative — Cross-Session Briefing

**Date:** 2026-05-11
**Status:** Active initiative; contract PR (#199) merged 2026-05-10; implementation PR in flight.
**Author:** Claude session in Kevin's cloud sandbox
**Audience:** Any other AI session or operator working in the same repo concurrently

> **Purpose of this doc:** Kevin frequently has multiple Claude sessions or AI agents active on this repo. This briefing tells you (the reader) what's in flight, what's safe to keep doing, and what to coordinate on, so we don't step on each other.

---

## What's happening

Kevin and the cloud-sandbox Claude are formalizing a dev-vs-prod separation that has been implicit but never written down. The shift:

- **VPS (`uaonvps`)** = **production only, always-on.** Autonomous loops (heartbeat / cron / dispatch sweep / AgentMail polling / X polling) keep ticking. Real Infisical `production` secrets. Real Postgres.
- **Kevin's desktop (`/home/kjdragan/lrepos/universal_agent`)** = **local dev, on-demand.** Same code, same routes, same UI, but autonomous loops are **off**. Spun up via a single command (`just dev`) when working, killed when done. Separate dev SQLite. Infisical `development` env. Zero ZAI quota burn at idle.

The motivation: treating the VPS as both the dev box and the prod box makes prod issues feel like emergencies and makes the dev cycle slow (commit → push → CI → deploy → restart → curl prod). The fix is to move dev back to the desktop with the autonomous loops off, so dev never collides with prod's ZAI concurrency or state.

The canonical contract is in [`docs/06_Deployment_And_Environments/12_Local_Dev_Environment.md`](../06_Deployment_And_Environments/12_Local_Dev_Environment.md).

---

## What's shipped (PR #199 — merged 2026-05-10)

A **doc-only** PR: `docs/06_Deployment_And_Environments/12_Local_Dev_Environment.md` — the contract for what local dev is, with bootstrap steps, ON/OFF tables, troubleshooting, and an explicit § Audit pending punch list.

- Merged 2026-05-10 as squash commit `be2df582` on `main`.
- No code touched.
- `paths-ignore: docs/**` on `deploy.yml` meant the merge did NOT trigger production deploy.

## What's in flight (implementation PR)

The follow-up PR closes the most critical items from § Audit pending:

- **New module: `src/universal_agent/loop_control.py`** — centralized "should this loop run?" decision. In `UA_RUNTIME_STAGE=development`, every loop defaults OFF; explicit `UA_<NAME>_ENABLED` always wins. 33 unit tests cover it.
- **Refactored autonomous-loop gates** to use `loop_control.should_run_loop()`: heartbeat_service, idle_dispatch_loop, dispatch_service (stale sweep), daemon_sessions, gateway_server (vp_event_bridge, vp_stale_reconcile, cron_registration master switch).
- **New `justfile` at repo root.** `just dev` runs gateway + api + web-ui with prefixed output and clean Ctrl-C teardown. Companion recipes: `dev-gateway`, `dev-webui`, `bootstrap`, `dev-kill`, `test`, `lint`, `format`, `preship`.
- **Doc update:** marks completed § Audit pending items, refines the "trigger a single loop manually" guidance to the two patterns that actually work today (Pattern A: flip individual flag in `.env`; Pattern B: deferred — single-iteration CLIs).

## What's NOT shipping yet (deferred to later PRs)

These are non-blockers:

1. Single-iteration CLI entry points (`python -m universal_agent.heartbeat tick` etc.). Workaround until then: flip the individual loop's env flag in `.env`.
2. Infisical `development` env completeness verification. Operator-side check; can only be done by reading what's in Infisical.
3. `snapshot_prod_to_dev.py` script. Only useful when you specifically want realistic data shape locally.
4. `bootstrap_local_hq_dev.sh` end-to-end freshness verification. Manual desktop run required.

The § Audit pending section of the contract doc is the authoritative living punch list.

---

## What's safe for you to keep doing

- **Application-code work** anywhere outside the files listed in § What to coordinate on.
- **Bug fixes** on `main` or feature branches via the normal PR flow.
- **Tests / refactors** that don't touch dev-environment plumbing.
- **Anything that ships through the normal PR-to-`main` flow** — the deploy path is unchanged.

---

## What to coordinate / pause on

These files were touched in the implementation PR. If you're actively editing any of them, **flag it to Kevin so we can sequence**:

- `justfile` (new at repo root)
- `src/universal_agent/loop_control.py` (new module)
- `src/universal_agent/heartbeat_service.py` (import + 1 constant)
- `src/universal_agent/services/idle_dispatch_loop.py` (import + 1 constant)
- `src/universal_agent/services/dispatch_service.py` (import + `_stale_sweep_enabled`)
- `src/universal_agent/services/daemon_sessions.py` (`daemon_sessions_enabled`)
- `src/universal_agent/gateway_server.py` (import + 2 module-level flags + cron-registration master gate)
- `tests/unit/test_loop_control.py` (new)
- `docs/06_Deployment_And_Environments/12_Local_Dev_Environment.md` (§ Audit pending + § Triggering loops)
- `docs/operations/2026-05-11_local_dev_initiative_briefing.md` (this doc)
- `docs/README.md`, `docs/Documentation_Status.md` (index entries)

---

## Production stability guarantees

- **In production (`UA_RUNTIME_STAGE=production`) every loop behaves exactly as before.** `loop_control.should_run_loop` returns the same answer the old per-loop env-var read would have returned. Zero behavior change in prod.
- **Explicit `UA_<NAME>_ENABLED` flags still work as overrides.** Backward compat preserved.
- **The cron-registration master switch defaults ON for production.** No cron schedules get dropped on the VPS.
- **The deploy pipeline is untouched.** No changes to `.github/workflows/deploy.yml` or `pr-validate.yml`.

If anything in this initiative threatens prod, it's bug-class — flag it immediately.

---

## Verification gates the implementation PR clears

- `uv run --frozen pytest tests/unit -q` → all green (33 new + existing suite)
- `uv run --frozen ruff check src/universal_agent/loop_control.py tests/unit/test_loop_control.py` → clean
- Smoke-import: every refactored module loads without error.
- `python3 -c "import ast; ast.parse(open('src/universal_agent/gateway_server.py').read())"` → syntax OK.

---

## When/how to be looped back in

When the implementation PR merges and Kevin verifies `just dev` on his desktop end-to-end, the next phase is the deferred items (single-iteration CLIs, dev DB story confirmation, snapshot script). Those will land as separate small PRs.

Until then: keep working, flag any file overlap, and assume environment plumbing is "in flight, hands off."

For background reading:

- **[Contract doc (this initiative)](../06_Deployment_And_Environments/12_Local_Dev_Environment.md)** — the canonical "what local dev is" definition
- **[Lane definition](../06_Deployment_And_Environments/05_Local_Runtime_Modes.md)** — HQ Dev Lane vs Desktop Worker Lane
- **[Branch + deploy model](../06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md)** — what happens after PR merge
- **[Claude execution profiles](../06_Deployment_And_Environments/10_Interactive_Coding_Environment.md)** — which Claude runs where, OAuth vs ZAI

---

## Operator's running tab of related context

- Earlier in this 2026-05-11 session, the operator and cloud-sandbox Claude worked through why VPS-as-dev was painful: ZAI single-concurrent-session constraint, lack of local repro, slow iteration. The conclusion was the dev/prod separation captured here.
- Pre-2026-05-10 the workflow was `feature/latest2 → develop → main → Deploy`. `develop` was retired 2026-05-10 (PR #181); model is now `<any-branch> → PR → main → Deploy`.
- Auto-merge is enabled for the repo; PRs to `main` queue and merge when CI is green.
- PR #199 (contract doc) merged 2026-05-10 as commit `be2df582`. The implementation PR follows the same flow.

If anything in this briefing contradicts what you're actively working on, **escalate to Kevin** rather than silently reconciling.
