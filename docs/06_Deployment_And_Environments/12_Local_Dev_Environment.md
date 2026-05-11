# 12. Local Development Environment — The Contract

> **Audience:** Kevin (primary), and any future agent/operator who needs to run UA locally for development.
>
> **Status:** Canonical (2026-05-11) — Phase F shipped. Contract is implemented end-to-end across PRs #199 (doc), #200 (loop_control + justfile), #202 (Phase C.2 service gates), #206 (Phase D dev-safe semantics), #207 (Phase E dev_tools CLI), Phase F (this PR: snapshot script + canonization). Local dev now defaults safely to no autonomous loops, no real emails, no Google API calls, no Claude Agent SDK iterations — regardless of Infisical-injected pollution.
>
> **TL;DR:** Your **desktop** runs UA on demand for development. The **VPS** runs UA always-on for production. They never share state. The desktop dev stack has the same code, the same routes, and the same UI as prod — but the autonomous loops (heartbeat, cron, dispatch sweep, etc.) are **off**. You spin it up when working, kill it when done. One command starts everything: `just dev`.

---

## The contract (one paragraph)

Local dev is the same code prod runs, with the **HTTP/UI surface alive** and the **autonomous loops off**. It runs on Kevin's desktop at `/home/kjdragan/lrepos/universal_agent`, talks to a **separate dev database** (never prod), and pulls all secrets from **Infisical's `development` environment** (no secret sprawl in `.env` files). It is **on demand** — you start it when you're working, you kill it when you stop. It cannot collide with prod because it does not tick.

---

## ON in local dev / OFF in local dev

| Surface | Local dev | Prod (VPS) |
|---|---|---|
| **HTTP gateway** (`:8002`) | ON, hot reload | ON, systemd-managed |
| **API server** (`:8001`) | ON, hot reload | ON, systemd-managed |
| **Web UI** (`:3000`, Next.js) | ON, hot reload | ON, served via web-ui systemd unit |
| **Database** | SQLite at `~/.local/share/universal_agent/dev.db` (fresh per developer) | Postgres (real prod data) |
| **Heartbeat tick loop** | OFF — trigger one tick at a time manually | ON, runs continuously |
| **Cron service registration** | OFF — trigger any cron once manually | ON, all crons registered |
| **Dispatch sweep loop** | OFF | ON |
| **AgentMail polling** | OFF | ON |
| **X / ClaudeDevs intel polling** | OFF | ON |
| **Demo workspace execution** (`/opt/ua_demos/`) | OFF | ON (Simone+Cody produce demos here) |
| **Outbound email / SMS / webhooks** | OFF (or pointed at sinks) | ON (real recipients) |
| **Infisical secrets source** | `development` env | `production` env |
| **Always-on?** | NO — manual lifecycle | YES — systemd auto-restart |

The rule of thumb: **anything that ticks on a timer is OFF in local dev.** Anything that responds to an HTTP request is ON. Triggers for "fire one iteration of a loop" exist for development testing — see § Triggering loops manually.

---

## Architecture: desktop dev vs VPS prod

```
┌────────────────────────────────────────────┐    ┌────────────────────────────────────────────┐
│  DESKTOP (mint-desktop, on-demand)          │    │  VPS (uaonvps, always-on)                  │
│                                              │    │                                              │
│  /home/kjdragan/lrepos/universal_agent       │    │  /opt/universal_agent  ← deploy lands here │
│  ├─ source code (your edits)                 │    │  ├─ source code (clobbered every deploy)   │
│  ├─ .venv (uv-managed)                       │    │  ├─ .venv                                   │
│  ├─ .env (bootstrap creds for Infisical-dev) │    │  ├─ .env (bootstrap creds for Infisical-   │
│  ├─ ~/.local/share/...universal_agent/dev.db │    │  │   prod, via systemd EnvironmentFile)    │
│                                              │    │  ├─ Postgres (real prod state)              │
│  Services (manual lifecycle, via `just dev`) │    │                                              │
│  ├─ gateway     :8002                        │    │  Services (systemd, always-on)              │
│  ├─ api         :8001                        │    │  ├─ universal-agent-gateway.service         │
│  └─ web-ui      :3000  (Next.js dev)          │    │  ├─ universal-agent-api.service             │
│                                              │    │  ├─ universal-agent-webui.service           │
│  Loops:                                       │    │  ├─ heartbeat tick (every Nm)               │
│  └─ NONE running                              │    │  ├─ cron service (registered crons)         │
│     (manual single-tick triggers exist)      │    │  └─ dispatch sweep                           │
│                                              │    │                                              │
│  Infisical env: development                   │    │  Infisical env: production                   │
└────────────────────────────────────────────┘    └────────────────────────────────────────────┘
                  │                                                   ▲
                  │  git push origin <branch>                          │
                  │  PR → CI → merge to main                          │
                  └───────────────────────────────────────────────────┘
                            (the only path that updates prod)
```

