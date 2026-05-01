---
name: link-purchase
description: Use this skill when the user gives an explicit, parseable purchase intent — e.g. "buy X from Y for $Z", "pay $N to merchant.com", "complete the checkout at <url>". Generates a Link spend request, surfaces the Link approval URL for the user to tap-approve in the Link app, and handles the post-approval flow. NEVER triggers from offhand mentions like "I should buy that someday". The user must specify a merchant, amount, and what's being bought.
allowed-tools:
  - mcp__link__*
license: Internal
version: 0.1.0
metadata:
  author: universal_agent
  requires:
    env:
      - UA_ENABLE_LINK
      - UA_LINK_DEFAULT_PAYMENT_METHOD_ID
---

# Link payments — agent-facing skill

This skill requests a one-time-use Link payment credential on behalf of the
user. The user keeps full control: every spend request shows up as a push
notification in the Link mobile app or web wallet, and nothing is charged
until they tap **Approve**. Card details are NEVER shown in chat.

## When to use

Trigger only when the user has given an **explicit, parseable purchase intent**:

- A specific merchant (URL or unambiguous name).
- A specific amount (or a clear way to compute it).
- A specific item / line items / context.

Examples that should trigger:
- "Buy 'Working in Public' from press.stripe.com — it's $35."
- "Pay $9.99 to substack.com/subscribe to xyz."
- "Complete the checkout at powdur.com for the Glow Renewal Vitamin C Serum, $35."

Examples that should NOT trigger:
- "I should pick up some books sometime."
- "What's a good gift for my brother?"
- "How much does X cost?"

When in doubt, ask the user to confirm: merchant URL, exact amount,
one-sentence description.

## Required confirmation flow

Before calling any spend-request endpoint, **always restate the purchase
verbatim and require explicit "yes, create the spend request"
confirmation**:

```
I'm about to create a Link spend request:
  Merchant: <name> (<url>)
  Amount:   $<X>.<XX>
  Items:    <line items>
  Context:  <why we're buying this; the user reads this when approving>

Confirm "yes" to create the request, or tell me what to change.
```

This is a belt-and-suspenders gate on top of the bridge's caller allowlist
and Link's own approval flow. The user reviews three times: at this prompt,
at the Link app push notification, and again when card details are fetched
via the email/UI signed-URL.

## How to call

The skill prefers the local in-process FastAPI endpoints over direct Stripe
`mcp__link__*` MCP tools. That way, requests flow through the bridge's
guardrails, audit log, and notifier automatically.

### Create a spend request

```
POST /api/link/spend-requests
{
  "merchant_name": "Stripe Press",
  "merchant_url": "https://press.stripe.com",
  "amount_cents": 3500,
  "context": "Buying 'Working in Public' (hardcover) from Stripe Press at the user's direct request through the shopping assistant. Final amount includes any shipping or tax.",
  "currency": "usd",
  "credential_type": "card",
  "line_items": [
    {"name": "Working in Public", "unit_amount": 3500, "quantity": 1}
  ],
  "request_approval": true
}
```

Successful response (201) includes `data.id` (the `lsrq_...` spend request
id) and `data.approval_url` (where the user taps approve).

### Poll until terminal status

```
GET /api/link/spend-requests/{id}
```

Repeat until `data.status` is one of: `approved`, `denied`, `expired`.
**Do not** advance to the next step while still pending. Show the user the
approval URL on each poll so they know where to act.

### After approval

When status flips to `approved`, the bridge automatically fires an email
notification to the operator email (with a one-shot signed URL to the card
details page). The skill should:

1. Stop polling.
2. Tell the user: "Approved. Check your email or Mission Control for the
   one-shot card link." Card details are NEVER shown in chat.
3. If `UA_LINK_AUTO_CHECKOUT=1`, the `agent_purchaser` sub-agent will pick
   up checkout. Otherwise the user completes the merchant's checkout form
   themselves via the signed URL.

## Constraints

### Link API constraints (returned as `validation_*`)
- `context` must be ≥ 100 characters.
- `amount_cents` must be a positive integer ≤ 50000 ($500.00).
- `currency` must be a 3-letter ISO code (default "usd").

### UA bridge guardrails (returned as `guardrail_*`)
- Per-call cap: `UA_LINK_MAX_AMOUNT_CENTS` (default $50.00).
- Daily cap: `UA_LINK_DAILY_BUDGET_CENTS` (default $100.00, rolling 24h).
- Optional merchant allowlist: `UA_LINK_MERCHANT_ALLOWLIST` (off by default).

**Do NOT attempt to circumvent these by retrying or splitting amounts.**
Surface the error to the user and stop.

## Test mode

In test mode (`UA_LINK_TEST_MODE=1`, the default), Link returns testmode
credentials backed by card `4242424242424242`. No real money is charged.

## What this skill does NOT do

- Add new payment methods to the Link wallet. Direct the user to
  https://app.link.com/wallet for that.
- Auto-approve spend requests. The user must tap approve in the Link app.
- Display card details in chat. Card details only ever appear on the
  signed-URL page (one-shot, 15-min TTL).

## Error reference

| Error code | What it means | What to tell the user |
|---|---|---|
| `guardrail_disabled` | `UA_ENABLE_LINK` is off | Feature isn't enabled. Stop. |
| `guardrail_caller` | Caller not in allowlist | Env misconfigured; surface to ops. |
| `guardrail_per_call_cap` | Amount > local cap | Ask user to reduce or escalate to ops. |
| `guardrail_daily_cap` | Daily budget would be exceeded | Defer until tomorrow or escalate. |
| `guardrail_merchant_allowlist` | Merchant not allowlisted | Ops adds merchant or use a different one. |
| `validation_context` | Context < 100 chars | Rewrite with more detail. |
| `validation_amount` | Out of range | Fix the amount. |
| `cli_not_found` | Link CLI not installed on VPS | Ops issue; surface and stop. |
| `cli_timeout` | CLI didn't respond | Retry once, then surface. |
| `auth_unauthenticated` | Auth blob invalid/expired | Tell user: re-run `scripts/bootstrap_link_auth.sh` and reseed Infisical. |

## Example end-to-end

```
User:  Buy "Working in Public" from press.stripe.com, $35.

Agent: I'm about to create a Link spend request:
         Merchant: Stripe Press (https://press.stripe.com)
         Amount:   $35.00
         Items:    Working in Public × 1 ($35.00)
         Context:  Buying 'Working in Public' from Stripe Press at the
                   user's direct request through the shopping assistant.
       Confirm "yes" to create the request.

User:  yes

Agent: Created spend request lsrq_001 (status: pending_approval).
       Approve in your Link app: https://app.link.com/approve/lsrq_001

[user taps Approve in Link app]

Agent: Approved! Card details are in your email — one-shot link, 15 min TTL.
       Open the merchant page and complete checkout, or wait for the
       agent_purchaser to handle it (if auto-checkout is enabled).
```
