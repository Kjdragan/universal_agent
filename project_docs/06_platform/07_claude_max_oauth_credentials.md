---
title: Claude Max OAuth Credentials (CLAUDE_CODE_OAUTH_TOKEN)
status: active
canonical: true
subsystem: claude-max-oauth
code_paths:
  - src/universal_agent/vp/clients/claude_cli_client.py
  - src/universal_agent/infisical_loader.py
last_verified: 2026-06-02
---

# Claude Max OAuth Credentials (`CLAUDE_CODE_OAUTH_TOKEN`)

Canonical reference + runbook for the credential that authenticates **Cody-on-Anthropic-Max**
work (every `cody_mode="anthropic"` mission and every `/opt/ua_demos/` demo build). Read this
when a Cody mission or demo build 401s with an auth error. For the broader execution-profile
picture see [Execution Environments](05_environments.md); for the secrets pipeline see
[Secrets & Infisical](01_secrets_and_infisical.md).

## TL;DR — single source of truth

**Production Cody-on-Anthropic-Max uses `CLAUDE_CODE_OAUTH_TOKEN` from Infisical
(environment slug `production`). Nothing else.**

- The token is a long-lived `sk-ant-oat01-…` (~108 chars) produced by `claude setup-token`.
- If a Cody mission or demo build 401s, the credential to refresh is `CLAUDE_CODE_OAUTH_TOKEN`
  in Infisical — **never** any `.credentials.json` file.
