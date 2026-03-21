# CI/CD Pipeline & Troubleshooting

Our CI/CD pipeline is built on GitHub Actions and automates PR review, staging validation, and production deployment over Tailscale.

## Canonical Rule

This is the only supported app deployment path in this repository.

- Open a pull request to `develop` to run Codex review on the proposed change.
- Merge to `develop` to deploy to staging automatically.
- Promote the exact validated `develop` SHA to `main` via the promotion workflow to deploy to production automatically.
- Do not treat `scripts/deploy_vps.sh`, `scripts/vpsctl.sh`, or manual SSH deploy steps as the primary deployment path.

## Workflows

### Primary Deployment Workflows

| Name | Trigger | Target |
|------|---------|--------|
| `Codex Review Develop PR` | Pull request to `develop` | Automated PR review |
| `Deploy Staging` | Push to `develop` | Staging Service |
| `Promote Validated Develop To Main` | Manual workflow dispatch | Fast-forward `main` to validated `develop` SHA, then dispatch production deploy |
| `Deploy Production` | Push to `main` | Production Service |

### Utility and Debug Workflows

| Name | Trigger | Purpose |
|------|---------|---------|
| `Debug Production Services` | Manual workflow dispatch | Fetch logs and status from production services (gateway, API) for troubleshooting |
| `Fix Production Repo Directory` | Manual workflow dispatch | Reconstitute git repository in production checkout if `.git` directory is corrupted |
| `Run Clear Agent Queue` | Manual workflow dispatch | Clear all pending tasks in the agent task hub (staging + production) |
| `Nightly Doc Drift Audit` | Scheduled (daily) | Detect documentation drift. Commits report to `develop` via auto-merged PR, then dispatches VP fix missions to VPS. |

## Current Targets

| Area | Staging | Production |
|------|---------|------------|
| Git branch | `develop` | `main` |
| VPS checkout | `/opt/universal-agent-staging` | `/opt/universal_agent` |
| Gateway/API ports | `9002` / `9001` via `UA_GATEWAY_PORT`, `UA_API_PORT`, and `UA_GATEWAY_URL=http://127.0.0.1:9002` in staging `.env` | `8002` / `8001` |
| Web UI port | `3001` | `3000` |
| Web UI URL | `https://srv1360701.taildcc090.ts.net:9443` (Tailnet) | `https://app.clearspringcg.com` (Public)<br>`https://srv1360701.taildcc090.ts.net` (Tailnet) |
| API URL | Proxied via Web UI | `https://api.clearspringcg.com` (Public)<br>`https://srv1360701.taildcc090.ts.net:8443` (Tailnet) |
| Legacy/fallback checkout | n/a | `/opt/universal_agent_repo` if `/opt/universal_agent` is occupied by a non-git legacy directory |
| Runtime secrets | `staging` via explicit bootstrap `.env` plus stage secret validation | `production` via explicit bootstrap `.env` plus stage secret validation |

## Infisical Runtime Lanes

The runtime model is stage-based:

- `development`
- `staging`
- `production`

Machine identity is written locally during bootstrap and validated during deploy:

- `FACTORY_ROLE`
- `UA_DEPLOYMENT_PROFILE`
- `UA_RUNTIME_STAGE`
- `UA_MACHINE_SLUG`

Deploy workflows must not provision machine-shaped Infisical environments during normal deploys.
They also rewrite the checkout bootstrap `.env` from scratch on every deploy so
stale historical lines cannot survive a lane migration.

## Required GitHub Secrets

- `OPENAI_API_KEY` (Codex PR review workflow)
- `TAILSCALE_OAUTH_CLIENT_ID` (Tailscale OAuth API client ID, tag identity `tag:ci-gha`)
- `TAILSCALE_OAUTH_SECRET` (Tailscale OAuth API client secret)
- `VPS_SSH_HOST`
- `VPS_SSH_USER`
- `VPS_SSH_KEY`
- `INFISICAL_CLIENT_ID` (staging workflow)
- `INFISICAL_CLIENT_SECRET` (staging workflow)
- `INFISICAL_PROJECT_ID` (staging workflow)

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

## Pipeline Steps

1. **Open PR to `develop`** from a `feature/...` branch.
2. **Codex review** runs on that PR and comments directly on the diff.
3. **Merge to `develop`** only after review and normal checks are acceptable.
4. **Staging deploy** runs automatically on the merge result in `develop`.
5. **Validate staging** against the exact merged `develop` SHA.
6. **Promote validated SHA** using the `Promote Validated Develop To Main` workflow.
7. **Production deploy** is dispatched explicitly by the promotion workflow after `main` is advanced.

