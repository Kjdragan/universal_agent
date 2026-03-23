# VPS Deployment Profile Stuck at `local_workstation` (2026-03-23)

## Purpose

This note explains a production failure in the YouTube tutorial ingest path that initially appeared to be a VPS deployment-profile problem and later proved to be a proxy-credential problem on the real VPS host.

This document is intended as a handoff summary for another AI coder or operator so they can understand:

1. what was broken
2. what part of the earlier diagnosis was wrong
3. what code and deployment changes were still worth keeping
4. what the actual remaining production issue is
5. how to verify the real fix

## Update (2026-03-23 14:09 CDT)

The original version of this document diagnosed the wrong machine.

Earlier curls were sent to `100.95.187.38:8002`, which is Kevin's Tailscale desktop host (`mint-desktop`), not the production VPS. The real production VPS is `100.106.113.93`.

That means:

1. `deployment_profile=local_workstation` on `100.95.187.38` was expected and correct
2. the production VPS was already reporting `deployment_profile=vps`
3. the earlier "VPS profile stuck at local_workstation" conclusion was based on the wrong host

The systemd-template hardening work described below is still good and worth keeping, but it is **not** the primary fix for the current `proxy_not_configured` symptom on production.

## Correct Host Mapping

| IP | Identity | Machine | Expected profile |
|---|---|---|---|
| `100.95.187.38` | `mint-desktop` | Kevin's local desktop | `local_workstation` |
| `100.106.113.93` | `srv1360701` | Actual production VPS | `vps` |

## Current Production Symptom

On the real VPS (`100.106.113.93`), the production gateway health endpoint returns:

- `deployment_profile.profile = "vps"`

The YouTube ingest endpoint returned:

- `error = "proxy_not_configured"`
- `proxy_mode = "disabled"`
- `worker_profile = "vps"`

That means the deployment profile is now correct, but the proxy path is still failing.

## What Was Verified on the Real VPS

These checks were run against `100.106.113.93`:

1. `systemctl cat universal-agent-gateway.service` showed:
   - `WorkingDirectory=/opt/universal_agent`
   - `EnvironmentFile=/opt/universal_agent/.env`
   - `ExecStart=/opt/universal_agent/.venv/bin/python3 -m universal_agent.gateway_server`
2. gateway startup logs showed:
   - `Infisical runtime secret bootstrap succeeded: profile=vps env=production ... loaded=192`
   - `Lifespan: deployment profile resolved to vps`
3. a fresh standalone bootstrap process on the VPS successfully fetched proxy secrets from Infisical
4. `_build_webshare_proxy_config()` succeeded in a fresh process after bootstrap
5. `scripts/check_webshare_proxy.py --json` failed with `407 Proxy Authentication Required`

One important debugging note:

- `/proc/<pid>/environ` is **not** a reliable indicator of Python `os.environ` mutations performed after process start in this environment, so the earlier check for missing `PROXY_*` inside `/proc/.../environ` should not be treated as proof that the running process lacked them at runtime.

## Why the Proxy Path Is Still Broken

`src/universal_agent/youtube_ingest.py` builds the proxy config from runtime environment variables:

- `PROXY_USERNAME`
- `PROXY_PASSWORD`

Those values are expected to arrive through Infisical bootstrap. The runtime bootstrap behavior depends on `UA_DEPLOYMENT_PROFILE`:

- `vps` -> strict mode, fail closed if Infisical cannot be loaded
- `local_workstation` -> non-strict mode, allow degraded fallback

On the real VPS, the current evidence shows that Infisical bootstrap is working in a fresh process and that the remaining failure is downstream of secret retrieval.

## Actual Root Cause on Production

The current production blocker is an invalid Webshare rotating username, not a broken deployment profile.

Verified facts from a fresh VPS bootstrap process:

- `PROXY_USERNAME` is present
- `PROXY_PASSWORD` is present
- `WEBSHARE_PROXY_HOST` is `p.webshare.io`
- `WEBSHARE_PROXY_PORT` is `80`
- Webshare probe fails with `407 Proxy Authentication Required`
- `PROXY_USERNAME` does **not** end with `-rotate`
- `WEBSHARE_PROXY_USER` does not match `PROXY_USERNAME`

That is consistent with the repository's Webshare documentation, which explicitly says the rotating residential username must include the `-rotate` suffix.

In other words:

1. the real VPS is in `vps` mode
2. a fresh VPS bootstrap process receives proxy secrets
3. the secret values currently fetched from Infisical are not valid for Webshare rotating residential auth

## Why `proxy_not_configured` Still Appeared

The remaining oddity is that the live HTTP endpoint still returned `proxy_mode="disabled"` while a fresh VPS bootstrap process could build a Webshare proxy config successfully.

The most likely explanation is:

1. the long-running gateway process is stale relative to the latest proxy-secret state
2. a service restart is needed after correcting the proxy secrets

This is an inference from the evidence, not a directly proven code path.

## Why the Earlier Fixes Did Not Solve It

Three earlier fixes targeted the Python process:

1. refreshing `_DEPLOYMENT_PROFILE` during FastAPI lifespan
2. adding a production `Environment=` drop-in
3. loading `.env` early at import time

Those changes targeted deployment identity and service ownership. They did not and could not repair an invalid Webshare credential value.

They also appeared more important than they really were because the initial health checks were sent to Kevin's desktop instead of the VPS.

## What Part of the Earlier Diagnosis Still Matters

The earlier systemd ownership diagnosis was not the root cause of the current production proxy failure, but the code changes are still worth keeping.

