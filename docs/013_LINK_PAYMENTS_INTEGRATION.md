# 013 — Link Payments Integration

> One-time-use card and shared-payment-token (SPT) credentials from a Stripe Link wallet, surfaced to UA agents through guardrails, an approval flow, and optional browser-automated checkout. Master switch defaults OFF — feature is fully inert until provisioned in Infisical.

## Overview

The Link payments integration lets Universal Agent request a one-time-use payment credential on the user's behalf from their [Link](https://link.com/agents) wallet. The user keeps full control: every spend request shows up as a push notification in the Link mobile app or web wallet, and nothing is charged until they tap **Approve**. Card details never appear in chat — they're rendered on a one-shot signed-URL page hosted by Mission Control, with copy-to-clipboard buttons for manual entry. When automated checkout is enabled, the `agent-purchaser` sub-agent walks the merchant's checkout form via Playwright (with chained captcha-solver and residential-proxy fallbacks).

The integration is structured as a **five-layer stack**:

| Layer | Asset | Purpose |
|-------|-------|---------|
| **CLI bridge** | `src/universal_agent/tools/link_bridge.py` | Subprocess wrapper around `@stripe/link-cli`, layered guardrails, audit log, stub-mode |
| **Health probe** | `src/universal_agent/services/link_health.py` | Startup `auth status` + `payment-methods list` check, cached snapshot |
| **Notifier + tokens** | `src/universal_agent/services/link_notifier.py` + `link_card_tokens.py` | Idempotent AgentMail delivery on approval, one-shot 15-min signed URLs |
| **API surface** | `src/universal_agent/api/link_routes.py` | `/api/link/*` CRUD + `/link/card/{token}` page |
| **Agent surface** | `.claude/skills/link-purchase/SKILL.md` + `.claude/agents/agent-purchaser.md` | Skill triggers spend-request creation; sub-agent drives Playwright checkout |

Plus two background services that close the loop:

| Service | File | Purpose |
|---|---|---|
| **Reconciler** | `src/universal_agent/services/link_reconciler.py` | Re-polls non-terminal spend requests; catches `POLLING_TIMEOUT` and out-of-band approvals |
| **Purchaser** | `src/universal_agent/services/link_purchaser.py` | Orchestrates browser-automated checkout, captcha-budget tracking, per-request idempotency |

```mermaid
flowchart TD
    USER[User: 'buy X from Y for $Z'] --> SKILL[link-purchase skill]
    SKILL -->|POST /api/link/spend-requests| ROUTES[link_routes.py]
    ROUTES -->|create_spend_request| BRIDGE[link_bridge.py]
    BRIDGE -->|guardrails| GUARD{caller? cap? budget? merchant?}
    GUARD -->|pass| CLI[link-cli subprocess]
    GUARD -->|fail| ERR[typed error → audit log]
    CLI --> LINK[Stripe Link API]
    LINK -->|push notification| PHONE[User's Link app]
    PHONE -->|tap Approve| LINK
    BRIDGE -->|on retrieve approved| NOTIFIER[link_notifier.py]
    NOTIFIER -->|issue token| TOKENS[link_card_tokens.py]
    NOTIFIER -->|email + signed URL| AGENTMAIL[AgentMail]
    AGENTMAIL --> EMAIL[Operator email]
    EMAIL -->|click link| PAGE[/link/card/token page]
    PAGE -->|consume token| BRIDGE
    BRIDGE -->|retrieve include_card| LINK
    PAGE --> HUMAN[Human: copy card → merchant checkout]

    BRIDGE -.if AUTO_CHECKOUT.-> PURCHASER[link_purchaser.py]
    PURCHASER -->|dispatch| SUBAGENT[agent-purchaser sub-agent]
    SUBAGENT -->|Playwright| MERCHANT[Merchant checkout]
    SUBAGENT -.captcha.-> CAPTCHASOLVE[captcha-solver skill]
    SUBAGENT -.IP block.-> PROXY[residential-proxy skill]

    RECONCILER[link_reconciler.py] -.cron.-> BRIDGE

    INFISICAL[Infisical secrets] -->|LINK_AUTH_BLOB| BRIDGE
    INFISICAL -->|UA_ENABLE_LINK + caps + flags| BRIDGE
```

