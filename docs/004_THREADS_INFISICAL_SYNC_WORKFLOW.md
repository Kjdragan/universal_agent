# 004 Threads + Infisical Sync Workflow

## Purpose

This document explains the exact process used to onboard Threads API credentials into Infisical and avoid manual secret-by-secret entry.

It also explains the automation path for ongoing token refresh and resync.

## What We Set Up

We implemented a two-stage flow:

1. Bootstrap Threads auth and generate a machine-readable JSON payload containing:
   - `THREADS_APP_ID`
   - `THREADS_APP_SECRET`
   - `THREADS_USER_ID`
   - `THREADS_ACCESS_TOKEN`
   - `THREADS_TOKEN_EXPIRES_AT`
2. Bulk upsert that payload into Infisical in one command.

Then we added a scheduled systemd job that:

1. Reads current Threads secrets from Infisical
2. Refreshes the long-lived token
3. Writes updated token/expiry back to Infisical
4. Runs a probe to confirm read-path health

## Why This Matters

Without this flow, secret management is slow and error-prone:

1. Manual copy/paste of each key
2. Drift between env files and secret manager
3. Token expiry incidents

With this flow:

1. Initial setup is repeatable
2. Sync is atomic (single JSON payload)
3. Ongoing token maintenance is automated

## One-Time Onboarding Flow (What We Ran)

### Step 1: Generate OAuth URL

```bash
cd /home/kjdragan/lrepos/universal_agent
uv run python3 CSI_Ingester/development/scripts/csi_threads_auth_bootstrap.py \
  --print-auth-url \
  --app-id "3656560324597958" \
  --redirect-uri "https://app.clearspringcg.com/threads-callback" \
  --scopes "threads_basic,threads_read_replies,threads_manage_mentions,threads_manage_insights,threads_keyword_search,threads_profile_discovery"
```

### Step 2: Complete consent and capture `code`

After approval, the redirect landed on a `404` page. That is acceptable.  
The required OAuth code is in the browser URL query string:

`.../threads-callback?code=<VALUE>&state=<VALUE>`

### Step 3: Exchange code and generate secrets JSON

```bash
uv run python3 CSI_Ingester/development/scripts/csi_threads_auth_bootstrap.py \
  --mode exchange \
  --app-id "3656560324597958" \
  --app-secret "<THREADS_APP_SECRET>" \
  --auth-code "<OAUTH_CODE>" \
  --redirect-uri "https://app.clearspringcg.com/threads-callback" \
  --skip-env-write \
  --infisical-json-file /tmp/threads-secrets.json
```

### Step 4: Bulk sync secrets into Infisical

```bash
uv run python3 CSI_Ingester/development/scripts/csi_threads_infisical_sync.py \
  --updates-file /tmp/threads-secrets.json
```

### Step 5: Probe owned read path

```bash
eval "$(uv run python3 - <<'PY'
import json, shlex
d = json.load(open('/tmp/threads-secrets.json'))
for k in ("THREADS_APP_ID","THREADS_APP_SECRET","THREADS_USER_ID","THREADS_ACCESS_TOKEN","THREADS_TOKEN_EXPIRES_AT"):
    print(f"export {k}={shlex.quote(str(d.get(k,'')))}")
PY
)"

uv run python3 CSI_Ingester/development/scripts/csi_threads_probe.py \
  --config-path CSI_Ingester/development/config/config.yaml \
  --source owned \
  --limit 3
```

## Scheduled Automation (Implemented)

### Runner

- `CSI_Ingester/development/scripts/csi_threads_token_refresh_sync.sh`

Behavior:

1. Loads systemd env file
2. Pulls current Threads keys from Infisical
3. Runs refresh mode in `csi_threads_auth_bootstrap.py`
4. Upserts refreshed payload with `csi_threads_infisical_sync.py`
5. Runs `csi_threads_probe.py --source owned`

### Systemd Units

- `CSI_Ingester/development/deployment/systemd/csi-threads-token-refresh-sync.service`
- `CSI_Ingester/development/deployment/systemd/csi-threads-token-refresh-sync.timer`

Timer schedule:

- Daily at `03:15 UTC` with `RandomizedDelaySec=15m`

### Installer Wiring

The standard installer now includes the new timer:

- `CSI_Ingester/development/scripts/csi_install_systemd_extras.sh`

## Required Env Keys for Automation

In `csi-ingester.env`:

1. `INFISICAL_CLIENT_ID`
2. `INFISICAL_CLIENT_SECRET`
3. `INFISICAL_PROJECT_ID`
4. `INFISICAL_ENVIRONMENT` (default `dev`)
5. `INFISICAL_SECRET_PATH` (default `/`)

Optional tuning:

1. `CSI_THREADS_REFRESH_TIMEOUT_SECONDS` (default `20`)
2. `CSI_THREADS_REFRESH_BUFFER_SECONDS` (default `21600`)
3. `CSI_THREADS_REFRESH_RUN_PROBE` (default `1`)
4. `CSI_THREADS_REFRESH_REQUIRE_PROBE_OK` (default `1`)
5. `CSI_THREADS_PROBE_SOURCE` (default `owned`)
6. `CSI_THREADS_PROBE_LIMIT` (default `3`)

## Known UX Gotchas We Resolved

1. Meta callback form must be truly saved (field list item committed), not just typed.
2. `404` on callback route is acceptable if URL still contains `code=`.
3. Placeholders like `YOUR_THREADS_APP_ID` must be replaced with real values.
4. Threads requires **Threads app ID/secret**, not regular app ID/secret.
5. In development mode, tester invite must be accepted by the Threads account.

## Operational Verification Signals

Healthy run indicators:

1. `THREADS_TOKEN_ACTION=exchange` or `THREADS_TOKEN_ACTION=refresh`
2. `SYNC_TOTAL=5` (five Threads keys synchronized)
3. `THREADS_PROBE_OWNED_OK posts=<n>`
4. `THREADS_PROBE_FAIL_COUNT=0`

## Notes on Running VPS Commands

The following commands require root and a deployed `/opt/universal_agent` path:

```bash
sudo /opt/universal_agent/CSI_Ingester/development/scripts/csi_install_systemd_extras.sh
sudo systemctl start csi-threads-token-refresh-sync.service
systemctl status csi-threads-token-refresh-sync.timer
journalctl -u csi-threads-token-refresh-sync.service -n 100 --no-pager
```

If `sudo` requires an interactive password in your shell, run them directly in your VPS terminal session.
