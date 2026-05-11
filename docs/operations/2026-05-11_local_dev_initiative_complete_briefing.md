# Local Dev / Prod Separation Initiative — Handoff Briefing for Other AI Coder

**Date:** 2026-05-11 (post-shipment)
**Status:** ✅ Complete and deployed. Production running on the new model since `e928a5a1`.
**Audience:** The other AI coder who was working in parallel during this initiative.

> This is the **post-ship** handoff. If you read the earlier in-flight briefing at `docs/operations/2026-05-11_local_dev_initiative_briefing.md`, this supersedes it. Everything is shipped.

---

## TL;DR — what changed

**Old model:** Development happened on the VPS (`/home/ua/dev/universal_agent`) via Antigravity Remote-SSH. Running a local desktop dev environment conflicted with the always-on VPS autonomous loops (ZAI quota, AgentMail inbox, etc.), so desktop dev wasn't practical.

**New model (now canonical):**
- **VPS** = production only, always-on. All autonomous loops tick. Real Infisical `production` secrets. Deploy via GitHub Actions on merge to `main`.
- **Desktop** = local dev, on-demand. Operator runs `just dev`, all autonomous loops are **off** by default, zero ZAI quota burn at idle, zero collision with prod state. Same code as prod. Separate dev SQLite DB.

---

## Concrete workflow you should now use

### Daily work (desktop)

```bash
cd /home/kjdragan/lrepos/universal_agent
git fetch origin && git pull --ff-only origin main
uv sync
just dev
```

This boots gateway (`:8002`) + API (`:8001`) + web-ui Next.js (`:3000`) in parallel with prefixed output. Ctrl-C tears down. **No heartbeat ticking, no cron firing, no real emails, no Claude Agent SDK iterations.**

### To test a specific autonomous loop in dev

Set `UA_DEV_<NAME>_FORCE_ON=1` in your local `.env` and restart `just dev`. Examples:

```bash
echo "UA_DEV_HEARTBEAT_FORCE_ON=1" >> .env
just dev   # Now heartbeat ticks; everything else stays off
```

Available dev opt-in vars (one per loop): see [`docs/06_Deployment_And_Environments/12_Local_Dev_Environment.md`](../06_Deployment_And_Environments/12_Local_Dev_Environment.md) § Triggering autonomous loops manually.

### To inspect what's gated without booting services

```bash
PYTHONPATH=src python -m universal_agent.dev_tools env-report      # per-loop dev decisions
PYTHONPATH=src python -m universal_agent.dev_tools loop-status heartbeat
PYTHONPATH=src python -m universal_agent.dev_tools cron-list       # persisted cron jobs
```

### To pull realistic prod data for local debugging

```bash
python scripts/snapshot_prod_to_dev.py                # actual
python scripts/snapshot_prod_to_dev.py --dry-run      # preview
```

Uses SQLite's online `.backup` over SSH (no prod pause). Refuses to run when `UA_RUNTIME_STAGE=production`. UA is 100% SQLite — confirmed; no Postgres anywhere.

### Shipping is unchanged

```bash
/ship    # from any branch — opens PR to main, enables auto-merge
```

PR-Validate CI runs; merge fires `deploy.yml`. Same as before.

---

## What changed in the codebase

### New module: `src/universal_agent/loop_control.py`

Centralized "should this autonomous loop run?" decision. Functions:
- `should_run_loop(name, prod_default=True)` — returns bool
- `explain_loop_decision(name)` — human-readable diagnostic
- `report_dev_overrides()` — startup log of per-loop decisions in dev
- `is_development_runtime()` — true iff `UA_RUNTIME_STAGE=development`

### CRITICAL behavior change — dev mode is defensive

In `UA_RUNTIME_STAGE=development`:

| Env var pattern | Effect |
|---|---|
| `UA_DEV_<NAME>_FORCE_ON=1` | Turns loop ON (canonical dev opt-in) |
| `UA_<NAME>_ENABLED=0` (or `false`/`no`/`off`) | Turns loop OFF (operator explicit) |
| `UA_<NAME>_ENABLED=1` (or `true`/`yes`/`on`) | **IGNORED** — defensive against Infisical prod-parity injection |
| Nothing set | OFF (dev default) |

In production / unset stage, original semantics — `UA_<NAME>_ENABLED` honored as before.

**Why the defensive layer:** Infisical's `development` environment mirrors many `UA_*_ENABLED=1` flags from production. Before Phase D, those mirrored flags would force loops ON in dev. Phase D ignores them and requires `UA_DEV_<NAME>_FORCE_ON=1` instead. **Do not rely on `UA_HEARTBEAT_ENABLED=1` to turn heartbeat on in dev — it won't work.**

### New CLI: `python -m universal_agent.dev_tools`

Subcommands: `env-report`, `loop-status <name>`, `cron-list [--workspace PATH]`. Inspection only; no live triggers.

### New script: `scripts/snapshot_prod_to_dev.py`

SQLite snapshots from prod via SSH + sqlite3 `.backup`. Refuses to run in production. Operator-only (uses your SSH keys).

### New `justfile` at repo root

Recipes: `dev`, `dev-gateway`, `dev-webui`, `bootstrap`, `dev-kill`, `test`, `lint`, `format`, `preship`.

### Defensive isolation in `cron_service.py`

In dev mode, CronService doesn't load persisted `cron_jobs.json` even if it gets instantiated somehow — belt-and-suspenders against the 53+ persisted prod cron jobs ticking on a dev box.