---

## 1. CLI bridge & guardrails

**File**: `src/universal_agent/tools/link_bridge.py`

Wraps the Stripe Link CLI as a subprocess. Every call goes through five sequential guardrails before any CLI dispatch — fail-closed at every layer:

| # | Guardrail | Purpose |
|---|---|---|
| 1 | `master_switch` | `UA_ENABLE_LINK=1` required. Otherwise everything returns `guardrail_disabled` and no subprocess is spawned. |
| 2 | `caller_allowlist` | Caller must be in `("chat", "ui", "skill:link-purchase", "ops", "test")`. Proactive services and arbitrary agents cannot spend. |
| 3 | `validation_*` | `context >= 100` chars; `1 <= amount_cents <= 50000`; 3-letter ISO currency. Mirrors Link API constraints. |
| 4 | `per_call_cap` | `UA_LINK_MAX_AMOUNT_CENTS` (default $50). |
| 5 | `daily_cap` | Rolling 24h sum of attempted amounts ≤ `UA_LINK_DAILY_BUDGET_CENTS` (default $100). |
| 6 | `merchant_allowlist` | Optional comma-separated hostnames in `UA_LINK_MERCHANT_ALLOWLIST` (off by default). |

### Bridge modes

`_bridge_mode()` returns one of three states:

| Mode | Trigger | Behavior |
|---|---|---|
| `stub` | `UA_ENABLE_LINK=0` OR `UA_LINK_FORCE_STUB=1` | Returns canned synthetic responses; subprocess never invoked. Used for dev + as ops escape hatch. |
| `test` | Master on, no live gate | Real CLI dispatch with `--test` flag. Link returns testmode credentials backed by card `4242…` — no real money. |
| `live` | `UA_ENABLE_LINK=1` AND `UA_ENABLE_LINK_LIVE=1` AND `UA_LINK_TEST_MODE=0` | Real CLI dispatch without `--test`. Real spend. **Requires both gates flipped — single-edit accidents fail safe to test mode.** |

### Auth-blob restoration

The Link CLI's `auth login` is interactive (device-flow). Production cannot run that on the VPS, so we use the **NotebookLM-style auth seed pattern**: operator runs `scripts/bootstrap_link_auth.sh` once locally, captures the resulting auth-blob file, base64-encodes it, pastes into Infisical as `LINK_AUTH_BLOB`. On bridge import, `_ensure_auth_seeded()` decodes the blob and writes it to `UA_LINK_AUTH_BLOB_PATH` with `0o600` perms before any CLI call. Idempotent across multiple imports.

### Audit log

Every bridge call appends a JSONL row to `AGENT_RUN_WORKSPACES/link_audit.jsonl`:

```json
{"audit_id":"audit_…","ts":1234567890,"ts_iso":"2026-…","mode":"test",
 "event":"create_attempt","caller":"ui","amount_cents":3500,
 "merchant_url":"https://press.stripe.com","credential_type":"card",
 "spend_request_id":"lsrq_…","guardrail_blocked":null,
 "cli_exit_code":0,"error":null}
```

**PAN/CVC never appear in the log** — only `last4`. Retention defaults to 90 days (`UA_LINK_AUDIT_RETENTION_DAYS`).

---

## 2. API surface

**File**: `src/universal_agent/api/link_routes.py` (mounted in `api/server.py`)

### Authenticated endpoints (sit behind dashboard auth)

| Method + Path | Purpose |
|---|---|
| `GET /api/link/health` | `bridge_status` + `last_probe` snapshot |
| `POST /api/link/health/probe` | Force a fresh health probe |
| `GET /api/link/spend-requests` | Recent `create_attempt` rows from audit log |
| `POST /api/link/spend-requests` | Create new (UI form / chat skill) |
| `GET /api/link/spend-requests/{id}` | Live retrieve via CLI |
| `POST /api/link/spend-requests/{id}/refresh` | Force re-poll; fires notifier on transition |
| `POST /api/link/spend-requests/{id}/checkout` | Trigger `agent-purchaser` (browser-automated checkout) |
| `GET /api/link/checkout/captcha-budget` | Captcha-solver usage snapshot for sub-agent |
| `POST /api/link/reconcile` | Run one reconciler pass (cron entrypoint) |
| `POST /api/link/mpp/decode` | Parse 402 `WWW-Authenticate` → network_id |
| `POST /api/link/mpp/pay` | Settle approved SPT spend request server-to-server |