Why it still matters:

1. the repo previously did not own the base application service units
2. repo-managed units are still the right deploy contract
3. production can deploy to `/opt/universal_agent` or fallback `/opt/universal_agent_repo`
4. rendering unit files from templates is still better than relying on undocumented host-local service definitions

## What Was Changed

The code changes made in response to the earlier diagnosis are still sound deployment hardening.

### 1. Added canonical systemd templates

New templates were added under:

- `deployment/systemd/templates/universal-agent-gateway.service.template`
- `deployment/systemd/templates/universal-agent-api.service.template`
- `deployment/systemd/templates/universal-agent-webui.service.template`
- `deployment/systemd/templates/universal-agent-telegram.service.template`

These templates make the service definition part of versioned source control.

### 2. Added a renderer/installer script

New script:

- `scripts/install_vps_systemd_units.sh`

This script:

1. accepts `--lane production|staging`
2. accepts `--app-root <checkout>`
3. renders the templates against the actual active checkout path
4. installs the units into `/etc/systemd/system`
5. installs the gateway/API stack-limit drop-ins
6. runs `systemctl daemon-reload`
7. enables the rendered units

This matters because production can deploy to either:

- `/opt/universal_agent`
- `/opt/universal_agent_repo`

and the unit definitions now follow the real checkout path instead of assuming one hardcoded host state.

### 3. Updated production deploy

`.github/workflows/deploy-prod.yml` now installs the canonical production units from repo templates before restarting services.

### 4. Updated staging deploy

`.github/workflows/deploy-staging.yml` now installs the canonical staging units from repo templates before restarting services.

This also removes the prior staging behavior where `universal-agent-staging-webui` might not exist and restart was best-effort only.

### 5. Removed the obsolete production-only drop-in

Deleted:

- `deployment/systemd/universal-agent-deployment-profile.conf`

That drop-in was an attempted workaround, not the root fix. Once the base unit is repo-managed and loads the checkout `.env` directly, this extra override is unnecessary and confusing.

### 6. Updated canonical deployment docs

The deployment docs now explicitly state that deploy owns the base systemd units:

- `docs/deployment/architecture_overview.md`
- `docs/deployment/ci_cd_pipeline.md`

## Verification Performed on the Code Changes

The new installer script was verified locally with targeted checks:

1. `bash -n scripts/install_vps_systemd_units.sh`
2. dry-render production units into a temp directory
3. dry-render staging units into a temp directory

The rendered units correctly pointed at the supplied checkout root and used:

- checkout `.env` for gateway/API/telegram
- `web-ui/.env.local` for webui

## Important Note About the Webshare Activity Screen

Seeing successful Webshare traffic in the Webshare dashboard does **not** prove that the broken VPS gateway process had a valid proxy configuration.

It only proves that **some client using the same Webshare account** generated traffic.

That traffic could have come from:

1. a different machine
2. a local dev process
3. another service or script
4. another test path that used Webshare credentials correctly

The failing production symptom was process-specific:

- the gateway process on port `8002`
- under its own systemd-managed environment
- during the YouTube ingest request path

So the correct question is not "did Webshare show any traffic?" but "did the specific production gateway process start with the right env and service definition?"

## Recommended Production Fix

The next operator action should be to fix the Webshare credentials stored in Infisical production:

1. set `PROXY_USERNAME` to the rotating username with the `-rotate` suffix
2. set `WEBSHARE_PROXY_USER` to the same value for alias consistency
3. confirm `PROXY_PASSWORD` and `WEBSHARE_PROXY_PASS` match the current Webshare dashboard password
4. keep `WEBSHARE_PROXY_HOST=p.webshare.io`
5. keep `WEBSHARE_PROXY_PORT=80`
6. restart `universal-agent-gateway.service`

Based on the repository documentation, the expected username shape is:

- `rotatingproxyua-rotate`

## Post-Fix Verification Checklist

After fixing the secret values and restarting the gateway:

1. query the production health endpoint and confirm `profile = "vps"`
2. run `scripts/check_webshare_proxy.py --json` on the VPS
3. confirm the Webshare probe no longer returns `407`
4. re-run the YouTube ingest request
5. if the proxy still fails, classify the new error:
   - `proxy_auth_failed`
   - `proxy_connect_failed`
   - `request_blocked`
   - `proxy_quota_or_billing`

If `proxy_auth_failed` remains after fixing the username suffix and restarting, the remaining likely issue is the password value.

## Files Changed For This Fix

- `.github/workflows/deploy-prod.yml`
- `.github/workflows/deploy-staging.yml`
- `deployment/systemd/templates/universal-agent-gateway.service.template`
- `deployment/systemd/templates/universal-agent-api.service.template`
- `deployment/systemd/templates/universal-agent-webui.service.template`
- `deployment/systemd/templates/universal-agent-telegram.service.template`
- `scripts/install_vps_systemd_units.sh`
- `docs/deployment/architecture_overview.md`
- `docs/deployment/ci_cd_pipeline.md`

## Bottom Line

The investigation had two phases:

1. an initial wrong-host diagnosis that led to useful deployment hardening
2. the corrected production diagnosis that identified invalid Webshare credentials as the real blocker

The current production issue is:

- the real VPS is in `vps` mode
- Infisical bootstrap works in a fresh process
- the current rotating proxy username is wrong for Webshare auth

The deployment/systemd template changes remain worth keeping, but the operator fix needed now is to correct the Webshare secrets and restart the gateway.
