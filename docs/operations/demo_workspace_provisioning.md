# Demo Workspace Provisioning Runbook

> 🔴 **2026-06-07 UPDATE — demos now run on ZAI, not real Anthropic.**
> Anthropic began API-billing the Claude-Code-via-Max SDK path, so demos were
> flipped to the ZAI proxy along with the rest of the fleet. `run_in_workspace`
> no longer scrubs `ANTHROPIC_*` by default, so a demo's `claude` inherits the
> daemon's ZAI env. The Max-plan OAuth login + `api.anthropic.com` smoke steps
> below are now only relevant for the rare demo explicitly pinned to real
> Anthropic (`endpoint_required: anthropic_native` + `scrub_env=True`).
>
> ⚠️ **BEFORE YOU TOUCH ANY CLAUDE COMMAND ON THE VPS** ⚠️
>
> UA runs **two separate Claude environments side by side** on the VPS — a
> ZAI-mapped one (cheap GLM models, default for everything) and an
> Anthropic-native one (real Claude, demos only, in `/opt/ua_demos/`).
> Mistaking one for the other is the #1 source of confusion in this system.
>
> Read **[Demo Execution Environments](../06_Deployment_And_Environments/09_Demo_Execution_Environments.md)**
> first. Especially the [decision tree](../06_Deployment_And_Environments/09_Demo_Execution_Environments.md#decision-tree-which-environment-do-i-need)
> and the [CLI vs SDK auth wrinkle](../06_Deployment_And_Environments/09_Demo_Execution_Environments.md#the-cli-vs-sdk-auth-wrinkle-read-this-it-bit-us).
> Then come back here for the actual setup steps.

> **Audience:** Ship operator / Kevin
> **Purpose:** One-time setup so Phase 3 (Cody implementation) can run on the VPS
> **Scope:** `/opt/ua_demos/` provisioning + Max plan OAuth login
> **Linked design:** [`docs/proactive_signals/claudedevs_intel_v2_design.md`](../proactive_signals/claudedevs_intel_v2_design.md) §8

---

## Why this exists

UA's normal `~/.claude/settings.json` reroutes Anthropic model IDs to a ZAI
proxy for cheap GLM-based coding. That's correct for routine UA work. It is
*wrong* for testing brand-new Anthropic features — the proxy may not yet
implement them, and a demo would silently fail or exercise the wrong API.

The fix: a parallel execution environment under `/opt/ua_demos/` where each
demo workspace carries its own project-local `.claude/settings.json` with no
ZAI mapping, no UA hooks, no enabled plugins. Claude Code launched from
inside such a workspace inherits the project-local settings and a Max plan
OAuth session, so demos exercise real Anthropic endpoints.

This file is the one-time setup. After it, Cody and Simone can provision per-demo
workspaces autonomously.

---

## Pre-conditions

- VPS has a user account that runs Cody's demo subprocesses (typically the
  same user that runs UA itself).
- Anthropic Max plan account credentials available.
- Claude Code CLI installed on the VPS (`claude --version` works).
- Python 3.12+ and `uv` installed.

---

## Step 1 — Create the demos root

```bash
sudo mkdir -p /opt/ua_demos
sudo chown $USER:$USER /opt/ua_demos
chmod 755 /opt/ua_demos
```

If you prefer a non-default location, set `UA_DEMOS_ROOT` in the UA process
environment and use that path here. The provisioner reads it.

---

## Step 2 — Provision the smoke workspace

The smoke workspace is what Phase 0 (dependency upgrade worker) uses to
verify after every Anthropic SDK bump that demos can still hit real
Anthropic endpoints.

```bash
cd /opt/universal_agent  # or wherever the UA repo lives on the VPS
PYTHONPATH=src uv run python -c "
from universal_agent.services.demo_workspace import provision_smoke_workspace
result = provision_smoke_workspace()
print(result.workspace_dir)
"
```

You should see `/opt/ua_demos/_smoke` printed. Verify the structure:

```bash
ls -la /opt/ua_demos/_smoke
cat /opt/ua_demos/_smoke/.claude/settings.json
```

The settings.json must NOT contain `env`, `hooks`, `enabledPlugins`, or
`extraKnownMarketplaces`. The provisioner asserts this and refuses to
return successfully if any are present.

---

## Step 3 — Authenticate via the Max plan

This is the one step that requires interactive login. Run from inside the
smoke workspace so the project-local settings.json takes effect:

```bash
cd /opt/ua_demos/_smoke
claude /login
```

Follow the browser-based OAuth flow with Kevin's Max plan account. Claude
Code will store the session token in the user's home directory; the
provisioner does not touch it.

**Important:** `claude /login` MUST be performed from inside a demo
workspace (or any directory that doesn't carry the ZAI mapping in its local
settings). Running it from a directory that inherits `~/.claude/settings.json`'s
`ANTHROPIC_BASE_URL` would store credentials for the wrong endpoint.

> **Post-2026-05-07 note (Interactive Coding Environment inversion):** the
> user-global `~/.claude/settings.json` on both VPS (`/home/ua/`) and desktop
> (`/home/kjdragan/`) no longer contains an `env` block carrying
> `ANTHROPIC_BASE_URL`. Plain `claude` defaults to Anthropic Max via OAuth
> from anywhere. The "must run /login from inside the demo workspace" rule
> remains a defensive best practice — but the *practical* risk it guards
> against is largely gone. See
> [`../06_Deployment_And_Environments/10_Interactive_Coding_Environment.md`](../06_Deployment_And_Environments/10_Interactive_Coding_Environment.md)
> for the full inversion story.

---

## Step 4 — Run the smoke demo

The smoke is **CLI-driven** (it shells out to `claude -p "..."`) — no
Python SDK install needed. The bundled `pyproject.toml` is intentionally
empty of dependencies so this just works:

```bash
cd /opt/ua_demos/_smoke
uv run python smoke.py
```

Expected output (on success):

```json
{
  "ok": true,
  "endpoint": "https://api.anthropic.com",
  "live_call": "completed",
  "response_excerpt": "OK",
  "matched_expected_token": true,
  "host": "srv1360701"
}
```

If `endpoint` is anything other than `https://api.anthropic.com`, an
`ANTHROPIC_BASE_URL` env var is leaking from the parent shell. Unset it
and try again:

```bash
unset ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_DEFAULT_HAIKU_MODEL ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_OPUS_MODEL
uv run python smoke.py
```

> **Post-2026-05-07 note:** after the inversion, the most common cause of a
> leaked `ANTHROPIC_BASE_URL` is having run `zai` earlier in the same shell
> (the `zai` shell function inlines the env when running `claude`, but does
> *not* export to the parent shell — so this should be rare). If you still
> see the leak in a fresh shell, check `/home/ua/.claude/settings.json` for
> any re-introduced `env` block — Phase B explicitly removed those keys.

Best practice for the VPS: run Cody's demo subprocesses with a clean
environment (e.g., a dedicated systemd service unit with `Environment=`
explicitly set, NOT inheriting the operator shell).

### Why the smoke is CLI-driven, not SDK-driven

This is critical to understand. The Claude Code CLI (`claude`) and the
Anthropic Python SDK (`from anthropic import Anthropic`) use **two
different authentication mechanisms**:

| Path | Auth source | What it tests |
|---|---|---|
| Claude Code CLI | Max plan OAuth session (set up by `claude /login`) | What Cody actually uses for demos |
| Anthropic Python SDK | `ANTHROPIC_API_KEY` env var only | A separate, distinct path that needs a different credential |

A successful `claude /login` does NOT make `Anthropic()` work in
Python — the SDK doesn't read the OAuth session. So validating the
demo execution path means invoking the CLI and checking the response,
not constructing an SDK client.

If you ever want to verify the endpoint manually beyond what the smoke
reports, this two-terminal trick gives bulletproof proof:

Terminal 1:

```bash
cd /opt/ua_demos/_smoke
claude -p "Write a 200-word story about a cat. Take your time."
```

Terminal 2 (while terminal 1 is still running):

```bash
ss -t state established | grep -E 'anthropic|z\.ai'
```

You should see a TCP connection to `api.anthropic.com` and **nothing**
to `api.z.ai`. If you see ZAI, the project-local settings precedence
isn't taking effect.

### Demos that need the Python SDK

A small minority of demos may want to exercise the Anthropic SDK directly
(e.g., to demonstrate prompt caching, the Memory Tool API surface, etc.).
These are **category-2 demos** and need different setup than the default
Claude Code feature demos:

1. The demo's own `pyproject.toml` adds `anthropic` as a dependency.
2. The workspace env carries an `ANTHROPIC_API_KEY` — **not** the same
   thing as the Max plan OAuth session. Get this from
   `console.anthropic.com` under the Max plan account.
3. The demo's `manifest.json.endpoint_hit` should still resolve to
   `api.anthropic.com` — the API key is the auth, not a different endpoint.

See [Demo Execution Environments](../06_Deployment_And_Environments/09_Demo_Execution_Environments.md)
for the full CLI-vs-SDK distinction and which type of demo to use when.

---

## Step 6 — Optional: ephemeral databases via Ghost

Demo workspaces ship with a `.mcp.json` that exposes the **Ghost** MCP server
(Timescale's "database for agents"). When a demo needs a real Postgres
(pgvector, TimescaleDB hypertables, PostGIS, JSONB at scale), Cody calls
`ghost_create`, `ghost_sql`, `ghost_schema`, `ghost_fork`, `ghost_delete`
as tools — no SDK, no connection-string plumbing, no operator setup per demo.

**Operator one-time setup:**

1. Sign in at https://ghost.build (GitHub OAuth) and generate an API key from
   the account dashboard.
2. Add it to Infisical at the same path as the rest of UA's MCP-consumed
   secrets:

   ```
   key  = GHOST_API_KEY
   value = ghost_pat_xxxxxxxxxxxx
   ```

3. Restart the UA daemon (or wait for the next deploy) so
   `initialize_runtime_secrets()` injects the new var into `os.environ`.
   From there, `_build_cli_env` propagates it into Cody's subprocess and the
   `${GHOST_API_KEY}` placeholder in the demo's `.mcp.json` resolves.

**First-run cost.** The MCP server launches via `npx -y @ghost.build/cli` —
the first demo to invoke it downloads ~2 MB. Subsequent demos hit the npm
cache. If you'd rather avoid the per-demo cold start, install the CLI
globally with `npm install -g @ghost.build/cli` (the same `.mcp.json`
command resolves to the installed binary because of npm's lookup order).

**Cleanup obligation (CRITICAL).** Ghost's free tier is 100 hours/month across
the whole UA account. Abandoned demo DBs burn that cap. The cleanup contract
is enforced by convention, not code:

| Phase | Responsibility |
|---|---|
| Cody, during the demo run | Record every created DB in `manifest.json.ghost_databases: ["<name>"]` before calling any `ghost_sql` against it. |
| Cody, on successful run | Call `ghost_delete` on each DB before writing the final `manifest.json`. |
| Cody, on failed run | Leave the DB intact AND keep its name in `manifest.json` so the next iteration / operator audit can reclaim it. |
| Operator, weekly | Run `ghost list` from any shell that has `GHOST_API_KEY` set. Compare against `/opt/ua_demos/*/manifest.json` and `ghost delete` any orphans. |

A future hardening would be a sweeper script that parses every demo's
`manifest.json`, cross-references against `ghost list`, and deletes orphans
automatically. Not built yet — manual audit is the current contract.

**Demos that should NOT use Ghost:**
- The `_smoke` workspace (no `.mcp.json`; it's a pure liveness probe).
- Any demo whose acceptance contract can be met with SQLite or in-memory
  fixtures — Ghost burns the 100hr cap for no reason.

**Demos that probably SHOULD use Ghost:**
- pgvector retrieval demos.
- TimescaleDB hypertable / continuous aggregate demos.
- PostGIS spatial query demos.
- Any demo whose acceptance contract explicitly names "real Postgres" or
  one of the above extensions.

---

## Step 7 — Verify Cody's invocation contract

When Cody's `cody-implements-from-brief` skill (PR 9) lands, it will:

1. `cd /opt/ua_demos/<demo-id>/`
2. Verify project-local settings.json is vanilla (calls
   `verify_vanilla_settings`).
3. Read `BRIEF.md`, `ACCEPTANCE.md`, `business_relevance.md`, `SOURCES/`.
4. Build the demo, write `manifest.json` recording which endpoint actually
   served the response.

For now, Cody isn't wired up yet, but the smoke demo's pattern is the
template every real demo will follow.

---

## Ongoing operations

Once the smoke workspace is provisioned and OAuth is set up:

- **New demos** are provisioned by Simone autonomously via PR 8's skill,
  which calls `provision_demo_workspace(demo_id)`.
- **Cleanup** of failed demos is manual for now: `rm -rf /opt/ua_demos/<demo-id>/`.
- **Re-authentication** if the Max plan session expires:
  `cd /opt/ua_demos/_smoke && claude /login`.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `provision_smoke_workspace` raises `ValueError: settings.json carries pollution markers` | Scaffold template was edited and re-introduced an env/hooks/plugins key | Remove the offending key from `src/universal_agent/templates/_smoke_demo/.claude/settings.json` and redeploy |
| `smoke.py` exits with code 2 (`endpoint_mismatch`) | `ANTHROPIC_BASE_URL` set in shell environment | `unset ANTHROPIC_BASE_URL`; consider running Cody as a systemd service with explicit env |
| `smoke.py` exits with code 1, `live_call: failed`, stderr mentions auth | Max plan OAuth session expired or never set up | Re-run step 3 from inside `/opt/ua_demos/_smoke/` |
| `smoke.py` exits with code 1, `live_call: skipped_claude_cli_not_installed` | Claude Code CLI not installed on VPS | `npm install -g @anthropic-ai/claude-code` (or whatever upgrade path PR 6b ends up using) |
| `smoke.py` exits with code 1, `live_call: timeout` | Network issue or `claude` is hanging | Try `claude -p "test"` manually; check VPS network egress |
| `uv run python smoke.py` complains "No virtual environment found" | The smoke template was deployed before PR 7b shipped the bundled `pyproject.toml` | Re-deploy or manually `cd /opt/ua_demos/_smoke && uv venv && uv pip install -r /dev/null` |
| `Anthropic()` constructor in some demo Python file raises `Could not resolve authentication method` | Trying to use the Python SDK with the Max plan OAuth session, which the SDK ignores | Either rewrite the demo to shell out to `claude` (preferred for Claude Code feature demos), OR add an `ANTHROPIC_API_KEY` from `console.anthropic.com` to the workspace env (for SDK feature demos) |
| Ghost MCP server fails to start with "missing GHOST_API_KEY" | `GHOST_API_KEY` not in Infisical, OR UA daemon hasn't restarted since it was added | Add the var to Infisical (Step 6), then `sudo systemctl restart ua-daemon` (or whatever the production service is named) so `initialize_runtime_secrets()` re-fetches |
| `ghost list` shows DBs no demo references | Abandoned by failed/orphaned demo. Burns the 100hr/mo cap. | Cross-reference against `/opt/ua_demos/*/manifest.json`. `ghost delete <name>` for orphans. |