---

## Prerequisites (one-time, on the desktop)

### 1. System tools

```bash
# Python package manager (uv) — already installed on your desktop
which uv  # expect: /usr/local/bin/uv or similar

# Node.js (for the web-ui) — already installed
node --version  # expect: v20+ or v22+

# just (Rust-built make replacement)
sudo apt install just
# or:
cargo install just
just --version  # expect: 1.x

# Infisical CLI (optional, for manual debugging — not required for `just dev`)
curl -1sLf 'https://dl.cloudsmith.io/public/infisical/infisical-cli/setup.deb.sh' | sudo -E bash
sudo apt install infisical
infisical --version  # expect: 0.30+ or similar
```

### 2. Bootstrap credentials for Infisical's `development` environment

You need four pieces of information from Infisical, scoped to the `development` environment of the universal_agent project:

| Variable | Where to find it |
|---|---|
| `INFISICAL_CLIENT_ID` | Infisical UI → Project Settings → Access Control → Identity (Universal Auth) → Client ID |
| `INFISICAL_CLIENT_SECRET` | Same identity → Client Secret (one-time visible; rotate if lost) |
| `INFISICAL_PROJECT_ID` | Infisical UI → Project Settings → Project ID |
| `INFISICAL_API_URL` (optional) | Default `https://app.infisical.com`; set if self-hosted |

Identity must have **read access to the `development` environment.** If you only have a prod-scoped identity, create a new dev-scoped one — never reuse prod creds for local dev.

### 3. Repo checkout

```bash
cd /home/kjdragan/lrepos/universal_agent
git fetch origin
git pull --ff-only   # bring local up to current main (or whatever feature branch)
uv sync              # install/refresh Python deps
cd web-ui && npm install && cd ..   # install/refresh node deps
```

---

## Bootstrap: writing the `.env` (one-time, repeatable)

The bootstrap pattern: a tiny `.env` at the repo root contains **only** the four Infisical bootstrap fields. Everything else (API keys, DB URLs, model names, etc.) flows from Infisical at service startup via `initialize_runtime_secrets()`.

### Step 1: create `.env` with bootstrap creds

```bash
cd /home/kjdragan/lrepos/universal_agent

cat > .env <<'EOF'
INFISICAL_CLIENT_ID=<paste-from-infisical-ui>
INFISICAL_CLIENT_SECRET=<paste-from-infisical-ui>
INFISICAL_PROJECT_ID=<paste-from-infisical-ui>
INFISICAL_API_URL=https://app.infisical.com
EOF

chmod 600 .env   # tight perms — this file holds Infisical creds
```

This file is **gitignored**. It never gets committed. It never leaves your desktop.

### Step 2: run the bootstrap script

```bash
bash scripts/bootstrap_local_hq_dev.sh
```

What this does:
1. Reads `INFISICAL_*` from your `.env`.
2. Validates connectivity to Infisical's `development` env via the Python SDK.
3. Rewrites `.env` to include lane settings (`FACTORY_ROLE=HEADQUARTERS`, `UA_RUNTIME_STAGE=development`, `UA_MACHINE_SLUG=kevins-desktop`, etc.) alongside the bootstrap creds.
4. Calls `scripts/install_local_webui_env.sh` to populate `web-ui/.env.local`.
5. Verifies via Python that `initialize_runtime_secrets()` resolves the lane correctly.
6. Prints a "HQ dev bootstrap complete" message.