### Unauthenticated endpoint (token IS the credential)

| Method + Path | Purpose |
|---|---|
| `GET /link/card/{token}` | One-shot card-details HTML page. Token is single-use, 15-min TTL. Sets `Cache-Control: no-store`, `X-Robots-Tag: noindex`, `Referrer-Policy: no-referrer`. |

The card page renders PAN, expiration, CVC, billing address, and a "Continue to merchant" button — each sensitive field has a **Copy** button. Card details are fetched fresh from Link CLI on consume; **no card data is ever written to disk by this layer**.

---

## 3. Notifier & card tokens

**Files**: `services/link_notifier.py`, `services/link_card_tokens.py`

When `link_bridge.retrieve_spend_request()` sees a status transition to `approved`, it invokes `link_notifier.maybe_notify_from_retrieve()`:

1. **Idempotency check**: notifier consults `AGENT_RUN_WORKSPACES/link_notifications.json` — fires only once per `spend_request_id`.
2. **Token mint**: `link_card_tokens.issue(spend_request_id)` returns a `tok_…` token bound to that request, with TTL = `UA_LINK_SIGNED_URL_TTL_SECONDS` (default 900s).
3. **Email build**: subject `"Approved: $X.XX to <merchant>"`, body contains the `https://app.clearspringcg.com/link/card/<token>` link — **never the card itself**.
4. **Delivery**: tries `agentmail_official.send_message` first; falls back to a structured logfire log when AgentMail isn't configured.

The token store enforces **one-shot semantics atomically**: `consume()` flips the `consumed` flag in the same write that returns success. A second call returns `{"ok": false, "code": "already_consumed"}`. Tokens never store card data — only the spend_request_id and TTL. Card data is fetched live on token consume.

---

## 4. Reconciler

**File**: `src/universal_agent/services/link_reconciler.py`

A stateless single-tick poller that catches up on non-terminal spend requests. Triggered via:

```bash
curl -X POST "https://app.clearspringcg.com/api/link/reconcile?max_per_tick=10"
```

Reads the audit log for the last `lookback_hours` (default 48), de-duplicates by `spend_request_id`, skips ids whose last observed status is terminal (`approved`/`denied`/`expired`/`succeeded`/`failed`), and calls `retrieve_spend_request()` for each candidate (bounded by `max_per_tick`, default 10 per tick). Lets the notifier hook fire on transitions.

**Use cases**: catches `POLLING_TIMEOUT` exits, out-of-band approvals (user approved hours later), and brief network failures during the original create poll. Designed to be invoked from cron at ~60s cadence.

Disabled by default when `UA_ENABLE_LINK=0`. Explicit kill via `UA_LINK_RECONCILER_DISABLED=1`.

---

## 5. Agent surface — skill + sub-agent

### `link-purchase` skill

**File**: `.claude/skills/link-purchase/SKILL.md`

Triggers on **explicit, parseable purchase intent** only — never on offhand mentions like "I should buy that someday." The skill enforces a mandatory user-confirmation flow: the agent must restate the purchase verbatim (merchant, URL, amount, line items, context) and require explicit "yes" before calling `POST /api/link/spend-requests`. This is a belt-and-suspenders gate on top of:

- The bridge's `caller_allowlist`
- The Link app's tap-to-approve UX
- The signed-URL one-shot card page

So a user reviews the request **three times** before any card is minted. The skill also prefers UA's HTTP API endpoints over direct `mcp__link__*` MCP tools, ensuring all calls flow through the bridge guardrails and audit log.

### `agent-purchaser` sub-agent

**File**: `.claude/agents/agent-purchaser.md`

Invoked after a spend request transitions to `approved` AND `UA_LINK_AUTO_CHECKOUT=1` AND credential type is `card` (SPT bypasses checkout entirely via `mpp_pay`). The sub-agent walks the merchant's checkout via the existing `agent-browser` skill, with a layered fallback chain:

