# Claude Max OAuth Credentials — Canonical Reference

**Last updated:** 2026-05-27
**Owners:** platform + interactive coding
**Audience:** anyone touching Cody-on-Anthropic mode, the `claude` CLI, or `~/.claude/.credentials.json` on any UA machine.

## TL;DR — single source of truth

**Production Cody-on-Anthropic-Max uses `CLAUDE_CODE_OAUTH_TOKEN` from Infisical. Nothing else.**

- The interactive `claude` CLI on Kevin's desktop / VPS uses `~/.claude/.credentials.json` for its OWN session. That file is **NOT** the credential production reads. The two stores are deliberately independent.
- If a Cody mission 401s with `Authentication failed`, the credential to refresh is `CLAUDE_CODE_OAUTH_TOKEN` in Infisical — NOT any `.credentials.json` file.
- `~/.claude/.credentials.json` on the VPS at `/home/ua/.claude/.credentials.json` is **legacy / orphan state** from an earlier interactive `claude /login`. Nothing in production reads it. Delete it whenever you want; it'll be recreated only if someone runs `claude` interactively as the `ua` user on the VPS.

## Architecture: three independent OAuth stores

| Store | What | Refreshed by | Read by |
|---|---|---|---|
| **Infisical `CLAUDE_CODE_OAUTH_TOKEN`** | Long-lived OAuth token (`sk-ant-oat01-…`, ~108 chars) produced by `claude setup-token` | Operator, manually, when it expires (token TTL is long; refresh is infrequent) | The `claude` CLI subprocess spawned by every Cody mission in Anthropic mode |
| **`/home/kjdragan/.claude/.credentials.json`** (Kevin's desktop) | `claudeAiOauth` access + refresh token for the desktop interactive session | The `claude` CLI itself, automatically, every interactive session | Kevin's `claude` CLI when he's coding locally |
| **`/home/ua/.claude/.credentials.json`** (VPS, orphan) | Stale `claudeAiOauth` from an old VPS interactive session (May 16 timestamp) | NOTHING — no auto-refresh process runs there | NOTHING in production |

**Why we don't try to mirror them**: they serve different audiences (machine-to-machine vs. interactive-user) and have different refresh mechanics (manual `setup-token` vs. automatic OAuth refresh chain). Trying to keep them in sync invites the kind of confusion that wasted operator time on 2026-05-27 morning when a stale `/home/ua/.claude/.credentials.json` was misread as a live production credential failure.

## The production credential flow (Cody-on-Anthropic)

```
Operator runs `claude setup-token` (once when token expires)
        │
        ▼ "Use this token by setting: export CLAUDE_CODE_OAUTH_TOKEN=<token>"
        │
        ▼ Operator pastes into Infisical as CLAUDE_CODE_OAUTH_TOKEN
        │
        ▼ Gateway / VP-worker daemon boots
        │
        ▼ initialize_runtime_secrets()  → os.environ['CLAUDE_CODE_OAUTH_TOKEN'] = ...
        │
        ▼ Cody mission with cody_mode='anthropic' arrives
        │
        ▼ claude_cli_client._build_cli_env():
        │   1. strips every ANTHROPIC_* var from the spawned subprocess env
        │      (so a stale ANTHROPIC_API_KEY can't take precedence)
        │   2. sets env['CLAUDE_CODE_OAUTH_TOKEN'] = os.environ['CLAUDE_CODE_OAUTH_TOKEN']
        │   3. sets env['CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS'] = '1'
        │
        ▼
   `claude` CLI subprocess inherits the env, authenticates via OAuth token.
   It NEVER reads ~/.claude/.credentials.json on this path.
```

Source-of-truth: [`src/universal_agent/vp/clients/claude_cli_client.py`](../../src/universal_agent/vp/clients/claude_cli_client.py) lines 858–890.

## How to refresh the token

When Cody missions start 401-ing with `Authentication failed` from `api.anthropic.com`:

1. On any machine that can run `claude` (Kevin's desktop, OR the VPS via `ssh ua@uaonvps`), run:
   ```bash
   claude setup-token
   ```
   It prints a fresh `sk-ant-oat01-…` token (do not copy the leading text — only the token value).

2. Save it into Infisical (production environment):
   ```bash
   # On any machine with machine-id Infisical creds (e.g. the VPS):
   TOK=$(infisical login --method=universal-auth \
          --client-id="$INFISICAL_CLIENT_ID" \
          --client-secret="$INFISICAL_CLIENT_SECRET" --plain --silent)
   INFISICAL_TOKEN="$TOK" infisical secrets set CLAUDE_CODE_OAUTH_TOKEN="<paste-token-here>" \
     --projectId="$INFISICAL_PROJECT_ID" --env=production
   ```

3. Restart the services so they pick up the new env via Infisical bootstrap:
   ```bash
   ssh ua@uaonvps "sudo systemctl restart \
       universal-agent-gateway \
       universal-agent-vp-worker@vp.coder.primary \
       universal-agent-vp-worker@vp.general.primary"
   ```

That's it. The next Cody mission spawned after restart will use the fresh token.

## Diagnostic recipe

If a Cody mission 401s and you want to verify it's a token problem (vs. a network problem, vs. a Composio problem, vs. a ZAI problem):

```bash
# Check the token Infisical hands out (prefix + length only — never print the full value)
ssh ua@uaonvps "set -a; source /opt/universal_agent/.env; set +a; \
  TOK=\$(infisical login --method=universal-auth \
        --client-id=\"\$INFISICAL_CLIENT_ID\" \
        --client-secret=\"\$INFISICAL_CLIENT_SECRET\" --plain --silent); \
  INFISICAL_TOKEN=\$TOK infisical run --projectId=\"\$INFISICAL_PROJECT_ID\" --env=production --silent -- \
    bash -c 'echo prefix=\${CLAUDE_CODE_OAUTH_TOKEN:0:8} len=\${#CLAUDE_CODE_OAUTH_TOKEN}'"
```

Expected: `prefix=sk-ant-o len=108`. If `len=0` or the prefix is anything else, the token in Infisical is wrong/missing — re-run `claude setup-token` and store the result.

**Do NOT** look at `/home/ua/.claude/.credentials.json` for this — that file is orphan state and tells you nothing about production auth.

## Common mistakes — avoid these

1. **Mistake**: "The OAuth at `/home/ua/.claude/.credentials.json` expired so Cody is broken."
   **Reality**: production Cody never reads that file. The relevant credential is `CLAUDE_CODE_OAUTH_TOKEN` in Infisical. Verify with the diagnostic above before refreshing.

2. **Mistake**: "Let's set `ANTHROPIC_API_KEY` to the OAuth token so claude CLI can find it."
   **Reality**: the OAuth token is not a valid API key — Claude rejects it as `Invalid API key · Fix external API key` if presented in the `ANTHROPIC_API_KEY` slot. Use `CLAUDE_CODE_OAUTH_TOKEN` (a different env var the CLI checks first). `claude_cli_client._build_cli_env` deliberately strips `ANTHROPIC_*` before injecting `CLAUDE_CODE_OAUTH_TOKEN` to prevent this exact confusion.

3. **Mistake**: "Both the desktop and VPS need to have the same `~/.claude/.credentials.json` so Cody works everywhere."
   **Reality**: they don't. Production Cody doesn't read either file. Sync them only if you want the same interactive `claude` session state on both machines (rare; usually not worth it).

## Dead-weight cleanup

`/home/ua/.claude/.credentials.json` on the VPS is leftover from an earlier interactive `claude /login`. It contributes nothing to production and **misleads anyone diagnosing auth problems**. Recommend deleting:

```bash
ssh ua@uaonvps "rm -f /home/ua/.claude/.credentials.json"
```

If someone later runs `claude` interactively as the `ua` user, the file will be recreated — but at that point its purpose is the new interactive session, not production.

## References

- Code: [`src/universal_agent/vp/clients/claude_cli_client.py:858-890`](../../src/universal_agent/vp/clients/claude_cli_client.py) — `_build_cli_env` for Anthropic mode
- Postmortem: [`docs/operations/2026-05-26_goal_smoke_test_result.md`](../operations/2026-05-26_goal_smoke_test_result.md) — the 2026-05-26 OAuth-refresh-chain failure that motivated the move to `claude setup-token`
- Sibling docs:
  - [`10_Interactive_Coding_Environment.md`](10_Interactive_Coding_Environment.md) — the desktop+VPS interactive-coding side
  - [`09_Demo_Execution_Environments.md`](09_Demo_Execution_Environments.md) — demo workspaces (same `CLAUDE_CODE_OAUTH_TOKEN` mechanism)