If this fails, see § Troubleshooting → "Bootstrap fails."

You re-run this only when:
- Your Infisical creds rotate (rare)
- A new dev-environment secret is added in Infisical (the bootstrap pulls fresh values)
- Something in the lane settings drifts and you want to reset

You do NOT re-run this every dev session.

---

## Daily workflow

### Start the dev stack

```bash
cd /home/kjdragan/lrepos/universal_agent
just dev
```

This single command runs all three services (gateway, API, web-ui) in parallel with hot reload. Output streams to your terminal, color-coded by service. Press **Ctrl-C** once to stop everything cleanly.

### Where to hit it

| URL | What it is |
|---|---|
| `http://localhost:3000` | Web UI dashboard |
| `http://localhost:3000/dashboard/corporation` | Corporation/factories view (Doc 05 test page) |
| `http://localhost:3000/dashboard/hackernews` | HN tab |
| `http://localhost:3000/dashboard/todolist` | Task Hub |
| `http://localhost:8002/api/v1/version` | Gateway version endpoint (no auth) |
| `http://localhost:8002/api/v1/health` | Gateway health |
| `http://localhost:8001/...` | API server endpoints |

### Editing code

- **Python changes** (gateway/api): uvicorn reloads automatically on save.
- **Web UI changes** (`web-ui/...`): Next.js reloads automatically on save.
- **Schema changes** (alembic): run migrations manually, see § Database.

### Stop the dev stack

Ctrl-C in the terminal running `just dev`. All three processes exit cleanly. Hit Ctrl-C again if anything hangs.

---

## Triggering autonomous loops manually (for testing)

Local dev does NOT tick autonomous loops by default. To test autonomous behavior in dev there are two patterns:

**Pattern A: Opt a specific loop INTO dev via `UA_DEV_<NAME>_FORCE_ON=1`.**

Phase D (2026-05-11) tightened the dev-mode gate: in dev, **any truthy `UA_<NAME>_ENABLED` is IGNORED** because Infisical's `development` env tends to mirror prod-parity flags. To opt a specific loop in for testing, use the dev-only `UA_DEV_<NAME>_FORCE_ON=1` variable in your `.env`:

```bash
# Example: turn the heartbeat on for one dev session
echo "UA_DEV_HEARTBEAT_FORCE_ON=1" >> .env
just dev   # Ctrl-C the previous session first

# When done, remove the line or set =0
```

Available dev opt-in flags (one per loop) — set `UA_DEV_<NAME>_FORCE_ON=1`:

| Loop | Dev opt-in var | Notes |
|---|---|---|
| Heartbeat (entire service) | `UA_DEV_HEARTBEAT_FORCE_ON` | Master gate. When OFF, the `HeartbeatService` is not instantiated. |
| Cron service (entire service) | `UA_DEV_CRON_FORCE_ON` | Master gate. When OFF, the `CronService` is not instantiated. (Even if it is, persisted jobs from `cron_jobs.json` are NOT loaded in dev.) |
| Cron job registration (only relevant if cron service is on) | `UA_DEV_CRON_REGISTRATION_FORCE_ON` | When OFF, no schedules are registered. |
| Idle dispatch loop | `UA_DEV_IDLE_POLL_FORCE_ON` | |
| Dispatch stale sweep | `UA_DEV_DISPATCH_STALE_SWEEP_FORCE_ON` | |
| Daemon sessions | `UA_DEV_DAEMON_SESSIONS_FORCE_ON` | |
| VP event bridge | `UA_DEV_VP_EVENT_BRIDGE_FORCE_ON` | |
| VP stale reconciler | `UA_DEV_VP_STALE_RECONCILE_FORCE_ON` | |
| AgentMail service | `UA_DEV_AGENTMAIL_SERVICE_FORCE_ON` | When ON, dev connects to the real AgentMail inbox and CAN send real emails. **Use with care.** |
| Notification dispatcher | `UA_DEV_NOTIFICATION_DISPATCHER_FORCE_ON` | When ON, dev CAN send real emails to operator's gmail. **Use with care.** |
| YouTube playlist watcher | `UA_DEV_YOUTUBE_PLAYLIST_WATCHER_FORCE_ON` | |
| HQ self-heartbeat | `UA_DEV_HQ_SELF_HEARTBEAT_FORCE_ON` | Refreshes factory registration — fine to leave off in dev. |

