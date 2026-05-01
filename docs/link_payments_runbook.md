# Link payments — operator runbook

This document is the source of truth for provisioning, flipping live mode,
and operating the Stripe Link payments integration on the production VPS.

> All secrets and runtime config live in **Infisical**. Never put production
> values in committed files. `.env.example` is documentation only.

## Architecture summary

| Layer | Code path | Purpose |
|---|---|---|
| Bridge | `tools/link_bridge.py` | Subprocess wrapper around `link-cli`, layered guardrails, audit log, stub mode |
| Health probe | `services/link_health.py` | Startup `auth status` + `payment-methods list` check, cached snapshot |
| Notifier | `services/link_notifier.py` | Idempotent AgentMail delivery on approval, issues card-page tokens |
| Card tokens | `services/link_card_tokens.py` | One-shot 15-min signed-URL tokens; never store card data |
| Reconciler | `services/link_reconciler.py` | Re-polls non-terminal spend requests, catches polling-timeouts |
| Purchaser | `services/link_purchaser.py` | Orchestrates browser-automated checkout via the agent-purchaser sub-agent |
| API routes | `api/link_routes.py` | `/api/link/*` + `/link/card/{token}` |
| Skill | `.claude/skills/link-purchase/SKILL.md` | Agent-facing trigger for purchase intent |
| Sub-agent | `.claude/agents/agent-purchaser.md` | Browser-automated checkout decision tree |

## One-time bootstrap (before first deploy)

Run on a workstation where you can interactively complete the Link
device-auth flow. You'll need a Link account and a payment method already
added at https://app.link.com/wallet.

```bash
bash scripts/bootstrap_link_auth.sh
```

The script:
1. Runs `link-cli auth login --client-name "Universal Agent (production)"` —
   prints a verification URL and phrase.
2. After you tap-approve in the Link app, detects the auth-blob file the
   CLI just wrote.
3. Prints the file path (use `$HOME` form) and a base64-encoded copy of
   the contents.

Paste both into Infisical (production environment):

| Infisical key | Source |
|---|---|
| `LINK_AUTH_BLOB` | The base64 string between the BEGIN/END markers |
| `UA_LINK_AUTH_BLOB_PATH` | The portable path (e.g. `$HOME/.config/link-cli-nodejs/config.json`) |

Then list your payment methods:

```bash
link-cli payment-methods list --format json
```

Pick the `id` (looks like `csmrpd_...`) of the card you want UA to use, and
add it to Infisical:

| Infisical key | Value |
|---|---|
| `UA_LINK_DEFAULT_PAYMENT_METHOD_ID` | `csmrpd_...` |

Optional but recommended:

| Infisical key | Value |
|---|---|
| `UA_LINK_OPERATOR_EMAIL` | The address that gets approval emails |
| `UA_LINK_DASHBOARD_BASE_URL` | Defaults to `https://app.clearspringcg.com` |

## Enabling test mode

After provisioning, flip the master switch in Infisical:

```
UA_ENABLE_LINK = 1
UA_LINK_TEST_MODE = 1   # default; explicit for clarity
UA_ENABLE_LINK_LIVE = 0 # default; do NOT flip until you've validated test mode
```

Restart / `/ship` an inert change. On startup:

- The Link MCP server appears in `mcp_servers_config`.
- The startup health probe runs and logs:
  `Link health probe: OK (mode=test, payment_methods=N, auth=...)`
- The `link-purchase` skill becomes available to the agent.
- `/api/link/*` endpoints become live.

In test mode, Link returns testmode credentials (card `4242424242424242`).
No real money is ever charged. Run a few test purchases end-to-end:

```bash
curl -X POST https://app.clearspringcg.com/api/link/spend-requests \
  -H "Content-Type: application/json" \
  -d '{
    "merchant_name": "Stripe Press",
    "merchant_url": "https://press.stripe.com",
    "amount_cents": 100,
    "context": "Test-mode validation purchase via UA bridge. Confirms full lifecycle works without real charges.",
    "currency": "usd"
  }'
```

You'll get a push notification in the Link app. Tap approve. The bridge
fires the email notifier with a signed URL to the card-details page.

## Flipping live mode

**Only do this after you've successfully run several test-mode purchases
end-to-end.** Live mode requires BOTH gates to be set:

