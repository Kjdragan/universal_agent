# Local Development Guide

Run Universal Agent on your desktop at `http://localhost:3000`, iterate with fast feedback, and push to production only when you're happy.

> [!IMPORTANT]
> Local dev runs **completely independently** of the VPS. The VPS keeps running 24/7 regardless of whether local dev is up or down. They share no databases, no processes, no state.

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.12+ | Pre-installed on most systems |
| `uv` | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node.js | 22+ | `nvm install --lts` (auto-sourced by `dev_up.sh`) |
| `infisical` | 0.40+ | `curl -1sLf 'https://dl.cloudsmith.io/public/infisical/infisical-cli/setup.deb.sh' \| sudo bash && sudo apt-get install infisical` |

## One-time setup

### 1. Infisical bootstrap credentials

Add these to your `~/.bashrc` (the values are in your password manager):

```bash
export INFISICAL_CLIENT_ID="st.xxxxx..."
export INFISICAL_CLIENT_SECRET="st.yyyyy..."
export INFISICAL_PROJECT_ID="proj-xxxxx..."
```

Then reload: `source ~/.bashrc`

### 2. Verify the `local` Infisical environment

The `local` environment must exist in Infisical with appropriate overrides for your desktop. The following values should differ from `production`:

| Key | Value | Purpose |
|-----|-------|---------|
| `UA_RUNTIME_STAGE` | `local` | Identifies this as local dev |
| `UA_DEPLOYMENT_PROFILE` | `local_workstation` | Distinguishes from VPS |
| `UA_MACHINE_SLUG` | `local-dev` | Logs/metrics identity |
| `INFISICAL_ENVIRONMENT` | `local` | In-app SDK env reference |

All shared secrets (API keys, tokens, credentials) remain the same as production.

### 3. Optional: shell `cd` hook

Add this to `~/.bashrc` for a reminder when you enter the repo:

```bash
_ua_cd_wrapper() {
    builtin cd "$@" || return
    if [[ "$PWD" == *"universal_agent"* ]] && [[ "$OLDPWD" != *"universal_agent"* ]]; then
        echo ""
        echo "  universal_agent local dev:"
        echo "    ./scripts/dev_up.sh     — start local stack"
        echo "    ./scripts/dev_down.sh   — stop local stack"
        echo ""
    fi
}
alias cd=_ua_cd_wrapper
```

---

## Daily workflow

```bash
# Start
./scripts/dev_up.sh

# Work, iterate, test at http://localhost:3000
# The web-ui has hot-reload — changes appear instantly.
# Python backend changes require a restart (dev_down + dev_up).

# Stop when done
./scripts/dev_down.sh

# Check status at any time
./scripts/dev_status.sh

# Nuclear option: wipe all local data
./scripts/dev_reset.sh
```

---

## How local differs from VPS

| Aspect | Local | VPS (Production) |
|--------|-------|-------------------|
| Infisical env | `local` | `production` |
| Runtime stage | `local` | `production` |
| Ports | 8001, 8002, 3000 | 8001, 8002, 3000 |
| Databases | `AGENT_RUN_WORKSPACES/` in repo root | VPS-specific path |
| Telegram bot | ❌ Not started | ✅ Running |
| VP workers | ❌ Not started | ✅ Running |
| Secrets | `infisical run` (in-memory) | systemd `EnvironmentFile` |
| Web-UI | `next dev` (hot-reload) | `next start` (built) |

---

## Architecture

```
dev_up.sh
  ├── Authenticates to Infisical (universal-auth)
  ├── Renders web-ui/.env.local from Infisical (accepted exception)
  ├── Starts gateway (port 8002) via infisical run
  ├── Starts api    (port 8001) via infisical run  
  └── Starts webui  (port 3000) via next dev

dev_down.sh
  ├── Reads /tmp/ua-local-dev.pids
  ├── SIGTERM each process (5s grace)
  ├── SIGKILL if still alive
  └── Verifies ports are free
```

### Secret handling

No plaintext application secrets are written to disk. The pattern:

1. Three bootstrap vars live in `~/.bashrc` (Infisical auth only)
2. `dev_up.sh` authenticates to Infisical and gets a session token
3. Each service is started inside `infisical run --env=local` which injects secrets as env vars
4. When the process exits, the secrets vanish from memory

**Exception:** `web-ui/.env.local` is rendered at startup with server-side env vars the Next.js runtime needs. This is acceptable because:
- The file is gitignored
- It's regenerated every `dev_up.sh` run
- `NEXT_PUBLIC_*` vars are browser-visible anyway (not truly secret)
- Server-side vars like `UA_DASHBOARD_OPS_TOKEN` need to be in the Next.js process env

---

## Troubleshooting

### "Missing Infisical bootstrap env vars"
Ensure `INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`, and `INFISICAL_PROJECT_ID` are exported in your shell. Run `env | grep INFISICAL_` to verify.

### "Ports already in use"
Run `./scripts/dev_down.sh` first. If that doesn't help:
```bash
lsof -i :8001 :8002 :3000
```
Kill any leftover processes manually.

### "node: command not found"
`dev_up.sh` auto-sources nvm. If node is still missing:
```bash
export NVM_DIR="$HOME/.nvm"
source "$NVM_DIR/nvm.sh"
nvm install --lts
```

### Service crashes immediately
Check the log:
```bash
tail -50 /tmp/ua-local-logs/gateway.log
tail -50 /tmp/ua-local-logs/api.log
tail -50 /tmp/ua-local-logs/webui.log
```

### Stale PID file
If `dev_status.sh` shows "dead (stale PID)" entries, run `dev_down.sh` to clean up.

### "ECONNRESET" loops during Next.js local dev
If your dashboard shows continuous connection reset errors on websocket connections during local development with Turbopack, ensure the `NEXT_PUBLIC_API_URL` or equivalent setting targets the actual backend port (e.g. `localhost:8001`) directly. Turbopack HMR bypasses standard Next.js path routing (`rewrites`) for WebSocket connections, meaning the client must know the direct backend port.

---

## Security notes

- **No secrets on disk.** Bootstrap creds are in your shell profile only. Application secrets are injected via `infisical run`.
- **Never hardcode secrets** in command-line args or script files. Always use `${VAR}` substitution.
- **The `local` and `production` Infisical environments are independent copies.** When you rotate a key in `production`, you must also update it in `local`.

---

## Known limitations

- Python backend changes require a full restart (`dev_down` + `dev_up`). There is no auto-reload for the Python services.
- The Telegram bot and VP workers are **not** started locally to avoid conflicts with the VPS instances.
- `local` and `production` databases are completely separate and never sync. A session created locally will not appear on the VPS and vice versa.
