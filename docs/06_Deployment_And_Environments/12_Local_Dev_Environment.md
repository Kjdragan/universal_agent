# 12. Local Development Environment — The Contract

> **Audience:** Kevin (primary), and any future agent/operator who needs to run UA locally for development.
>
> **Status:** Contract draft (2026-05-10). Some pieces work today; others are gaps to close. See **§ Audit pending** at the bottom for the explicit punch list.
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

**Pattern A: Flip an individual loop ON via env var, restart `just dev`.**

Each loop respects an explicit `UA_<NAME>_ENABLED` override that beats the dev-default-OFF. Add the line to your `.env`, restart, and the loop ticks at its normal interval. Set back to `0` or remove the line when done.

```bash
# Example: turn the heartbeat on for one dev session
echo "UA_HEARTBEAT_AUTONOMOUS_ENABLED=1" >> .env
just dev   # Ctrl-C the previous session first

# When done, remove the line or set =0
```

Available flags (one per loop):

| Loop | Env var |
|---|---|
| Autonomous heartbeat | `UA_HEARTBEAT_AUTONOMOUS_ENABLED` |
| Idle dispatch loop | `UA_IDLE_POLL_ENABLED` |
| Dispatch stale sweep | `UA_DISPATCH_STALE_SWEEP_ENABLED` |
| Daemon sessions | `UA_DAEMON_SESSIONS_ENABLED` |
| VP event bridge | `UA_VP_EVENT_BRIDGE_ENABLED` |
| VP stale reconciler | `UA_VP_STALE_RECONCILE_ENABLED` |
| Cron registration (all crons) | `UA_CRON_REGISTRATION_ENABLED` |
| AgentMail polling | `UA_AGENTMAIL_ENABLED` |
| AgentMail WS | `UA_AGENTMAIL_WS_ENABLED` |
| Autonomous daily briefing | `UA_AUTONOMOUS_DAILY_BRIEFING_ENABLED` |

**Pattern B: Single-iteration CLI invocations (deferred — coming in a follow-up PR).**

The intended pattern is `python -m universal_agent.heartbeat tick` and similar — one tick, deterministic, exits when done. These entry points are not yet wired (see § Audit pending). Until they are, Pattern A is the way.

Either pattern gives you the **same code path as prod** — only the trigger is different.

---

## Database

### Default: SQLite at `~/.local/share/universal_agent/dev.db`

- Created on first service start.
- Persists across dev sessions (so your test data survives Ctrl-C).
- **Never points at prod's Postgres URL.** This is enforced by the Infisical `development` env returning a SQLite URL, not a Postgres one.
- Delete the file to start over: `rm ~/.local/share/universal_agent/dev.db && just dev`

### Realistic data: pulling a prod snapshot

When you want to debug against realistic data shape (large CSI report counts, real HN snapshots, real Task Hub history):

```bash
# Pull a one-time snapshot from prod's Postgres into dev's SQLite
uv run python -m universal_agent.scripts.snapshot_prod_to_dev
```

(Audit pending — this script may need to be created. The pattern: `pg_dump --data-only` from prod, transform to SQLite-compatible SQL, load into dev.db. Read-only dump; never writes back to prod.)

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

**Closed in PR following the contract:**

- [x] **`justfile` at repo root with `dev` recipe.** Runs gateway + api + web-ui in parallel with prefixed output and clean Ctrl-C teardown. Recipes: `dev`, `dev-gateway`, `dev-webui`, `bootstrap`, `dev-kill`, `test`, `lint`, `format`, `preship`.
- [x] **Verify every autonomous loop has a clean OFF flag.** Audit completed; new `src/universal_agent/loop_control.py` is the centralized control plane. In `UA_RUNTIME_STAGE=development` every loop defaults OFF; explicit `UA_<NAME>_ENABLED` always wins. Refactored: heartbeat_service, idle_dispatch_loop, dispatch_service (stale sweep), daemon_sessions, gateway_server (vp_event_bridge, vp_stale_reconcile, cron_registration master). Already-OFF-by-default: AgentMail, daily briefing, dashboard SSE, activity digest. Standalone CLI process: worker.py (only runs when manually invoked).
- [x] **`docs/README.md` and `docs/Documentation_Status.md` reference this doc** + the companion briefing doc.

**Deferred to follow-up PRs (not blockers for the contract):**

- [ ] **Single-iteration CLI entry points** for each autonomous loop. Pattern: `python -m universal_agent.heartbeat tick`, `python -m universal_agent.cron run-once <name>`. Useful for testing autonomous behavior in dev without ticking continuously. Workaround until then: set `UA_<NAME>_ENABLED=1` in `.env` for the specific loop you want to test, restart `just dev`, and the loop will tick at its normal interval. Tradeoff: that loop ticks until you set the flag back to 0.
- [ ] **`development` Infisical environment completeness.** Cross-reference every secret the gateway/api try to load against the `development` env in Infisical. Anything prod-only must either be added to dev (with a sandbox/test value) or have a defaults-and-skip path. **Operator-side check** — can only be verified by reading what's in Infisical.
- [ ] **Dev DB story.** Confirm the `development` env returns a SQLite URL (or that the gateway falls back to one when a Postgres URL isn't set). Confirm migrations apply cleanly to a fresh SQLite. Depends on the Infisical-completeness audit above.
- [ ] **`snapshot_prod_to_dev.py` script.** For pulling a read-only prod snapshot when realistic data is needed. Not a blocker — dev works without it; this is a quality-of-life addon when you want to debug against realistic data shape.
- [ ] **`bootstrap_local_hq_dev.sh` freshness verification.** Last modified March 2026; needs end-to-end manual run on Kevin's desktop to confirm it still works against current code + Infisical schema.

Once the deferred items close, this doc moves from "Contract draft" to "Canonical."

---

## Cross-references

- **Lane definition:** [`05_Local_Runtime_Modes.md`](05_Local_Runtime_Modes.md) — defines HQ Dev Lane vs Desktop Worker Lane (this doc supersedes its HQ Dev Lane runbook).
- **VPS-as-dev (now a fallback path):** [`11_Daily_Dev_Workflow.md`](11_Daily_Dev_Workflow.md) — Antigravity Remote-SSH workflow. Pre-2026-05-10 this was the canonical dev path. Post-inversion, **desktop dev (this doc) is canonical**; VPS-via-Remote-SSH is a fallback for cases where desktop dev isn't available.
- **Branch + deploy model:** [`04_Branching_And_Release_Workflow.md`](04_Branching_And_Release_Workflow.md) — what happens after you commit + push.
- **Secrets:** [`../deployment/secrets_and_environments.md`](../deployment/secrets_and_environments.md) — Infisical bootstrap pattern (canonical).
- **Claude execution profiles:** [`10_Interactive_Coding_Environment.md`](10_Interactive_Coding_Environment.md) — which Claude runs where, OAuth vs ZAI.
