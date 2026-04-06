# NotebookLM VPS Runbook (Infisical-Backed)

This runbook documents the NotebookLM auth/bootstrap model for UA VPS deployments.

## Scope

- NotebookLM operations in UA runtime.
- Infisical-injected secret usage.
- Dedicated `vps` NotebookLM profile.

## Required Non-Secret Runtime Flags

- `UA_ENABLE_NOTEBOOKLM_MCP` (default `0`)
- `UA_NOTEBOOKLM_AUTH_SEED_ENABLED` (default `1` on VPS)
- `UA_NOTEBOOKLM_PROFILE` (default `vps`)
- `UA_NOTEBOOKLM_CLI_COMMAND` (default `nlm`)
- `UA_NOTEBOOKLM_MCP_COMMAND` (default `notebooklm-mcp`)

## Required Infisical Secrets

- `NOTEBOOKLM_AUTH_COOKIE_HEADER`
- Optional override: `NOTEBOOKLM_PROFILE`

## Required Host Tooling

- Install the unified upstream package: `notebooklm-mcp-cli`
- Required executables:
  - `nlm`
  - `notebooklm-mcp`
- Recommended install for the `ua` service user:
  - `uv tool install --force notebooklm-mcp-cli`
- Ensure runtime PATH includes `/home/ua/.local/bin`

## Bootstrap Flow

1. UA runtime initializes secrets through existing bootstrap:
   - `runtime_bootstrap.py`
   - `infisical_loader.py`
2. NotebookLM operator runs preflight:
   - `nlm login --check --profile vps`
3. If invalid and seeding is enabled:
   - write `NOTEBOOKLM_AUTH_COOKIE_HEADER` to temp file under `CURRENT_SESSION_WORKSPACE`
   - run `nlm login --manual --file <temp> --profile vps`
   - delete temp file immediately
4. Re-check auth and continue operations.

## Post-Install Verification

Run these checks as the same user that runs the UA services:

```bash
command -v nlm
command -v notebooklm-mcp
nlm login --check --profile vps
```

If `command -v nlm` fails, this is an installation or PATH problem, not an auth problem.

## Rotation Procedure

1. Update `NOTEBOOKLM_AUTH_COOKIE_HEADER` in Infisical.
2. Restart UA process (or trigger a fresh session bootstrap).
3. Run a NotebookLM auth preflight task and verify `nlm login --check --profile vps` succeeds.

## Alternative Auth Method: Direct Desktop Sync (Headless Bypass)

If the VPS is headless and cannot process the manual login flow effectively during setup or rotation, you can bypass the Infisical cookie-injection pipeline completely by syncing a valid local session:

1. Authenticate locally on your desktop machine (e.g., `uvx nlm login`).
2. Sync your local `notebooklm-mcp-cli` profile directly to the VPS `ua` user over SSH:

   ```bash
   rsync -avz ~/.notebooklm-mcp-cli/ ua@<VPS_IP>:/home/ua/.notebooklm-mcp-cli/
   ```

3. Run `nlm login --check --profile vps` (or `default`) on the VPS to verify the copied cookies are valid.

## Security Constraints

1. Never print or persist cookie header values.
2. Never commit NotebookLM auth artifacts to git.
3. Keep seed files ephemeral in the run workspace only.

## Failure Modes

1. Missing seed secret + expired auth:
   - operation blocks with `needs_confirmation`/`blocked` and asks operator to refresh secret.
2. CLI missing:
   - report missing `nlm` binary and stop.
3. MCP unavailable:
   - continue with CLI fallback path.