> **Note: legacy `UA_ENABLE_HEARTBEAT=1` / `UA_HEARTBEAT_ENABLED=1` are IGNORED in dev.** Phase D treats them as Infisical-injected prod-parity pollution. Setting them in your local `.env` will NOT enable the loop in dev — only `UA_DEV_<NAME>_FORCE_ON=1` does. This is intentional defensive behavior so dev stays safe even if Infisical's `development` env has prod flags mirrored.

**Pattern B: Inspection CLI via `python -m universal_agent.dev_tools` (Phase E, 2026-05-11).**

For sanity-checking what's running BEFORE you spin up the stack:

```bash
# Print per-loop dev decisions (same as the gateway startup log)
PYTHONPATH=src python -m universal_agent.dev_tools env-report

# Explain why a single loop is on or off
PYTHONPATH=src python -m universal_agent.dev_tools loop-status heartbeat

# List persisted cron jobs (note: dev refuses to load these at startup)
PYTHONPATH=src python -m universal_agent.dev_tools cron-list
```

These are **inspection only** — they don't trigger any autonomous loop. To actually fire a loop in dev, use Pattern A (`UA_DEV_<NAME>_FORCE_ON=1`).

> **Why no live single-tick CLI?** Triggering one heartbeat / cron / sweep cycle in isolation requires booting a minimal in-process gateway (the loops need the gateway + DB + Infisical secrets to function). That's substantial scaffolding for limited extra value when Pattern A already gives you "tick at normal interval until you Ctrl-C." If you need single-iteration testing for a specific scenario, use Pattern A and Ctrl-C after one tick — the gateway logs each cycle.

Either pattern gives you the **same code path as prod** — only the trigger is different.

---

## Database

### Default: SQLite at `~/.local/share/universal_agent/dev.db`

- Created on first service start.
- Persists across dev sessions (so your test data survives Ctrl-C).
- **Never points at prod's Postgres URL.** This is enforced by the Infisical `development` env returning a SQLite URL, not a Postgres one.
- Delete the file to start over: `rm ~/.local/share/universal_agent/dev.db && just dev`

### Realistic data: pulling a prod snapshot

When you want to debug against realistic data shape (real Task Hub history, real run/attempt records, real VP state), use the Phase F snapshot script. UA is 100% SQLite — both prod and dev — so this collapses to safely copying SQLite files over SSH:

```bash
# Pull snapshots of the standard runtime DBs from prod to local
python scripts/snapshot_prod_to_dev.py

# Dry-run first if you want to see what it'll do
python scripts/snapshot_prod_to_dev.py --dry-run

# Custom host or paths
python scripts/snapshot_prod_to_dev.py \
    --ssh-host ua@uaonvps \
    --prod-workspaces-dir /opt/universal_agent/AGENT_RUN_WORKSPACES \
    --dev-workspaces-dir ./AGENT_RUN_WORKSPACES
```

**How it works:** the script uses SQLite's online `.backup` command on the VPS (creates a consistent point-in-time snapshot WITHOUT pausing prod), then `scp`s the snapshot to the local dev workspace. Operator-only — uses your SSH keys, doesn't take credentials. Refuses to run in production (`UA_RUNTIME_STAGE=production`).

Snapshotted databases (default): `runtime_state.db`, `activity_state.db`, `vp_state.db`, `coder_vp_state.db`. Add `--db custom.db` to snapshot others. CSI db at `/var/lib/universal-agent/csi/csi.db` is owned by root and not snapshotted by default — add `--include-csi` (not wired yet) or do it manually if needed.

**Safety:** refuses to overwrite a local DB modified within the last 5 minutes (passes `--force` to override). Prevents stomping on local dev changes mid-session.

### Migrations

