// turbo-all
# Dev Reset
Wipe local dev data and start fresh. Destructive.

## Steps
1. Stop the stack first:
   ```bash
   ./scripts/dev_down.sh
   ```
2. Confirm with user: "This will delete ~/universal_agent_local_data/ and AGENT_RUN_WORKSPACES/. Type 'reset' to confirm."
3. If confirmed:
   ```bash
   ./scripts/dev_reset.sh
   ```
4. Ask user if they want to restart: "Start fresh stack now? [y/N]"

## Rules
- ❌ NEVER reset without explicit user confirmation.
- ❌ NEVER touch VPS or production data.