```
UA_ENABLE_LINK = 1
UA_ENABLE_LINK_LIVE = 1
UA_LINK_TEST_MODE = 0    # <-- this is the second gate
```

If either is missing, the bridge stays in test mode regardless. Belt-and-
suspenders against accidental config changes.

After the change, restart / `/ship`. The startup banner will read:

```
Link health probe: OK (mode=live, ...)
```

`GET /api/link/health` will return `bridge_status.mode == "live"`.

The first live spend request you make will charge your real card behind the
scenes (Link networks-tokenizes a virtual card on top of your selected
payment method). Start small (e.g. $1.00) until you're confident.

## Day-to-day operations

### Watching health

```bash
curl https://app.clearspringcg.com/api/link/health
```

Returns:
```json
{
  "bridge_status": {
    "enabled": true,
    "live_mode": true,
    "mode": "live",
    "spent_today_cents": 1500,
    "merchant_allowlist": [],
    ...
  },
  "last_probe": {
    "ok": true,
    "auth_status": {"authenticated": true, "update_available": false},
    "payment_methods_count": 1,
    ...
  }
}
```

### Force a fresh health probe

```bash
curl -X POST https://app.clearspringcg.com/api/link/health/probe
```

### Run reconciler manually (for cron)

```bash
curl -X POST "https://app.clearspringcg.com/api/link/reconcile?max_per_tick=10"
```

Suggested cron cadence: every 60 seconds while there are active spend
requests; idle otherwise.

### Captcha budget snapshot (for the agent-purchaser sub-agent)

```bash
curl https://app.clearspringcg.com/api/link/checkout/captcha-budget
```

Returns `{"cap": 20, "used": 0, "remaining": 20, "window": "rolling_24h"}`.

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| Health probe fails with `cli_not_found` | Link CLI not installed on VPS | Add `npm i -g @stripe/link-cli` to deploy script, or set `UA_LINK_CLI_PATH` to a known binary |
| Health probe fails with `auth_unauthenticated` | Auth blob expired or revoked | Re-run `bootstrap_link_auth.sh` locally, paste new `LINK_AUTH_BLOB` into Infisical, restart |
| Health probe fails with `no_payment_methods` | Wallet is empty | Add a card at https://app.link.com/wallet, restart |
| Bridge returns `guardrail_disabled` to all callers | `UA_ENABLE_LINK=0` | Confirm Infisical value; restart |
| All spend requests stuck `pending_approval` | User isn't approving in Link app, or push notifications off | Check Link app notification settings; the user must tap approve |
| Email notifications missing | `UA_LINK_OPERATOR_EMAIL` unset, or AgentMail not configured | Set the env var; check AgentMail bridge health |
| `agent-purchaser` returns `fallback_no_dispatcher` | Harness not registering the dispatcher hook | Confirm `link_purchaser.set_dispatcher(...)` runs at startup; until then, manual completion via signed-URL still works |
| `agent-purchaser` returns `fallback_3ds` | Bank challenged the virtual card | Use the email signed-URL to complete 3DS manually; this is expected on some merchants |
| `agent-purchaser` returns `fallback_captcha_budget` | Daily NopeCHA quota exhausted from purchase flows | Wait or raise `UA_LINK_DAILY_CAPTCHA_BUDGET`; falls back to manual completion |

## Emergency: disable everything

In Infisical, set:

```
UA_ENABLE_LINK = 0
```

All bridge calls return `guardrail_disabled` immediately; no CLI calls go
out. The MCP server is not registered on next restart. Pending spend
requests in the Link app will time out naturally.

For a softer kill that leaves config intact but neuters real CLI calls
(useful for incident response without losing audit/notifier state):

```
UA_LINK_FORCE_STUB = 1
```

The bridge will return synthetic stub responses to all callers as if the
master switch were off, while keeping the rest of the runtime alive.

## Rotating the auth blob

Every few months, or after any incident:

1. Run `bootstrap_link_auth.sh` locally with a fresh `--client-name` (e.g.
   `"Universal Agent (production-2026Q2)"`).
2. The Link app shows the new connection. Approve.
3. Paste the new `LINK_AUTH_BLOB` into Infisical (overwriting the old).
4. In the Link app, revoke the prior connection.
5. `/ship` an inert change to restart with the new blob.

The bridge restores the new blob to disk on import; old auth stops working
the moment you revoke it in the Link app, regardless of restart timing.