## Bootstrap Identity Written By Deploys

### Staging VPS

- `INFISICAL_ENVIRONMENT=staging`
- `UA_RUNTIME_STAGE=staging`
- `FACTORY_ROLE=HEADQUARTERS`
- `UA_DEPLOYMENT_PROFILE=vps`
- `UA_MACHINE_SLUG=vps-hq-staging`
- `UA_GATEWAY_PORT=9002`
- `UA_API_PORT=9001`
- `UA_GATEWAY_URL=http://127.0.0.1:9002`

### Production VPS

- `INFISICAL_ENVIRONMENT=production`
- `UA_RUNTIME_STAGE=production`
- `FACTORY_ROLE=HEADQUARTERS`
- `UA_DEPLOYMENT_PROFILE=vps`
- `UA_MACHINE_SLUG=vps-hq-production`

The bootstrap file written by deploys is intentionally minimal. Stage-shared
runtime config and secrets are loaded from Infisical after bootstrap validation.
Current deploy workflows serialize that bootstrap file deterministically during
deploy rather than editing keys in place.

## Deployed Runtime Tooling

- Staging and production deploys install project dependencies with `uv sync`.
- Staging and production deploys rebuild the Next.js `universal-agent-webui` application via `npm run build`. `npm install` is **conditional** — it only re-runs when `package.json` has changed since the last deploy (detected via a mtime sentinel file `node_modules/.package-json-mtime`). The `.next` build cache persists on the VPS between deploys, so incremental Next.js builds are fast.
- Staging and production deploys install the external NotebookLM tool package `notebooklm-mcp-cli` for the `ua` service user via `uv tool install --force notebooklm-mcp-cli`.
- This provides the `nlm` CLI and `notebooklm-mcp` server binaries expected by the NotebookLM runtime.
- The deployed runtime PATH must include `/home/ua/.local/bin` so those binaries are discoverable by gateway-executed Bash commands and MCP registration.
- Staging deploy must execute `uv` tool installation under the real `ua` home directory (`sudo -H -u ua` / `HOME=$ua_home`) so NotebookLM tools land in the service user's tool path.
- In the staging SSH deploy script, PATH must be quoted for remote expansion, not shipped as a literal `$PATH`, or basic commands like `getent`/`cut` can disappear from the remote shell.

## Expected Deploy Times

| Scenario | Staging | Production |
|----------|---------|------------|
| First deploy on a fresh VPS (cold npm build) | ~20–25 min | ~20–25 min |
| Normal deploy — no `package.json` change | ~8–12 min | ~8–12 min |
| Deploy after `package.json` change (fresh npm install) | ~15–20 min | ~15–20 min |

Both workflows have `timeout-minutes: 35` to accommodate the worst-case cold build. Normal deploys complete well within 15 minutes.

To force a full `npm install` on the next deploy (e.g. after a failed install left a corrupt `node_modules`), delete the sentinel on the VPS:

```bash
rm /opt/universal-agent-staging/web-ui/node_modules/.package-json-mtime  # staging
rm /opt/universal_agent/web-ui/node_modules/.package-json-mtime           # production
```

## Service Restart on Deploy

Every deploy pulls code, syncs dependencies, rebuilds the web UI, and then **restarts all managed systemd services**. This is the mechanism that ensures the running gateway, API, web UI, and other services pick up new code.

### Systemd Unit Names

| Service | Staging Unit | Production Unit |
|---------|-------------|------------------|
| Gateway | `universal-agent-staging-gateway` | `universal-agent-gateway` |
| API | `universal-agent-staging-api` | `universal-agent-api` |
| Web UI | `universal-agent-staging-webui` | `universal-agent-webui` |
| Telegram | — (not running in staging) | `universal-agent-telegram` |
| VP Worker (coder) | — | `universal-agent-vp-worker@vp.coder.primary` |
| VP Worker (general) | — | `universal-agent-vp-worker@vp.general.primary` |

### Restart Order

**Staging**: gateway + API restarted together, then webui.

**Production**: gateway + API + webui + telegram restarted together, then each enabled VP worker is restarted individually.

VP workers are only restarted if `systemctl is-enabled` reports them as active. This allows new VPS nodes to deploy without VP worker units installed.

### Deployment-Window Flag

Both workflows set `/tmp/ua-deployment-window` before restarting services and clear it after. This flag exists so the CSI canary can suppress SLO alerts during the brief service restart window. A background cleanup process removes the flag after 25 minutes as a safety net if the deploy exits abnormally.

