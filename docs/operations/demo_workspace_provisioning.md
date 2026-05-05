# Demo Workspace Provisioning Runbook

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

---

## Step 4 — Run the smoke demo

```bash
cd /opt/ua_demos/_smoke
uv pip install anthropic   # demo-local dep
uv run python smoke.py
```

Expected output (on success):

```json
{
  "ok": true,
  "endpoint": "https://api.anthropic.com",
  "model": "claude-haiku-4-5-20251001",
  "stop_reason": "end_turn",
  "live_call": "completed",
  "response_excerpt": "OK"
}
```

If `endpoint` is anything other than `https://api.anthropic.com`, an
`ANTHROPIC_BASE_URL` env var is leaking from the parent shell. Unset it
and try again:

```bash
unset ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_DEFAULT_HAIKU_MODEL ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_OPUS_MODEL
uv run python smoke.py
```

Best practice for the VPS: run Cody's demo subprocesses with a clean
environment (e.g., a dedicated systemd service unit with `Environment=`
explicitly set, NOT inheriting the operator shell).

---

## Step 5 — Verify Cody's invocation contract

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
| `smoke.py` exits with code 1, error mentions auth | Max plan OAuth session expired or never set up | Re-run step 3 |
| `claude` not found | Claude Code CLI not installed on VPS | `npm install -g @anthropic-ai/claude-code` (or whatever upgrade path PR 6b ends up using) |
