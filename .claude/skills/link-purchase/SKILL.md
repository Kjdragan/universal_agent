---
name: link-purchase
description: Use this skill when the user gives an explicit, parseable purchase intent — e.g. "buy X from Y for $Z", "pay $N to merchant.com", "complete the checkout at <url>". Generates a Link spend request, surfaces the Link approval URL for the user to tap-approve in the Link app, and handles the post-approval flow. NEVER triggers from offhand mentions like "I should buy that someday". The user must specify a merchant, amount, and what's being bought.
allowed-tools:
  - mcp__link__*
  - browser_subagent
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

Trigger when the user gives a purchase intent:

- A specific merchant (URL or unambiguous name).
- A specific amount (or a clear way to compute it).
- An item description OR a generic intent (e.g. "buy something").

Examples that should trigger:
- "Buy 'Working in Public' from press.stripe.com — it's $35."
- "Pay $9.99 to substack.com/subscribe to xyz."
- "Complete the checkout at powdur.com for the Glow Renewal Vitamin C Serum, $35."
- "Buy something from press.stripe.com for $1."

**CRITICAL:** If the user does not provide an exact item, you can use a generic placeholder like "Test Item" or "Requested Purchase" for the spend request. Later, you can instruct the `browser_subagent` to find an item that matches the requested price (e.g., "Find an item for $1"). Do NOT force the user to give you exact item names if they just want to test a transaction.

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

**Note:** Since this is a test request, Link will still display your real bank account/card on the approval screen, but NO real money will be charged. It will generate a test 4242 virtual card.

Confirm "yes" to create the request, or tell me what to change.
```

This is a belt-and-suspenders gate on top of the bridge's caller allowlist
and Link's own approval flow. The user reviews three times: at this prompt,
at the Link app push notification, and again when card details are fetched
via the email/UI signed-URL.

## How to call

Use the `run_command` tool to execute `npx @stripe/link-cli` commands. Do NOT use the local `/api/link` endpoints.

### 1. Create a spend request

First, fetch the default payment method ID:
```bash
npx @stripe/link-cli payment-methods list --format json
```
Extract the `id` from the payment method where `"is_default": true`.

Then, create the spend request using the CLI:
```bash
npx @stripe/link-cli spend-request create \
  --merchant-name "Stripe Press" \
  --merchant-url "https://press.stripe.com" \
  --context "Buying 'Working in Public' (hardcover) from Stripe Press at the user's direct request through the shopping assistant. Final amount includes any shipping or tax." \
  --amount 3500 \
  --line-item "name:Working in Public,unit_amount:3500,quantity:1" \
  --total "type:total,display_text:Total,amount:3500" \
  --payment-method-id "<default_payment_method_id>" \
  --test \
  --format json
```

*Note: The `--test` flag generates a test virtual card (4242...). You MUST provide a valid `--payment-method-id` even in test mode. Make sure the `--context` is at least 100 characters long.*

Extract the spend request ID (`lsrq_...`) from the JSON output.

### 2. Request approval and securely retrieve card details

To trigger the approval push notification and poll for the card details to be securely saved to a file, run this command in the background (WaitMsBeforeAsync ~ 1000):

```bash
npx @stripe/link-cli spend-request request-approval lsrq_001 && \
npx @stripe/link-cli spend-request retrieve lsrq_001 \
  --include card \
  --format json \
  --interval 2 \
  --max-attempts 150 > /tmp/link-card.json
```

* While it polls, tell the user to approve the request in the Link app.
* Periodically check `command_status` until the command completes successfully.

### 3. After approval: Complete checkout using Browser Subagent

Once status is `approved` and `/tmp/link-card.json` is successfully written, you must complete the checkout yourself.

- **CRITICAL RULE:** You MUST exclusively use the native `browser_subagent` tool to perform the checkout.
- **NEVER** use raw `bash` commands to run CLI browser wrappers (e.g., `npx agent-browser` or Playwright CLIs). CLI wrappers will crash due to system AppArmor sandbox restrictions.
- **DO NOT** read the card file contents into the chat transcript.
- Call `browser_subagent` with a detailed task description instructing it to read the card details securely and perform the checkout (e.g., "Navigate to press.stripe.com, find the $1 item, add it to the cart, read the card credentials from `/tmp/link-card.json`, and complete the checkout using these card details").

## Constraints

### Link API constraints (returned as `validation_*`)
- `context` must be ≥ 100 characters.
- `amount` must be a positive integer in cents ≤ 50000 ($500.00).
- `currency` must be a 3-letter ISO code (default "usd").

### UA bridge guardrails
*Note: Because we are using the Link CLI directly, local bridge guardrails no longer apply, but you should still honor reasonable limits.*

## Test mode

In test mode (adding `--test` to the `create` command), Link returns testmode
credentials backed by card `4242424242424242`. No real money is charged.

## What this skill does NOT do

- Add new payment methods to the Link wallet. Direct the user to
  https://app.link.com/wallet for that.
- Auto-approve spend requests. The user must tap approve in the Link app.
- Display card details in chat. Card details only ever appear securely in the
  `/tmp/link-card.json` file.

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
