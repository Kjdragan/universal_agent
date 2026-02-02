# 043 — Telegram Gateway Client Implementation Plan (2026‑02‑02)

## Goal
Make Telegram run as a **true gateway client** (Option C) so it always uses the same execution engine, tool registry, and guardrails as the Web UI and CLI‑via‑gateway, while keeping a controlled dev escape hatch.

## Current State (Summary)
- Telegram bot uses `AgentAdapter` which defaults to **InProcessGateway** when `UA_GATEWAY_URL` is not set.
- This creates a **second execution engine** inside the bot service.
- Railway build fails due to `pycairo` (pulled by `manim`) requiring a compiler toolchain.

## Implementation Steps

### Phase 1 — Enforce External Gateway (Option C)
1. **Config flag**: Introduce `UA_TELEGRAM_ALLOW_INPROCESS` env var (default `0`).
2. **Adapter guard**: If `UA_GATEWAY_URL` is missing and `UA_TELEGRAM_ALLOW_INPROCESS` is false, **fail fast** with a clear error.
3. **Debug visibility**: Print `UA_GATEWAY_URL` + `UA_TELEGRAM_ALLOW_INPROCESS` in bot startup logs.
4. **Local dev escape hatch**: Update `start_telegram_bot.sh` to set `UA_TELEGRAM_ALLOW_INPROCESS=1` for local testing.

### Phase 2 — Railway Build Fix (Bot Reliability)
5. **Docker build tools**: Add compiler toolchain + Cairo dev headers to support `pycairo` and `manimpango` builds:
   - `build-essential`, `pkg-config`, `libcairo2-dev`, `libpango1.0-dev`
6. **Keep Chrome install**: Required for HTML→PDF headless in gateway runs.

### Phase 3 — Deployment/Env Expectations
7. **Bot service env vars** (Railway or VPS):
   - `UA_GATEWAY_URL=https://<gateway-host>`
   - `TELEGRAM_BOT_TOKEN=<token>`
   - `WEBHOOK_URL=https://<bot-host>/webhook`
   - `WEBHOOK_SECRET=<secret>`
   - `ALLOWED_USER_IDS=<id>`
8. **Gateway service** must be always on and reachable by bot.

### Phase 4 — Validation Checklist
9. Start bot with `UA_GATEWAY_URL` set → confirm **ExternalGateway** usage in logs.
10. Send `/agent <prompt>` → confirm task executes on gateway and returns in Telegram.
11. Confirm session mapping: `tg_<user_id>` persists across messages.
12. Verify webhook registration on bot host is successful.

## Risks / Notes
- Option C introduces **dependency on gateway uptime** (expected tradeoff).
- Build fix keeps the container large; future optimization can split bot dependencies.

## Deliverables
- Code changes to enforce external gateway + dev escape hatch
- Dockerfile update to unblock Railway builds
- Updated Telegram startup script behavior

