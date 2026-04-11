// turbo-all
# Dev Up
Start the Universal Agent local dev stack.

## Steps
1. Confirm you are in the universal_agent repo root:
   ```bash
   git rev-parse --show-toplevel && pwd
   ```
2. Confirm Infisical bootstrap creds are set:
   ```bash
   env | grep -E '^INFISICAL_(CLIENT_ID|CLIENT_SECRET|PROJECT_ID)=' | sed 's/=.*/=<set>/'
   ```
   All three must show `<set>`. If any missing, STOP.
3. Run the startup script:
   ```bash
   ./scripts/dev_up.sh
   ```
4. Wait 5 seconds, verify services:
   ```bash
   sleep 5
   curl -sf http://localhost:8001/health >/dev/null 2>&1 && echo "api: ok" || echo "api: FAIL"
   curl -sf http://localhost:8002/health >/dev/null 2>&1 && echo "gateway: ok" || echo "gateway: FAIL"
   curl -sf http://localhost:3000 >/dev/null 2>&1 && echo "webui: ok" || echo "webui: FAIL"
   ```
5. Report result.

## Rules
- ❌ Do NOT start Telegram bot or VP workers locally.
- ❌ Do NOT SSH to the VPS.
- ✅ Always verify all three services before declaring success.
