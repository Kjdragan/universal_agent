# `/goal` VPS Smoke Test Result — 2026-05-26

**Status:** **BLOCKED — operator re-auth required before `/goal` can be verified**
**PRD step:** Step 1 of [`12_VP_Goal_Integration_And_Failure_Rescue_PRD.md`](../01_Architecture/12_VP_Goal_Integration_And_Failure_Rescue_PRD.md) § 6
**Test date:** 2026-05-26 ~05:00 UTC
**Tested by:** Claude session "Build out vp flows"

## What was attempted

Three escalating tests, all from `ua@uaonvps`:

| # | Test | Result |
|---|---|---|
| 1 | `claude -p "/goal echo done"` in `/tmp/goal-test-fresh` | `Failed to authenticate. API Error: 401 Invalid authentication credentials` |
| 2 | `claude --print --dangerously-skip-permissions "say hello"` from `/opt/universal_agent` | Same 401 |
| 3 | Direct invocation of `_execute_cli_session(cody_mode="anthropic")` (the exact code path production Cody uses) via `vp/clients/claude_cli_client.py` | Same 401, exit code 1 |

Test 3 used the production VP CLI client path with `cody_mode="anthropic"` so `_build_cli_env` scrubs `ANTHROPIC_*` env vars and the subprocess falls through to workspace-local OAuth — exactly mirroring how production Cody invocations work today.

## What this means

The Anthropic OAuth access token at `/home/ua/.claude/.credentials.json` shows `expiresAt: 1778923529644` = 2026-05-16 09:25 UTC (decoded via `date -d @1778923529`). That's **10 days expired**. The credentials file mtime is 2026-05-16 01:25, meaning the last successful in-file refresh was 8 hours before that token's expiry — i.e. the refresh chain stopped working around that time.

This **does NOT mean production Cody has been broken for 10 days**. Recent successful Cody-on-Anthropic missions:

| `completed_at` (UTC) | `cody_mode` | Status |
|---|---|---|
| 2026-05-25 16:01 | anthropic | completed |
| 2026-05-25 15:56 | anthropic | completed |
| 2026-05-25 09:39 | anthropic | completed |
| 2026-05-25 09:14 | anthropic | **failed** |

So Anthropic-mode Cody was working ~13h before this test and failed once same day. The most plausible explanation is that an in-memory refresh chain inside the long-running VP worker process was keeping a working token alive for production invocations, but headless `claude --print` invocations from outside that process can't access it. This matches the warning already documented in [`claude_cli_client.py:67-73`](../../src/universal_agent/vp/clients/claude_cli_client.py#L67): *"The Anthropic OAuth access token on this host has likely expired and headless `claude --print` does not refresh it."*

The four Cody-Anthropic missions today might have been the last ones the in-memory chain could service before it too lost the refresh thread.

## What `/goal` setup question is still unanswered

The 401 happens **before** any `/goal` logic engages. Therefore we still do NOT know:

- Whether `/goal` is blocked by workspace-trust dialog in fresh autonomous workspaces (the highest-leverage open question from the PRD)
- Whether the evaluator small-fast-model resolves cleanly on production OAuth
- Whether the auto-mode flow-through works through the `--dangerously-skip-permissions` flag the way the doc implies

These all require successful auth to test, which requires operator action.

## Operator action required

When at the keyboard, run **one** of:

```bash
# Option A (recommended for headless): long-lived setup token
ssh ua@uaonvps
PATH=/home/ua/.local/bin:$PATH claude setup-token

# Option B: refresh OAuth via interactive browser flow
ssh ua@uaonvps
PATH=/home/ua/.local/bin:$PATH claude /login
```

Option A produces a long-lived token suitable for headless `claude --print` invocations and is the preferred path for UA's autonomous use. Option B refreshes the OAuth credentials in-place.

Once re-auth is done, re-run the smoke test by:

```bash
ssh ua@uaonvps
PATH=/home/ua/.local/bin:$PATH cd /opt/universal_agent
uv run python /tmp/goal_test_script.py  # script left on VPS
```

Three expected outcomes:

| Outcome | Meaning | Next step |
|---|---|---|
| `/goal` runs, DONE.txt is created with "ok", status=completed | `/goal` works in fresh autonomous workspaces — green light for Step 4 `/goal` wiring | Flip `UA_VP_GOAL_ENABLED=1` (added in Step 4) |
| `/goal` errors with "trust dialog not accepted" or similar | Workspace trust blocks `/goal` in autonomous spawn — Step 4 needs a trust-acceptance mechanism added | Investigate per-user trust toggle or trusted-parent-path config |
| Other unexpected failure | Different root cause | Investigate per error |

## Why this didn't block the rest of the work

The PRD's Step 4 (`/goal` integration) was the only piece that needed empirical confirmation. Step 2 (failure-rescue), Step 3 (CC-Simone centralization), and Step 5 (HEARTBEAT/PROMPT reconciliation) have no `/goal` dependency. Self-briefing (the larger half of Step 4) also has no `/goal` dependency.

Implementation proceeded as follows:

- Steps 2, 3, 5: implemented fully and shipped
- Step 4: self-briefing skill + `BRIEF.md`/`ACCEPTANCE.md`/`goal_condition.txt`/`COMPLETION.md` artifact path implemented fully; the `/goal` invocation path is **feature-flagged OFF by default** (`UA_VP_GOAL_ENABLED=0`). Once operator re-auth + smoke test passes, flipping the flag to 1 enables the loop with no code change required.

This preserves the architectural work while honoring the "verify before depending on it" rule that gates `/goal` adoption.

## References

- PRD: [`12_VP_Goal_Integration_And_Failure_Rescue_PRD.md`](../01_Architecture/12_VP_Goal_Integration_And_Failure_Rescue_PRD.md)
- Auth failure detection code: [`claude_cli_client.py:60-95`](../../src/universal_agent/vp/clients/claude_cli_client.py#L60)
- VP CLI client invocation: [`claude_cli_client.py:271-310`](../../src/universal_agent/vp/clients/claude_cli_client.py#L271)
- Credentials file: `/home/ua/.claude/.credentials.json` (chmod 600, mtime 2026-05-16 01:25)
- Test script (left on VPS for re-running): `/tmp/goal_test_script.py`