### Gates added across the codebase

Master gates now exist for: heartbeat (entire service), cron service (entire service), cron registration, idle dispatch loop, dispatch stale sweep, daemon sessions, VP event bridge, VP stale reconciler, AgentMail service, notification dispatcher, YouTube playlist watcher, HQ self-heartbeat.

---

## Anti-patterns to avoid

1. **Don't use Antigravity Remote-SSH for daily dev.** It's now the fallback path for when desktop isn't available (e.g., traveling, hardware issue). Doc 11 retains that workflow with a banner clarifying it's no longer canonical.

2. **Don't set `UA_HEARTBEAT_ENABLED=1` / `UA_CRON_ENABLED=1` in your dev `.env` expecting them to turn loops on.** They're ignored in dev. Use `UA_DEV_HEARTBEAT_FORCE_ON=1` / `UA_DEV_CRON_FORCE_ON=1`.

3. **Don't assume dev DB is Postgres.** UA is 100% SQLite end-to-end. There was never a Postgres path; Phase F confirmed this.

4. **Don't put real credentials in `.env`.** The bootstrap pattern: tiny `.env` at repo root has ONLY 4 Infisical bootstrap fields (`INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`, `INFISICAL_PROJECT_ID`, `INFISICAL_API_URL`). Everything else comes from Infisical's `development` env.

5. **Don't run heartbeat / cron / agent loops in your dev session and assume "it's like prod."** They're off by design. To test loop behavior, opt in one loop at a time via `UA_DEV_<NAME>_FORCE_ON=1`.

---

## Canonical docs

| Doc | What it covers |
|---|---|
| `docs/06_Deployment_And_Environments/12_Local_Dev_Environment.md` | **CANONICAL.** Full runbook: bootstrap, daily workflow, loop opt-in pattern, troubleshooting, prod snapshot |
| `docs/06_Deployment_And_Environments/13_Infisical_Dev_Env_Hygiene.md` | Optional Infisical cleanup (remove `UA_*_ENABLED` flags from dev env) |
| `docs/WORKFLOW.md` | One-page operator index (leads with `just dev` now) |
| `docs/06_Deployment_And_Environments/05_Local_Runtime_Modes.md` | Lane definitions (HQ Dev vs Desktop Worker) — Doc 12 is the runbook |
| `docs/06_Deployment_And_Environments/11_Daily_Dev_Workflow.md` | **Fallback** path: VPS-as-dev via Antigravity Remote-SSH |
| `CLAUDE.md` § Working Rules | First bullet now says "local dev happens on Kevin's desktop, not the VPS" |

---

## Shipped via these PRs (all merged to `main`)

| PR | Title | What it did |
|---|---|---|
| #199 | docs: local dev environment contract (draft) | Wrote Doc 12 contract |
| #200 | feat(local-dev): loop_control master switch + justfile | Created `loop_control.py`, `justfile`, refactored 5 loops |
| #202 | feat(local-dev): Phase C.2 — gate remaining services | Heartbeat/cron/agentmail/notification/youtube master gates |
| #204 | fix(deploy): detect Python version mismatch in venv | Unrelated — fixed a deploy infrastructure bug that surfaced during the work |
| #206 | feat(local-dev): Phase D — dev-safe loop_control | The defensive layer (ignore Infisical injection of `UA_*_ENABLED=1` in dev) |
| #211 | feat(local-dev): Phase E + F combined | `dev_tools` CLI, `snapshot_prod_to_dev.py`, Doc 12 → Canonical |
| #212 | docs: post-initiative drift sweep | Updated CLAUDE.md, WORKFLOW.md, Doc 11, Doc 05, etc. to match new reality |
| #201/#203/#205/#209 | chore(memory): session snapshots | Routine state snapshots; no functional impact |

Production verified healthy on the new SHA via `GET /api/v1/version`.

---

## If you were mid-work on something during the initiative

Most likely impact areas — check whether anything you were touching changed:

- `src/universal_agent/heartbeat_service.py` — `DEFAULT_HEARTBEAT_AUTONOMOUS_ENABLED` now uses `should_run_loop`
- `src/universal_agent/cron_service.py` — added dev-mode skip of `cron_jobs.json` load
- `src/universal_agent/feature_flags.py` — `heartbeat_enabled()` and `cron_enabled()` rewritten for dev/prod split
- `src/universal_agent/gateway_server.py` — added gates around AgentMail service, YouTube watcher, HQ self-heartbeat, cron registration; new `report_dev_overrides()` call at lifespan startup
- `src/universal_agent/services/idle_dispatch_loop.py`, `dispatch_service.py`, `daemon_sessions.py` — refactored to use `should_run_loop`
- `scripts/bootstrap_local_hq_dev.sh` — prints "loops silenced" banner at end of bootstrap

If your work touched any of these and you need to rebase, the diff is small. If you have questions about how something now works, run `python -m universal_agent.dev_tools loop-status <name>` to get the gate's current decision + reasoning.

---

## Quick sanity check — am I in dev mode?

```bash
echo "$UA_RUNTIME_STAGE"   # should be "development" in dev, "production" on VPS, empty in fresh shells
```

If you're seeing autonomous loops firing in dev despite the gates above, something injected `UA_RUNTIME_STAGE=production` or you missed `UA_RUNTIME_STAGE=development` in `.env`. Run `bash scripts/bootstrap_local_hq_dev.sh` to re-sync.