```bash
uv run alembic upgrade head   # apply pending migrations to dev.db
uv run alembic revision --autogenerate -m "description"   # create a new migration
```

---

## Tests

Same commands work locally and in CI:

```bash
uv run pytest tests/unit -x -q          # unit tests (fast, no external deps)
uv run pytest tests/integration         # integration tests (run against local stack)
uv run ruff check .                     # lint
uv run ruff format .                    # auto-format
```

Unit tests run in seconds and should always pass on `main`. Integration tests assume `just dev` is running (or wire up their own test stack).

---

## When to use local dev vs VPS

| Task | Where |
|---|---|
| Add/edit a route handler, service-layer function, scoring logic | Local dev — repro, edit, test, ship |
| Fix a UI bug (drawer, layout, chart, optimistic update) | Local dev |
| Develop a new autonomous loop | Local dev — test via single-iteration trigger |
| Write/fix a unit or integration test | Local dev |
| Debug a 500 in a route handler | Local dev — reproduce against dev DB or prod snapshot |
| Verify a UI change against real prod data shape | Local dev with prod snapshot, OR post-deploy on prod |
| Investigate prod-only state corruption | VPS (via desktop terminal Claude with SSH access) |
| Read prod logs for a specific incident | VPS (via Logfire dashboard or desktop terminal Claude with `journalctl`) |
| Confirm a deploy went green | VPS (via `/api/v1/version` endpoint or GH Actions UI) |

The decision is almost always **local dev first, VPS only if local can't reproduce.**

---

## Troubleshooting

### Bootstrap fails: "Missing required bootstrap credentials"

`.env` doesn't contain `INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`, or `INFISICAL_PROJECT_ID`. Re-do § Bootstrap → Step 1.

### Bootstrap fails: "HQ dev bootstrap verification failed"

Infisical creds resolved but the Python verification didn't return the expected role/profile/stage. Means either:
- Your identity has wrong scope (not `development`)
- Infisical project ID is wrong
- Infisical API URL is wrong (or you need to specify a self-hosted URL)

Read the script output for which check failed; the assertion line tells you which value was wrong.

### `just dev` fails: port already in use

Another process is on `:8002`, `:8001`, or `:3000`. Find it: `sudo lsof -iTCP:8002 -sTCP:LISTEN`. Kill it. Or set alternate ports in `.env` and re-run.

### Gateway returns 500 immediately on every endpoint