> [!IMPORTANT]
> **Code changes only take effect after the service restarts.** If the deploy workflow completes but services are not restarted (e.g., `systemctl` is not available), the gateway will continue running the old code. The workflow logs a warning in this case.

## Post-Deploy Health Verification

After a deploy completes, verify the services are running correctly:

### Quick Check (from any Tailscale-connected machine)

```bash
# Production gateway health
curl -s http://100.106.113.93:8002/api/v1/health

# Staging gateway health
curl -s http://100.106.113.93:9002/api/v1/health
```

### On the VPS

```bash
# Check service status
sudo systemctl status universal-agent-gateway universal-agent-api universal-agent-webui

# Tail gateway logs for errors
sudo journalctl -u universal-agent-gateway -n 50 --no-pager

# For staging:
sudo systemctl status universal-agent-staging-gateway universal-agent-staging-api
sudo journalctl -u universal-agent-staging-gateway -n 50 --no-pager
```

### Verify Latest Code Is Running

The gateway lifespan initialization can take 2–3 minutes. If the health endpoint is unavailable immediately after deploy, wait and retry.

To confirm the gateway is running the expected code, check the process start time against the deploy timestamp:

```bash
ps -eo pid,lstart,cmd | grep gateway_server | grep -v grep
```

## Local Development Restart Caveat

> [!WARNING]
> The CI/CD pipeline only restarts **systemd-managed services on the VPS**. If you are running the gateway, API, or dev server **locally** as a direct Python process (e.g., `python -m universal_agent.gateway_server` or `npm run dev`), deploying to `develop` or `main` does **not** restart your local process. You must restart it manually to pick up new code.

This is the expected behavior — local development processes are owned by the developer, not by the CI/CD pipeline.

Common scenario where this matters:

1. You push a backend fix to `develop` and it deploys to the VPS staging.
2. Your local gateway (started manually) is still running the old code.
3. The local dashboard shows stale behavior because it's hitting the local gateway, not the VPS.

**Fix:** Kill and restart the local gateway process, or point your local web UI at the VPS gateway instead.

## Review and Promotion Rule

- There is exactly one Codex review gate: the PR into `develop`.
- There is no second Codex review on `main`.
- Production promotion should use the **full 40-character** validated `develop` SHA. The workflow will resolve short SHAs, but full SHAs are preferred to avoid ambiguity.
- The promotion workflow refuses to run if `develop` has moved since the validated SHA.
- The promotion workflow explicitly dispatches `Deploy Production`; it does not rely on workflow fan-out from the `main` fast-forward.
- To make the review gate enforceable, configure GitHub branch protection on `develop` to require the `Codex Review Develop PR` check before merge.

## Temporary Missing-Secret Behavior

If `OPENAI_API_KEY` is not configured yet:

- the `Codex Review Develop PR` workflow posts a warning comment and exits successfully
- the PR can still merge to `develop`
- staging and production promotion can still proceed

Once `OPENAI_API_KEY` is configured, the same workflow becomes the real blocking Codex review gate again.

## Recommended GitHub Branch Protection

Configure these settings in GitHub repository settings.

### `develop`

- Require a pull request before merging
- Require status checks to pass before merging
- Required status check: `Codex Review Develop PR / codex-review`
- Require branches to be up to date before merging
- Restrict direct pushes if you want review to be mandatory in practice
- If `OPENAI_API_KEY` is still missing, this required check will pass in "review skipped" mode rather than enforcing a real Codex review
- The `Nightly Doc Drift Audit` workflow creates auto-merged PRs (`chore/drift-report-<date>`) using `gh pr merge --squash --admin`. These are automated report commits, not feature changes.

### `main`

- Optional: require a pull request before merging
- Do not require the Codex review check on `main`
- Restrict direct pushes except for trusted release operators if you want production promotion to happen only via the promotion workflow or explicit release action

## Operational Meaning

- While you code, `develop` is the automated VPS-backed dev/staging lane.
- `main` is a separately deployable production lane and is currently deployable.
- Production and staging both have passing workflow runs as of March 14, 2026.

## Troubleshooting

### Deploy Job Times Out

Both `deploy-staging.yml` and `deploy-prod.yml` have `timeout-minutes: 35`.

If a deploy times out, the most common cause is a cold `npm run build` with no existing `.next` cache on the VPS. This happens on the very first deploy to a fresh server or after `node_modules` is wiped.