| Failure mode | Recovery |
|---|---|
| HTTP 403/503 before page renders | Chain to `residential-proxy` skill, retry |
| Cloudflare/reCAPTCHA/hCaptcha challenge | Check `/api/link/checkout/captcha-budget` first; if `remaining > 0`, chain to `captcha-solver`, load cookies, retry. Otherwise, fall back to manual. |
| 3DS / SCA challenge | Always fall back to manual (3DS is fundamentally human-in-the-loop) |
| Unknown form structure | One mapping attempt by `cc-*` autocomplete + name/id heuristics; on failure, fall back |
| Unhandled exception or > 5min total | Fall back |

**One attempt per spend request, ever** — the card is single-use. Card details are passed in-memory to the sub-agent and never written anywhere. The orchestration module (`link_purchaser.py`) records only `{ok, status, evidence path, last4}` to `AGENT_RUN_WORKSPACES/link_purchaser_attempts.json`.

The captcha-solver budget (`UA_LINK_DAILY_CAPTCHA_BUDGET=20` default, rolling 24h) protects research flows that share the NopeCHA daily quota from being starved by a runaway purchase loop.

---

## 6. Safety properties

The integration has **multiple independent safety layers**. A failure in any one does not compromise the others:

| Layer | Defense |
|---|---|
| Master switch | `UA_ENABLE_LINK=0` → no subprocess invocation, ever (verified: `subprocess.run` never called) |
| Force-stub | `UA_LINK_FORCE_STUB=1` → soft kill while keeping config intact |
| Caller allowlist | Proactive services / scheduler / arbitrary agents cannot create spend requests |
| Per-call cap | One bad request can't exceed $50 (default) without ops raising the cap |
| Daily cap | Sum of all today's attempted spend ≤ $100 (default) — even denied attempts count |
| Live double-gate | `UA_ENABLE_LINK_LIVE=1` AND `UA_LINK_TEST_MODE=0` both required for real money. Single-edit accidents → test mode. |
| Test mode default | Card `4242…` until explicitly flipped — no real charges during validation |
| Link API ceiling | Hard 50000¢ ($500) max enforced by Link itself |
| User approval (Link app) | Push notification + tap-to-approve — Stripe's UX, not bypassable |
| **Card scoping** | Stripe issues virtual cards **merchant-locked + amount-locked + short-expiry** via Issuing for Agents — even leaked credentials can only charge that one merchant for that one amount |
| Card hygiene | PAN/CVC never written to disk in audit log, receipt store, attempt store, or token store |
| Signed-URL TTL | Card-details page tokens are one-shot, 15-min expiry |
| Notifier idempotency | `link_notifications.json` ensures one approval email per spend_request_id |
| Purchaser idempotency | `link_purchaser_attempts.json` ensures one checkout attempt per spend_request_id |
| Captcha budget | Purchase-driven NopeCHA usage capped at 20/day (configurable) |

---

## 7. End-to-end demo walkthrough

### Prerequisites (one-time)