Probably a startup error swallowed by uvicorn. Check the gateway's stdout in the `just dev` terminal — the traceback will be there. Common causes:
- Missing dev secret in Infisical (the gateway tried to load a key that doesn't exist in the `development` env)
- Database not initialized — run `uv run alembic upgrade head`
- Stale `.env` after Infisical rotation — re-run `bash scripts/bootstrap_local_hq_dev.sh`

### Web UI loads but API calls fail

Check `web-ui/.env.local` — it should have `NEXT_PUBLIC_GATEWAY_URL=http://localhost:8002` and similar. Re-run `bash scripts/install_local_webui_env.sh` if missing.

### "Database is locked" SQLite error

Another process is holding `dev.db`. Likely a leftover dev session that didn't terminate. `pkill -f gateway_server` and `pkill -f api.server` then retry.

### I want to nuke everything and start fresh

```bash
cd /home/kjdragan/lrepos/universal_agent
rm -f .env
rm -f web-ui/.env.local
rm -f ~/.local/share/universal_agent/dev.db
# Then re-do § Bootstrap from scratch.
```

---

## What this is NOT

- **Not always-on.** Local dev runs only when you're at the keyboard. The VPS is the only always-on environment.
- **Not connected to prod state.** The dev database is separate. There is no path by which a local dev change can mutate prod data.
- **Not a replica of the VPS.** No nginx, no systemd, no demo workspaces, no real cron service ticking. It's a development stack, not a production simulator.
- **Not a way to "share dev with another machine."** Each developer's desktop is its own dev environment. State doesn't sync between desktops.
- **Not where ZAI quota is burned.** With loops off, dev does not call ZAI on a timer. Manual single-iteration triggers DO call ZAI (which is expected — that's the test). Continuous quota burn happens only on the VPS.

---

## Audit pending — gaps to close before this contract is fully real

Items the doc claims that **may not yet be true** in the codebase. Each one is a small, testable task.

**Closed in PR #200 (initial implementation, 2026-05-11):**

- [x] **`justfile` at repo root with `dev` recipe.** Runs gateway + api + web-ui in parallel with prefixed output and clean Ctrl-C teardown. Recipes: `dev`, `dev-gateway`, `dev-webui`, `bootstrap`, `dev-kill`, `test`, `lint`, `format`, `preship`.
- [x] **Initial loop-off audit + master switch.** New `src/universal_agent/loop_control.py` is the centralized control plane. In `UA_RUNTIME_STAGE=development` every loop defaults OFF; explicit `UA_<NAME>_ENABLED` always wins. Initial refactor wave: idle_dispatch_loop, dispatch_service (stale sweep), daemon_sessions, gateway_server (vp_event_bridge, vp_stale_reconcile, cron_registration master), heartbeat_service (autonomous-tick subset).
- [x] **`docs/README.md` and `docs/Documentation_Status.md` reference this doc** + the companion briefing doc.

**Closed in Phase C.2 follow-up (2026-05-11, after first end-to-end dev verification revealed gaps):**

- [x] **Heartbeat SERVICE master gate** (not just the autonomous-tick subset). `feature_flags.heartbeat_enabled()` now defaults OFF in dev. Without this, `HeartbeatService` itself instantiated and the scheduler loop ticked, firing real Claude Agent SDK runs.
- [x] **Cron service master gate.** `feature_flags.cron_enabled()` now defaults OFF in dev — the `CronService` itself doesn't instantiate (independent of `UA_CRON_REGISTRATION_ENABLED` which only controls registration once the service is up).
- [x] **AgentMail service master gate.** `should_run_loop("agentmail_service")` — when OFF, no inbox polling, no WebSocket, no outbound email. Prevents dev from racing with prod on the same `oddcity216@agentmail.to` inbox or sending real emails to operator's gmail.
- [x] **Notification dispatcher master gate.** `_notification_dispatcher_enabled()` now uses `should_run_loop` — defaults OFF in dev. Prevents accidental email blast from queued notification backlog when dev boots.
- [x] **YouTube playlist watcher master gate.** `should_run_loop("youtube_playlist_watcher")` — no Google API quota burn from dev.
- [x] **`bootstrap_local_hq_dev.sh` prints a "loops silenced" banner** at the end of bootstrap so operators see what's off and how to opt back in.

**Closed in Phase D (2026-05-11, after second end-to-end dev verification revealed Infisical pollution):**

A second `just dev` run showed heartbeat + cron service + 5 cron jobs firing despite the Phase C.2 gates, because Infisical's `development` env mirrors `UA_ENABLE_HEARTBEAT=1` / `UA_CRON_ENABLED=1` from prod parity. Phase C.2's gates honored those as "explicit operator overrides" — correct for operator-set env vars but wrong for Infisical injection. Phase D tightens the semantics:

- [x] **`loop_control.should_run_loop` dev semantics tightened.** In `UA_RUNTIME_STAGE=development`, truthy `UA_<NAME>_ENABLED` is now **IGNORED** as likely Infisical pollution. Only `UA_DEV_<NAME>_FORCE_ON=1` opts a loop in for dev testing. Explicit `UA_<NAME>_ENABLED=0/false` still honored (operator can force off). Production semantics unchanged.
- [x] **`feature_flags.heartbeat_enabled()` and `cron_enabled()` updated** to the dev/prod split. In dev, legacy `UA_ENABLE_HEARTBEAT=1` from Infisical is also ignored; use `UA_DEV_HEARTBEAT_FORCE_ON=1` to opt in.
- [x] **HQ self-heartbeat gate** added in `gateway_server.py`. `should_run_loop("hq_self_heartbeat")` — in dev, factory registration doesn't refresh (fine; no fleet membership to maintain).
- [x] **CronService defensive isolation** — even if cron_enabled() returns True somehow, the service skips loading the persisted `cron_jobs.json` in dev. Belt-and-suspenders: the 53 persisted prod cron jobs cannot tick on Kevin's desktop even by accident.
- [x] **`report_dev_overrides()` startup log.** At gateway boot in dev mode, logs a per-loop summary showing which loops are off, which are dev-opted-in, and which truthy `UA_*_ENABLED` flags are being ignored as Infisical pollution. Operator sees at a glance what's happening.

**Closed in Phase E (2026-05-11):**

- [x] **Inspection CLI: `python -m universal_agent.dev_tools`.** Three subcommands: `env-report` (per-loop dev decisions, same as gateway startup log), `loop-status <name>` (explain one loop), `cron-list` (list persisted cron jobs from `cron_jobs.json`). 12-test suite in `tests/unit/test_dev_tools_cli.py`. **Live single-iteration triggers (`heartbeat tick`, `cron run-once`) explicitly deferred** — they'd require booting a minimal in-process gateway, and Pattern A (set `UA_DEV_<NAME>_FORCE_ON=1`, restart `just dev`, Ctrl-C after one tick) covers the same use case without the scaffolding.

**Closed in Phase F (2026-05-11):**

- [x] **`snapshot_prod_to_dev.py` script.** New `scripts/snapshot_prod_to_dev.py` pulls SQLite snapshots from prod via SSH + SQLite's online `.backup` (consistent point-in-time, no prod pause). Refuses to run in production. Refuses to overwrite a local DB modified within 5 minutes (use `--force` to override). Default DBs: `runtime_state.db`, `activity_state.db`, `vp_state.db`, `coder_vp_state.db`. 10-test suite in `tests/unit/test_snapshot_prod_to_dev.py`. See § Realistic data above.
- [x] **Dev DB story confirmed: UA is 100% SQLite.** Investigation during Phase F found no Postgres code paths anywhere — `durable/db.py` ships only SQLite drivers, all the runtime/state/VP DBs are `.db` files in `AGENT_RUN_WORKSPACES`. The hypothetical "force SQLite in dev" defensive code was unnecessary because Postgres was never an option. Documented above in § Database (clarified).
- [x] **`bootstrap_local_hq_dev.sh` freshness verification.** Operator confirmed working end-to-end on desktop 2026-05-11. Closed.

**Operator-side, not blocking:**

- [x] **`development` Infisical environment hygiene.** Operator-side cleanup per `13_Infisical_Dev_Env_Hygiene.md` — already partially completed by operator 2026-05-11. Phase D's defensive layer makes this nice-to-have, not blocking.

**Contract is canonical.** Future enhancements (e.g., a live single-iteration CLI for heartbeat/cron triggers) would be additive — not gaps in the contract.

---

## Cross-references

- **Running Claude Code in this dev env:** [`../development/CLAUDE_CODE_CHEAT_SHEET.md`](../development/CLAUDE_CODE_CHEAT_SHEET.md) — one-page cheat sheet for `bash scripts/claude_with_mcp_env.sh`, common flags, ZAI cheap-mode alternative.
- **Lane definition:** [`05_Local_Runtime_Modes.md`](05_Local_Runtime_Modes.md) — defines HQ Dev Lane vs Desktop Worker Lane (this doc supersedes its HQ Dev Lane runbook).
- **VPS-as-dev (now a fallback path):** [`11_Daily_Dev_Workflow.md`](11_Daily_Dev_Workflow.md) — Antigravity Remote-SSH workflow. Pre-2026-05-10 this was the canonical dev path. Post-inversion, **desktop dev (this doc) is canonical**; VPS-via-Remote-SSH is a fallback for cases where desktop dev isn't available.
- **Branch + deploy model:** [`04_Branching_And_Release_Workflow.md`](04_Branching_And_Release_Workflow.md) — what happens after you commit + push.
- **Secrets:** [`../deployment/secrets_and_environments.md`](../deployment/secrets_and_environments.md) — Infisical bootstrap pattern (canonical).
- **Claude execution profiles:** [`10_Interactive_Coding_Environment.md`](10_Interactive_Coding_Environment.md) — which Claude runs where, OAuth vs ZAI.
