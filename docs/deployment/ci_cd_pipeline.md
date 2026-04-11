# CI/CD Pipeline & Troubleshooting

Our CI/CD pipeline is built on GitHub Actions and automates PR review and production deployment over Tailscale.

## Canonical Rule

This is the only supported app deployment path in this repository.

- `feature/latest2` is the active work branch.
- Open a pull request to `develop` to run Devin automated review and CI checks. `develop` is for integration and review only.
- Fast-forward `main` to the validated `develop` SHA to deploy to production automatically.
- Do not treat `scripts/deploy_vps.sh`, `scripts/vpsctl.sh`, or manual SSH deploy steps as the primary deployment path.

Release verification rule:
- prove release state by deployed `HEAD` SHA plus live behavior
- do not assume a missing fix solely from branch expectations or `git branch --show-current` output on the VPS checkout

## Workflows

### Primary Deployment Workflows

| Name | Trigger | Target |
|------|---------|--------|
| `Devin PR Review` | Pull request to `develop` | Automated PR review by Devin |
| `Deploy` | Push to `main` | Production Service |

### Utility Workflows

| Name | Trigger | Purpose |
|------|---------|---------|
| `Nightly Doc Drift Audit` | Scheduled (daily) | Detect documentation drift via auto-merged PR |
| `OpenClaw Release Sync` | Scheduled | Syncs OpenClaw updates |

## Current Targets

| Area | Production |
|------|------------|
| Git branch | `main` |
| VPS checkout | `/opt/universal_agent` |
| Gateway/API ports | `8002` / `8001` |
| Web UI port | `3000` |
| Web UI URL | `https://app.clearspringcg.com` (Public)<br>`https://uaonvps` (Tailnet) |
| API URL | `https://api.clearspringcg.com` (Public)<br>`https://uaonvps:8443` (Tailnet) |
| Legacy/fallback checkout | `/opt/universal_agent_repo` if `/opt/universal_agent` is occupied by a non-git legacy directory |
| Runtime secrets | `production` via explicit bootstrap `.env` plus stage secret validation |

## Infisical Runtime Lanes

The runtime model is stage-based:

- `development`
- `production`

Machine identity is written locally during bootstrap and validated during deploy:

- `FACTORY_ROLE`
- `UA_DEPLOYMENT_PROFILE`
- `UA_RUNTIME_STAGE`
- `UA_MACHINE_SLUG`

Deploy workflows must not provision machine-shaped Infisical environments during normal deploys.
They rewrite the checkout bootstrap `.env` from scratch on every deploy so
stale historical lines cannot survive a lane migration.

## Canonical Systemd Units

Deploy workflows own both the application checkout and the base systemd units that run it.

- Canonical unit templates live under `deployment/systemd/templates/`.
- `scripts/install_vps_systemd_units.sh` renders those templates against the active checkout path and installs them into `/etc/systemd/system/`.
- Production deploy installs `universal-agent-gateway`, `universal-agent-api`, `universal-agent-webui`, and `universal-agent-telegram` along with VP workers.
- Gateway/API stack-limit drop-ins are installed alongside the rendered base units during the same step.

This is intentional: deploys must not rely on manually created host-only base units whose `WorkingDirectory`, `ExecStart`, or `EnvironmentFile` can drift from the checked-out release.

## Required GitHub Secrets

- `DEVIN_API_KEY` (Devin PR review workflow)
- `TAILSCALE_OAUTH_CLIENT_ID` (Tailscale OAuth API client ID, tag identity `tag:ci-gha`)
- `TAILSCALE_OAUTH_SECRET` (Tailscale OAuth API client secret)
- `VPS_SSH_HOST`
- `VPS_SSH_USER`
- `VPS_SSH_KEY`
- `INFISICAL_CLIENT_ID`
- `INFISICAL_CLIENT_SECRET`
- `INFISICAL_PROJECT_ID`

## Required Tailscale Policy Model

CI runs must authenticate as a dedicated tagged principal and use non-interactive SSH authorization.

### Tags

- CI runner tag: `tag:ci-gha`
- VPS tag: `tag:vps`

### SSH Policy (required)

```json
{
  "ssh": [
    { "action": "accept", "src": ["tag:ci-gha"], "dst": ["tag:vps"], "users": ["root", "ua"] }
  ]
}
```

### Network Policy (required)

Allow `tag:ci-gha` to reach `tag:vps` on TCP/22 in your current ACL/grants model.

## Pipeline Steps (using `/ship` slash command)

1. **Commit & Push** on `feature/latest2`.
2. **Open PR to `develop`**.
3. **Devin PR Review** runs automated checks (does not block).
4. **Merge to `develop`** once PR passes auto-merge limits.
5. **Fast-forward `main`** to point to the new `develop` commit.
6. **Production deploy** triggers automatically on push to `main` via Github Actions.
7. **Post-release verification should use the deployed checkout SHA**. If production appears to be missing a fix, confirm the VPS `HEAD` commit before reopening code investigation or assuming a deploy gap.

