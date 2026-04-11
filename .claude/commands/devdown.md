// turbo-all
# Dev Down
Stop the local dev stack cleanly.

## Steps
1. Run:
   ```bash
   ./scripts/dev_down.sh
   ```
2. Verify ports are free:
   ```bash
   for port in 3000 8001 8002; do
     lsof -i ":$port" >/dev/null 2>&1 && echo "port $port: STILL IN USE" || echo "port $port: free"
   done
   ```
3. Report result.

## Rules
- ❌ Do NOT wipe local data — use /devreset for that.
- ❌ Do NOT touch the VPS.