Actions:
1. Check whether `node_modules` and `.next` exist on the VPS under `web-ui/`.
2. If not, simply re-run the workflow — the cold build just needs more time and will succeed within the 35-minute window.
3. If the timeout keeps happening on subsequent runs, check if `package.json` changed (triggering a re-install) or if the VPS is under memory pressure during the build.

### Promotion Workflow Refuses To Run

If the promotion workflow fails before pushing `main`, inspect the validation step.

#### Signature: develop moved

If the workflow reports that `origin/develop` has moved, the staging-validated SHA is no longer the current `develop` head.

Required action:

1. decide whether the newer `develop` head should be staged and validated
2. if yes, revalidate the newer SHA in staging
3. run promotion again with the new validated SHA

#### Signature: main cannot fast-forward

If the workflow reports that `main` cannot fast-forward cleanly to the requested SHA, the branch history has diverged and requires manual investigation before release.

#### Signature: promotion succeeded but production deploy did not start

If `main` advances but no `Deploy Production` run appears, inspect the promotion workflow logs.

Required checks:

1. verify the promotion workflow has `actions: write` permission
2. verify the `Dispatch production deploy workflow` step completed successfully
3. verify `deploy-prod.yml` still supports `workflow_dispatch`

### SSH Preflight Fails Fast

If preflight exits before deploy, inspect error output in the workflow log.

#### Signature: interactive Tailscale check

If stderr includes either:

- `Tailscale SSH requires an additional check`
- `https://login.tailscale.com/...`

then CI identity is not matching the required non-interactive SSH policy. Verify:

- GitHub Action uses `oauth-client-id`/`oauth-secret` with `tags: tag:ci-gha`.
- Tailscale node(s) are tagged correctly (`tag:ci-gha` for runner identity, `tag:vps` on destination).
- SSH rule is `action: "accept"` from `tag:ci-gha` to `tag:vps` for `root`/`ua`.
- Network policy allows TCP/22 from `tag:ci-gha` to `tag:vps`.

### SSH Key or VPS Authentication Fails

- Verify the `VPS_SSH_KEY` secret in GitHub.
- Ensure the matching public key is present in target user `authorized_keys`.
- Check SSH auth logs on the VPS for rejected keys.

### Tailscale Connection Issues

- Ensure `TAILSCALE_OAUTH_CLIENT_ID` and `TAILSCALE_OAUTH_SECRET` are valid and not expired/revoked in GitHub Secrets.
- The OAuth client must have the writable `auth_keys` scope.
- Ensure the OAuth client was created with tag identity `tag:ci-gha`.
- Check the [Tailscale Admin Console](https://login.tailscale.com/admin/machines) to see if the GitHub Runner is joining properly.
- Verify ACL/grants permit runner-to-VPS traffic on SSH.
### Service Startup Errors

- Tailing logs on the VPS:
  ```bash
  sudo journalctl -u universal-agent-staging-gateway -f
  ```
- Verify the `.env` file exists in the installation directory.

### Staging Or Production `uv sync` Fails With Python Interpreter Permission Errors

If staging or production deploy logs show either:

- `failed to canonicalize path /opt/universal_agent/.venv/bin/python3: Permission denied`
- `Failed to execute /opt/universal_agent/.venv/bin/python3: Permission denied`

then the existing `.venv` was created against a Python interpreter path that the `ua` service user cannot traverse.

Current deploy workflow behavior:

1. chowns the repo to `ua`
2. checks whether `ua` can resolve `.venv/bin/python3`
3. removes `.venv` only if that check fails
4. rebuilds dependencies as `ua` with `uv`

This is intended to self-heal stale virtualenvs created against inaccessible Python cache paths.

### NotebookLM Preflight Fails With `FileNotFoundError` Or `nlm` Missing

If NotebookLM auth preflight reports `auth_cli_missing:FileNotFoundError`, the runtime cannot find the external NotebookLM tool binaries.

Current deploy workflow behavior:

1. installs `notebooklm-mcp-cli` for the `ua` service user with `uv tool install --force notebooklm-mcp-cli`
2. verifies both `nlm` and `notebooklm-mcp` resolve on PATH
3. relies on `/home/ua/.local/bin` being present in the runtime PATH

If this still fails on a node, verify:

- `sudo -u ua env PATH=/home/ua/.local/bin:/usr/local/bin:$PATH command -v nlm`
- `sudo -u ua env PATH=/home/ua/.local/bin:/usr/local/bin:$PATH command -v notebooklm-mcp`
- the service process is running with `/home/ua/.local/bin` on PATH