## Lessons From The April 5 Dashboard Incident

An intermediate theory during the dashboard return-crash investigation was that production must still be on an older UI SHA. That theory was wrong.

What actually mattered:

1. production was already on the newer dashboard fix SHA
2. the remaining browser-specific crash was driven by persisted browser state
3. the deploy-gap theory was disproven only after checking the live VPS checkout `HEAD`

Operational rule:
- do not use "production is probably behind" as a debugging shortcut
- verify the deployed SHA first, then debug the live runtime state that still differs

## Bootstrap Identity Written By Deploys

### Production VPS

- `INFISICAL_ENVIRONMENT=production`
- `UA_RUNTIME_STAGE=production`
- `FACTORY_ROLE=HEADQUARTERS`
- `UA_DEPLOYMENT_PROFILE=vps`
- `UA_MACHINE_SLUG=vps-hq-production`
- `UA_GATEWAY_PORT=8002`
- `UA_API_PORT=8001`
- `UA_GATEWAY_URL=http://127.0.0.1:8002`

The bootstrap file written by deploys is intentionally minimal. Stage-shared
runtime config and secrets are loaded from Infisical after bootstrap validation.
Current deploy workflows serialize that bootstrap file deterministically during
deploy rather than editing keys in place.

## Deployed Runtime Tooling

- Production deploy installs project dependencies with `uv sync`.
- Deploy runs the shared helper `scripts/deploy_validate_runtime.sh` after writing the lane bootstrap `.env`.
- That helper performs the validation contract:
  1. ensure Python 3.12 is available to `uv`
  2. run `uv sync`
  3. run `scripts/validate_runtime_bootstrap.py`
  4. run `scripts/verify_observability_runtime.py`
  5. run `scripts/verify_service_imports.py`
  6. if any of those checks fail, delete `.venv`, do one clean `uv sync`, and rerun the full validation sequence
  7. if validation still fails, abort deploy before any service restart
- `scripts/verify_observability_runtime.py` is stricter than the runtime fail-open bootstrap: deploy success requires a real `logfire` import and a healthy OpenTelemetry context entry-point load, not just the ability to limp forward on the stub.
- Deploy rebuilds the Next.js `universal-agent-webui` application via `npm run build`. `npm install` is **conditional** — it only re-runs when `package.json` has changed since the last deploy (detected via a mtime sentinel file `node_modules/.package-json-mtime`). The `.next` build cache persists on the VPS between deploys, so incremental Next.js builds are fast.
- Deploy rebuilds the MkDocs documentation site via `mkdocs build`. The generated static site is served by the `universal-agent-docs` systemd unit on `localhost:8100`, exposed to the tailnet via `tailscale serve`. See `scripts/configure_docs_server.sh` for one-time setup.
- Deploy installs the external NotebookLM tool package `notebooklm-mcp-cli` for the `ua` service user via `uv tool install --force notebooklm-mcp-cli`.
- This provides the `nlm` CLI and `notebooklm-mcp` server binaries expected by the NotebookLM runtime.
- Deploy installs the `goplaces` CLI tool (v0.3.0) for the `ua` service user by downloading the release binary from GitHub to `/home/ua/.local/bin/goplaces`.
- Installation is idempotent.

## Expected Deploy Times

| Scenario | Production |
|----------|------------|
| First deploy on a fresh VPS (cold npm build) | ~20–25 min |
| Normal deploy — no `package.json` change | ~8–12 min |
| Deploy after `package.json` change (fresh npm install) | ~15–20 min |

The deploy workflow has `timeout-minutes: 35` to accommodate the worst-case cold build. Normal deploys complete well within 15 minutes.

To force a full `npm install` on the next deploy, delete the sentinel on the VPS:

```bash
rm /opt/universal_agent/web-ui/node_modules/.package-json-mtime
```

## Service Restart on Deploy

Every deploy pulls code, syncs dependencies, rebuilds the web UI, and then **restarts all managed systemd services**. This is the mechanism that ensures the running gateway, API, web UI, and other services pick up new code.

### Systemd Unit Names

| Service | Production Unit |
|---------|------------------|
| Gateway | `universal-agent-gateway` |
| API | `universal-agent-api` |
| Web UI | `universal-agent-webui` |
| Docs | `universal-agent-docs` |
| Telegram | `universal-agent-telegram` |
| VP Worker (coder) | `universal-agent-vp-worker@vp.coder.primary` |
| VP Worker (general) | `universal-agent-vp-worker@vp.general.primary` |

### Restart Order

**Production**: gateway + API + webui + telegram restarted together, then each enabled VP worker is restarted individually.

VP workers are only restarted if `systemctl is-enabled` reports them as active.

Before those restarts, the deploy re-renders and installs the canonical base units from the repository so the restart always targets the current checkout path and env files.

