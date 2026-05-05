# Phase 0 Smoke Demo

A minimal demo that verifies the `/opt/ua_demos/_smoke/` execution path
works end-to-end against real Anthropic endpoints via the Max plan OAuth
session. Used by Phase 0's dependency-currency upgrade worker as the
gate that decides whether an Anthropic SDK / Claude Code CLI bump is
safe to ship.

## Why CLI-driven and not SDK-driven

Earlier versions of this smoke used `from anthropic import Anthropic`
to test the Python SDK path. That was wrong for our architecture
because **the Claude Code CLI and the Anthropic Python SDK use
different authentication mechanisms**:

| Mechanism | What it auths | How |
|---|---|---|
| Claude Code CLI (`claude /login`) | The CLI itself | Max plan OAuth session, persisted in user home dir |
| Anthropic Python SDK (`Anthropic()`) | Direct API calls from Python | `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` env var |

Cody (our coding agent) IS a Claude Code instance — when she runs a
demo, she invokes the `claude` CLI, which uses the Max plan OAuth.
That's the canonical demo execution primitive. Validating with `Anthropic()`
in Python would be testing a different code path that no real demo uses
unless it specifically needs the SDK.

So this smoke shells out to `claude -p "..."` and verifies the response
came back sensibly. That's the actual demo execution path.

## What it checks

1. The subprocess inherits no `ANTHROPIC_BASE_URL` env override (no ZAI
   mapping leakage from the parent shell).
2. The `claude` CLI is on `PATH`.
3. A one-shot prompt (`claude -p "Reply with exactly the word OK"`)
   completes within 60 seconds.
4. The response contains the expected token.

## How to run

```bash
cd /opt/ua_demos/_smoke
uv run python smoke.py
```

Exit codes:
- `0` — all checks passed (CLI returned a sensible response)
- `1` — claude CLI failed (binary missing, OAuth expired, or API error)
- `2` — endpoint mismatch (`ANTHROPIC_BASE_URL` set in parent env)

## Pre-conditions

- `claude /login` was run once on the VPS **from inside this directory**
  (`/opt/ua_demos/_smoke/`) with Kevin's Max plan account. Logging in
  from any other directory may store credentials against the ZAI-mapped
  endpoint instead of `api.anthropic.com`.
- `~/.claude/settings.json` may contain UA's normal ZAI mapping. The
  project-local `.claude/settings.json` in this directory overrides it
  when `claude` is invoked here.
- The `claude` CLI is installed (`npm i -g @anthropic-ai/claude-code` or
  per the Phase 0 dependency-currency upgrade pipeline).

## What this demo does NOT validate

This smoke validates the **CLI execution path** only. It does NOT
validate the **Python SDK path**. If a future demo specifically needs
to call `Anthropic()` from Python (e.g., to exercise an SDK-level
feature), that demo must:

1. Add `anthropic` to its own workspace's `pyproject.toml` dependencies.
2. Provision an `ANTHROPIC_API_KEY` env var in its execution environment.
   This is a **separate credential** from the Max plan OAuth session,
   issued from `console.anthropic.com` under the same Max plan account.

See [Demo Execution Environments](../../../docs/06_Deployment_And_Environments/09_Demo_Execution_Environments.md)
for the full CLI-vs-SDK story.

## Verifying the endpoint manually

If you want belt-and-suspenders proof that requests actually go to
`api.anthropic.com` and not the ZAI proxy, in one terminal:

```bash
cd /opt/ua_demos/_smoke
claude -p "Write a 200-word story about a cat. Take your time."
```

In a second terminal while the request is in flight:

```bash
ss -t state established | grep -E 'anthropic|z\.ai'
```

You should see a TCP connection to an `api.anthropic.com` IP and
nothing to `api.z.ai`.