- `~/.claude/.credentials.json` on the VPS (`/home/ua/.claude/.credentials.json`) is **orphan
  state** from an old interactive `claude /login`. Nothing in production reads it. It is safe to
  delete and was deleted on 2026-06-02 after it misled an automated diagnosis (a parked demo's
  defer reason cited that file's stale May-16 expiry; production never touched it).

## Architecture: three independent OAuth stores

| Store | What | Refreshed by | Read by |
|---|---|---|---|
| **Infisical `CLAUDE_CODE_OAUTH_TOKEN`** (env `production`) | Long-lived OAuth token (`sk-ant-oat01-…`, ~108 chars) from `claude setup-token` | Operator, manually, when it expires (long TTL; infrequent) | The `claude` CLI subprocess spawned by every Cody Anthropic-mode mission / demo build |
| **`/home/kjdragan/.claude/.credentials.json`** (Kevin's desktop) | `claudeAiOauth` access+refresh token for the desktop interactive session | The `claude` CLI itself, automatically, each interactive session | Kevin's local `claude` CLI |
| **`/home/ua/.claude/.credentials.json`** (VPS, orphan) | Stale `claudeAiOauth` from an old VPS interactive session | NOTHING — no auto-refresh runs there | NOTHING in production |

These serve different audiences (machine-to-machine vs. interactive-user) with different refresh
mechanics (manual `setup-token` vs. automatic OAuth refresh chain). Do **not** try to mirror them.

## The production credential flow (Cody-on-Anthropic)

```
Operator runs `claude setup-token` (once, when the token expires)
        │  prints: export CLAUDE_CODE_OAUTH_TOKEN=<sk-ant-oat01-…>
        ▼
Operator stores it in Infisical as CLAUDE_CODE_OAUTH_TOKEN (env=production)
        │
        ▼  Gateway / VP-worker daemon boots
infisical_loader.py::initialize_runtime_secrets()  → os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = …
        │  (injected IN-PROCESS; no `infisical run` wrapping — see the /proc gotcha below)
        ▼  a cody_mode="anthropic" mission/demo arrives
vp/clients/claude_cli_client.py::_build_cli_env():
   1. strips every ANTHROPIC_* var from the subprocess env
      (so a stale ANTHROPIC_API_KEY can't take precedence — Claude Code prefers an API key over OAuth)
   2. forwards env["CLAUDE_CODE_OAUTH_TOKEN"] from os.environ (legacy fallback: ANTHROPIC_MAX_OAUTH_TOKEN)
   3. sets env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"
        ▼
`claude --print` subprocess authenticates via the OAuth token.
It NEVER reads ~/.claude/.credentials.json on this path.
```

Source of truth: `vp/clients/claude_cli_client.py::_build_cli_env` (the Anthropic-mode env scrub +
token forward) and `infisical_loader.py::initialize_runtime_secrets` (the in-process injection).

## How to refresh the token

When Cody missions / demo builds start 401-ing with an authentication error from `api.anthropic.com`:

1. On any machine that can run `claude` (Kevin's desktop, or the VPS via `ssh ua@uaonvps`), run:
   ```bash
   claude setup-token
   ```
   It prints a fresh `sk-ant-oat01-…` token (copy only the token value, not the leading text).

2. Store it in Infisical (`production`):
   ```bash
   # On any machine with machine-id Infisical creds (e.g. the VPS, sourcing /opt/universal_agent/.env):
   TOK=$(infisical login --method=universal-auth \
          --client-id="$INFISICAL_CLIENT_ID" \
          --client-secret="$INFISICAL_CLIENT_SECRET" --plain --silent)
   INFISICAL_TOKEN="$TOK" infisical secrets set CLAUDE_CODE_OAUTH_TOKEN="<paste-token>" \
     --projectId="$INFISICAL_PROJECT_ID" --env=production
   ```
   > The environment slug is **`production`**, not `prod` — `--env=prod` errors out.

3. Restart the services so they re-inject the new env via `initialize_runtime_secrets()`:
   ```bash
   ssh ua@uaonvps "sudo systemctl restart \
       universal-agent-gateway \
       universal-agent-vp-worker@vp.coder.primary \
       universal-agent-vp-worker@vp.general.primary"
   ```

The next Anthropic-mode mission spawned after restart uses the fresh token.

## Diagnostic recipe (verify it's really a token problem)

```bash
ssh ua@uaonvps 'set -a; source /opt/universal_agent/.env; set +a
TOK=$(infisical login --method=universal-auth --client-id="$INFISICAL_CLIENT_ID" \
       --client-secret="$INFISICAL_CLIENT_SECRET" --plain --silent)
# (a) presence + shape (never print the value):
INFISICAL_TOKEN=$TOK infisical run --projectId="$INFISICAL_PROJECT_ID" --env=production --silent -- \
  bash -c "echo prefix=\${CLAUDE_CODE_OAUTH_TOKEN:0:10} len=\${#CLAUDE_CODE_OAUTH_TOKEN}"
# (b) live validity — mirrors _build_cli_env (strip ANTHROPIC_*, run claude with the token):
INFISICAL_TOKEN=$TOK infisical run --projectId="$INFISICAL_PROJECT_ID" --env=production --silent -- \
  bash -c "for v in \$(env | grep ^ANTHROPIC_ | cut -d= -f1); do unset \$v; done
           /home/ua/.local/bin/claude --print --model claude-opus-4-8 \"Reply with one word: READY\""'
```

Expected: `prefix=sk-ant-oat len=108`, and the live call prints `READY`. If `len=0`/wrong prefix,
the Infisical token is missing/wrong — re-run `claude setup-token` and store it. If the shape is
right but the live call 401s, the token is expired/revoked — refresh it.

> **`/proc/<pid>/environ` gotcha:** `initialize_runtime_secrets()` injects secrets **in-process**
> (`os.environ[...] = …`) at startup; the services are **not** launched via `infisical run`
> wrapping. `/proc/<pid>/environ` only reflects the *exec-time* environment, so it shows **none**
> of the injected secrets even when they are live in `os.environ`. Do **not** conclude
> "`CLAUDE_CODE_OAUTH_TOKEN` is missing" from `/proc/environ` — use the live `claude --print` test
> above (run via `infisical run`, which is the faithful equivalent of the in-process injection).

> **`claude` binary location:** on the VPS it's `/home/ua/.local/bin/claude` (not on a
> non-interactive shell's default PATH). The VP worker invokes bare `claude` and relies on PATH;
> ad-hoc diagnostics must use the absolute path.

## Common mistakes — avoid these

1. **"The OAuth at `/home/ua/.claude/.credentials.json` expired, so Cody is broken."**
   Production Cody never reads that file. The relevant credential is `CLAUDE_CODE_OAUTH_TOKEN` in
   Infisical. Verify with the diagnostic above before refreshing. (This exact misread caused a
   demo to be wrongly deferred on 2026-06-02; the file has since been deleted.)

2. **"Set `ANTHROPIC_API_KEY` to the OAuth token so the CLI finds it."**
   The OAuth token is not a valid API key — Claude Code rejects it as `Invalid API key` if placed
   in the `ANTHROPIC_API_KEY` slot. It must go in `CLAUDE_CODE_OAUTH_TOKEN`, a different env var
   the CLI checks first. `_build_cli_env` deliberately strips `ANTHROPIC_*` before injecting it.

3. **"Sync the desktop and VPS `.credentials.json` so Cody works everywhere."**
   Production reads neither file. Sync them only if you want the same interactive `claude` session
   state on both machines (rarely worth it).

## Auth-failure handling in code

On a 401, `vp/clients/claude_cli_client.py::_is_auth_failure` detects the auth error and aborts
retries immediately (same env → same 401), returning a `failed` outcome carrying
`_AUTH_FAILURE_OPERATOR_HINT` — which points the operator at the refresh procedure above
(`claude setup-token`).

## Dead-weight cleanup

`/home/ua/.claude/.credentials.json` is leftover from an earlier interactive `claude /login`,
contributes nothing to production, and misleads auth diagnosis. Delete it whenever present:

```bash
ssh ua@uaonvps "rm -f /home/ua/.claude/.credentials.json"
```

It is recreated only if someone runs `claude` interactively as the `ua` user — at which point its
purpose is that new interactive session, not production.