### Deployment-Window Flag

The workflow sets `/tmp/ua-deployment-window` before restarting services and clears it after. This flag exists so the CSI canary can suppress SLO alerts during the brief service restart window.

> [!IMPORTANT]
> **Code changes only take effect after the service restarts.** If the deploy workflow completes but services are not restarted (e.g., `systemctl` is not available), the gateway will continue running the old code. The workflow logs a warning in this case.

## Post-Deploy Health Verification

After a deploy completes, verify the services are running correctly:

### Quick Check (from any Tailscale-connected machine)

```bash
# Production gateway health
curl -s http://100.106.113.93:8002/api/v1/health
```

### On the VPS

```bash
# Check service status
sudo systemctl status universal-agent-gateway universal-agent-api universal-agent-webui

# Tail gateway logs for errors
sudo journalctl -u universal-agent-gateway -n 50 --no-pager
```

### Verify Latest Code Is Running

The gateway lifespan initialization can take 2–3 minutes. If the health endpoint is unavailable immediately after deploy, wait and retry.

To confirm the gateway is running the expected code, check the process start time against the deploy timestamp:

```bash
ps -eo pid,lstart,cmd | grep gateway_server | grep -v grep
```

## Local Development Restart Caveat

> [!WARNING]
> The CI/CD pipeline only restarts **systemd-managed services on the VPS**. If you are running the gateway, API, or dev server **locally** as a direct process, deploying does **not** restart your local process. You must restart it manually to pick up new code.

## Review and Promotion Rule

- There is exactly one review gate: the PR into `develop`.
- Direct pushes to `main` without PR flow are restricted for safety.
- The `Deploy` workflow triggers on push to `main` preventing the need for workflow dispatch APIs.
- To make the review gate enforceable, configure GitHub branch protection on `develop` to require status checks before merge.

## Temporary Missing-Secret Behavior

If `DEVIN_API_KEY` is not configured yet:
- the `Devin PR Review` workflow posts a warning or fails fast.
- the PR can still merge to `develop` since Devin PR reviews are non-blocking.

Once `DEVIN_API_KEY` is configured, Devin reviews provide helpful code advice.

## Recommended GitHub Branch Protection

Configure these settings in GitHub repository settings.

### `develop`

- Require a pull request before merging
- Optionally require Devin reviews to complete
- The `Nightly Doc Drift Audit` workflow creates auto-merged PRs using `gh pr merge --squash --admin`.

### `main`

- Do not require PR reviews on `main`.
- Restrict direct pushes except for trusted operators/workflow fast-forwards.

## Troubleshooting

### Deploy Job Times Out

`deploy.yml` has `timeout-minutes: 35`.

If a deploy times out, the most common cause is a cold `npm run build` with no existing `.next` cache on the VPS. This happens on the very first deploy to a fresh server or after `node_modules` is wiped. Simply re-run the workflow.

### SSH Preflight Fails Fast

If preflight exits before deploy, inspect error output in the workflow log.

#### Signature: interactive Tailscale check

If stderr includes either:

- `Tailscale SSH requires an additional check`
- `https://login.tailscale.com/...`

then CI identity is not matching the required non-interactive SSH policy. Verify permissions.

### SSH Key or VPS Authentication Fails

- Verify the `VPS_SSH_KEY` secret in GitHub.
- Ensure the matching public key is present in target user `authorized_keys`.
- Check SSH auth logs on the VPS for rejected keys.

### Tailscale Connection Issues

- Ensure `TAILSCALE_OAUTH_CLIENT_ID` and `TAILSCALE_OAUTH_SECRET` are valid.
- Verify ACL/grants permit runner-to-VPS traffic on SSH.

### Service Startup Errors

- Tailing logs on the VPS:
  ```bash
  sudo journalctl -u universal-agent-gateway -f
  ```
- Verify the `.env` file exists in the installation directory.

### Production `uv sync` Fails With Python Interpreter Permission Errors

If production deploy logs show either:

- `failed to canonicalize path /opt/universal_agent/.venv/bin/python3: Permission denied`
- `Failed to execute /opt/universal_agent/.venv/bin/python3: Permission denied`

then the existing `.venv` was created against a Python interpreter path that the `ua` service user cannot traverse.

Current deploy workflow behavior:
1. chowns the repo to `ua`
2. checks whether `ua` can resolve `.venv/bin/python3`
3. removes `.venv` only if that check fails
4. rebuilds dependencies as `ua` with `uv`

### NotebookLM Preflight Fails With `FileNotFoundError` Or `nlm` Missing

If NotebookLM auth preflight reports `auth_cli_missing:FileNotFoundError`, the runtime cannot find the external NotebookLM tool binaries.

If this fails on a node, verify running `sudo -u ua env PATH=/home/ua/.local/bin:/usr/local/bin:$PATH command -v nlm`.