1. Have a [Link account](https://app.link.com) with at least one payment method added at https://app.link.com/wallet.
2. Have node + npm available locally (for the bootstrap script).
3. Have access to Infisical for the production environment.

### Bootstrap (5 minutes, one-time)

On your **local workstation** (not the VPS):

```bash
bash scripts/bootstrap_link_auth.sh
```

The script runs `link-cli auth login`, prints a verification URL and short phrase. Open the URL on your phone, log in to Link, enter the phrase. After approval, the script detects the auth blob, prints its file path and a base64 of its contents.

In Infisical (production env), set:

```
UA_LINK_AUTH_BLOB_PATH = $HOME/.config/link-cli-nodejs/config.json   # use the path the script printed
LINK_AUTH_BLOB         = <base64 string from the script output>
```

Then locally, list your Link payment methods to find the one to use:

```bash
link-cli payment-methods list --format json
```

Pick the `id` (looks like `csmrpd_…`) and add it to Infisical:

```
UA_LINK_DEFAULT_PAYMENT_METHOD_ID = csmrpd_…
UA_LINK_OPERATOR_EMAIL            = you@example.com   # optional but recommended
```

### Enable test mode

In Infisical:

```
UA_ENABLE_LINK    = 1
UA_LINK_TEST_MODE = 1   # default; explicit for clarity
# Leave UA_ENABLE_LINK_LIVE unset / 0 for now
```

`/ship` an inert change to deploy. On startup the logs will show:

```
Link health probe: OK (mode=test, payment_methods=1, auth={'authenticated': True, ...})
```

### Demo flow A — Card flow (the common case)

**Step 1.** Trigger via chat:

> User: "Buy 'Working in Public' from press.stripe.com — it's $35."

**Step 2.** The `link-purchase` skill activates and the agent restates the purchase:

> Agent: "I'm about to create a Link spend request:
> - Merchant: Stripe Press (https://press.stripe.com)
> - Amount: $35.00
> - Items: Working in Public × 1 ($35.00)
> - Context: Buying 'Working in Public' from Stripe Press at the user's direct request through the shopping assistant.
>
> Confirm 'yes' to create the request."

**Step 3.** User confirms. The agent calls:

```http
POST /api/link/spend-requests
{
  "merchant_name": "Stripe Press",
  "merchant_url": "https://press.stripe.com",
  "amount_cents": 3500,
  "context": "Buying 'Working in Public' from Stripe Press at the user's direct request through the shopping assistant.",
  "currency": "usd"
}

→ 201 Created
{
  "ok": true,
  "data": {
    "id": "lsrq_test_001",
    "status": "pending_approval",
    "approval_url": "https://app.link.com/approve/lsrq_test_001",
    ...
  },
  "audit_id": "audit_…",
  "mode": "test"
}
```

**Step 4.** The user gets a push notification in the Link app showing the spend request with merchant context. They tap **Approve**.

**Step 5.** The agent (or the reconciler at the next 60s cron tick) polls:

```http
GET /api/link/spend-requests/lsrq_test_001
→ 200 { "data": { "status": "approved", ... }, "mode": "test" }
```

**Step 6.** The notifier hook fires. The operator gets an email:

> Subject: ✅ Approved: $35.00 to Stripe Press
> [View card details] (https://app.clearspringcg.com/link/card/tok_…)
> One-shot link, expires in 15 minutes.

**Step 7.** The operator clicks the link. The card-details page renders:

```
Card number: 4242 4242 4242 4242   [Copy]
Expiration:  12/30                 [Copy]
CVC:         314                   [Copy]
Billing address:
  ...
[Continue to merchant →]
```

**Step 8.** Operator clicks "Continue to merchant", pastes the card details into Stripe Press's checkout form, completes the purchase. (In test mode, no money moves — Stripe's test merchant ack-and-forgets.)

**A second click on the card-details URL returns a 410 Gone** — the token is one-shot.

### Demo flow B — Browser-automated checkout (when `UA_LINK_AUTO_CHECKOUT=1`)

Steps 1–5 are identical. After the bridge sees the approval, instead of (or in addition to) emailing the operator, the runtime calls:

```http
POST /api/link/spend-requests/lsrq_test_001/checkout
→ 200 { "ok": true, "status": "completed", "evidence": "...screenshot path..." }
```

…and the `agent-purchaser` sub-agent runs through:

1. Open https://press.stripe.com via `agent-browser`.
2. Navigate to checkout, fill `cc-*` fields with the card.
3. Submit. Wait for confirmation page text ("thank you", "order placed").
4. Capture screenshot, return success.

If anything fails (captcha exhausted, 3DS challenge, unknown form), it returns `202 Accepted` with `status: "fallback_*"` and the operator falls back to the email/signed-URL flow.

### Demo flow C — MPP / 402 server-to-server (for compatible merchants)

For merchants that support [Machine Payments Protocol](https://mpp.dev) (HTTP 402), there's no checkout form at all:

**Step 1.** Probe the merchant URL, get a 402 with a `WWW-Authenticate` header. Decode it:

```http
POST /api/link/mpp/decode
{ "challenge": "Payment id=\"ch_001\", method=\"stripe\", request=\"…\"" }
→ 200 { "data": { "network_id": "net_…", "method": "stripe", ... } }
```

**Step 2.** Create a spend request with `credential_type: "shared_payment_token"` and the `network_id`. User approves in the Link app.

**Step 3.** Settle:

```http
POST /api/link/mpp/pay
{
  "spend_request_id": "lsrq_…",
  "url": "https://climate.stripe.dev/api/contribute",
  "method": "POST",
  "data": { "amount": 100 }
}
→ 200 { "data": { "response": { "status": 200, ... } } }
```

No checkout form, no card details, no operator click — fully agentic for opted-in merchants.

### Going live

After running several test-mode purchases successfully, in Infisical:

```
UA_ENABLE_LINK_LIVE = 1
UA_LINK_TEST_MODE   = 0
```

`/ship` again. Startup logs flip to:

```
Link health probe: OK (mode=live, ...)
```

The first live spend request charges the underlying card (your real Visa/etc. that backs the Link wallet) via a network-tokenized virtual card minted by Stripe for that merchant only. Start with $1.00.

### Health check anytime

```bash
curl https://app.clearspringcg.com/api/link/health
```

Returns the bridge status (mode, today's spent total, guardrail config) plus the latest health probe result (auth state, payment-method count). The runbook at `docs/link_payments_runbook.md` has the full failure-mode → fix table.

---

## 8. Operator runbook

See **[`docs/link_payments_runbook.md`](link_payments_runbook.md)** for the full operator manual:

- One-time bootstrap (`bootstrap_link_auth.sh` → Infisical)
- Test-mode and live-mode flip procedures (double-gate explanation)
- Day-to-day operations (health endpoint, reconcile cron, captcha-budget snapshot)
- Failure-mode → fix lookup table
- Emergency disable (hard kill via master switch, soft kill via `UA_LINK_FORCE_STUB`)
- Auth blob rotation procedure

---

## 9. Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `UA_ENABLE_LINK` | `0` | Master switch. Off → entire feature inert (no MCP, no CLI calls, all guardrails return `guardrail_disabled`). |
| `UA_ENABLE_LINK_LIVE` | `0` | Live-mode gate 1. Required for real spend. |
| `UA_LINK_TEST_MODE` | `1` | Live-mode gate 2 — must be `0` for live. Default-on means accidental enable stays test. |
| `UA_LINK_FORCE_STUB` | `0` | Ops + test escape hatch — forces stub mode regardless of master switch. |
| `LINK_AUTH_BLOB` | (unset) | Base64-encoded auth blob from `bootstrap_link_auth.sh`. |
| `UA_LINK_AUTH_BLOB_PATH` | (unset) | Where the bridge restores the auth blob on startup. |
| `UA_LINK_AUTH_SEED_ENABLED` | `1` | Whether to attempt auth-blob restoration. |
| `UA_LINK_DEFAULT_PAYMENT_METHOD_ID` | (unset) | `csmrpd_…` id used for spend requests when caller doesn't specify. |
| `UA_LINK_MAX_AMOUNT_CENTS` | `5000` | Per-call ceiling ($50). |
| `UA_LINK_DAILY_BUDGET_CENTS` | `10000` | Rolling 24h cap ($100). |
| `UA_LINK_MERCHANT_ALLOWLIST` | (empty) | Comma-separated allowed merchant hostnames. Empty = no filter. |
| `UA_LINK_ENTRY_CHAT` | `1` | Allow chat entry-point. |
| `UA_LINK_ENTRY_UI` | `1` | Allow Mission Control form entry-point. |
| `UA_LINK_ENTRY_SKILL` | `1` | Allow `link-purchase` skill entry-point. |
| `UA_LINK_AUTO_CHECKOUT` | `1` | Run `agent-purchaser` after approval (vs. email-only). |
| `UA_LINK_DAILY_CAPTCHA_BUDGET` | `20` | Max captcha-solver invocations per day from purchase flows. |
| `UA_LINK_SIGNED_URL_TTL_SECONDS` | `900` | Card-details page TTL (15 min). |
| `UA_LINK_AUDIT_RETENTION_DAYS` | `90` | Audit log retention. |
| `UA_LINK_RECONCILER_INTERVAL_SECONDS` | `30` | Suggested cron cadence (the `/reconcile` endpoint is stateless; cadence is set by your cron). |
| `UA_LINK_RECONCILER_DISABLED` | `0` | Kill switch for the reconciler endpoint. |
| `UA_LINK_OPERATOR_EMAIL` | (unset) | Where approval emails route (falls back to `UA_OPERATOR_EMAIL`). |
| `UA_LINK_DASHBOARD_BASE_URL` | `https://app.clearspringcg.com` | Base URL used in card-page links. |
| `UA_LINK_CLI_PATH` | (auto-detect) | Override link-cli binary path. |
| `UA_LINK_AUDIT_PATH` / `UA_LINK_CARD_TOKENS_PATH` / `UA_LINK_NOTIFIER_STATE_PATH` / `UA_LINK_CAPTCHA_USAGE_PATH` / `UA_LINK_PURCHASER_ATTEMPTS_PATH` | (under `AGENT_RUN_WORKSPACES/`) | Path overrides for state files (used in tests). |

**All values live in Infisical for production.** `.env.example` documents the names but the file itself is reference-only — no secrets, no real config in the repo.

---

## 10. Test coverage

| Module | Test file | Coverage focus |
|---|---|---|
| Bridge guardrails | `tests/link/test_link_bridge_guardrails.py` | All 5 guardrails + validation + master switch |
| Audit log | `tests/link/test_link_audit.py` | JSONL shape, no-PAN/CVC invariant, daily window |
| Subprocess | `tests/link/test_link_subprocess.py` | CLI argv build, JSON parsing, error mapping, npx fallback |
| Health probe | `tests/link/test_link_health.py` | Stub skip, auth-fail, unauthenticated, empty wallet, success |
| Card tokens | `tests/link/test_link_tokens.py` | Issue/peek/consume/expire, one-shot semantics, no card data |
| Notifier | `tests/link/test_link_notifier.py` | Idempotency, card URL build, no PAN/CVC in body |
| Routes | `tests/link/test_link_routes.py` | All HTTP endpoints, status codes, card page rendering |
| Reconciler | `tests/link/test_link_reconciler.py` | Disabled paths, terminal skip, max_per_tick bound, dedup |
| Purchaser | `tests/link/test_link_purchaser.py` | Disabled, idempotency, fallbacks, captcha budget, no PAN persisted |
| MPP | `tests/link/test_link_mpp.py` | mpp_decode + mpp/pay routes, live-mode flip |

**Total: 140+ pytest cases across 10 files.** All tests run in stub mode by setting `UA_LINK_FORCE_STUB=1` so they exercise guardrails + audit + state without invoking the real CLI.

---

## 11. Lessons learned (during build)

- **Two-gate live mode is worth the friction.** Single-edit `UA_ENABLE_LINK_LIVE=1` could otherwise flip real spend on accidentally; requiring `UA_LINK_TEST_MODE=0` *also* be flipped means a typo or rebase mishap fails safe. The runbook is explicit that both must be set.
- **Card scoping changes the threat model.** Stripe Issuing-for-agents virtual cards are merchant-locked + amount-locked + short-expiry. This is what made browser-automated checkout safe enough to ship default-on — even a wrong-merchant fill cannot be exploited.
- **Tokens never store card data.** The signed-URL token authorizes a fresh `link-cli retrieve --include=card` at view time. Card data exists only in process memory + the response body, never on disk.
- **The reconciler is the safety net for `POLLING_TIMEOUT`.** Link CLI's exit code on poll timeout is *not* a denial — the spend request is still pending. Without the reconciler, an out-of-band approval (user taps after the create-poll exits) would silently never trigger the notifier.
- **The captcha budget split protects research flows.** NopeCHA's 100/day quota is shared across UA. Capping purchase-driven captcha calls at 20/day means a misbehaving purchase loop can't starve the research/scraping pipelines.
- **Stripe's own skill says "Enter these into the merchant's checkout form."** The `card` flow always requires *something* (human or automation) to type the card number into a checkout. SPT/MPP is the only fully-agentic settlement path, and it requires merchant opt-in. Both paths are first-class in the integration.
