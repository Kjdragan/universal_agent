# Threads + Infisical Setup (Step-by-Step, No Secret-by-Secret Entry)

This guide is for Phase 1 (analytics/read-only) Threads setup in CSI.

## Short answer to your question

You do **not** need to manually type all five runtime secrets one-by-one into Infisical.

You only need to do a few manual Meta steps (app setup + OAuth consent).  
Then scripts will generate and bulk-sync the Threads secrets.

## What is manual vs automated

### Manual (you)

1. Create/configure a Meta app with Threads API product.
2. Set OAuth redirect URI in the app.
3. Copy `THREADS_APP_ID` and `THREADS_APP_SECRET` from Meta once.
4. Open consent URL and approve access.
5. Copy the OAuth `code` from the redirect URL once.

### Automated (scripts)

1. Exchange OAuth code -> short-lived token -> long-lived token.
2. Resolve `THREADS_USER_ID` from `/v1.0/me` automatically.
3. Build full secret payload JSON.
4. Bulk upsert all Threads secrets to Infisical.
5. Refresh token later and re-sync.

## The 5 Threads runtime secrets and how they are obtained

1. `THREADS_APP_ID`
Source: Meta app dashboard (copy once).

2. `THREADS_APP_SECRET`
Source: Meta app dashboard (copy once).

3. `THREADS_USER_ID`
Source: script resolves it automatically from Threads `/v1.0/me`.

4. `THREADS_ACCESS_TOKEN`
Source: script gets it from token exchange.

5. `THREADS_TOKEN_EXPIRES_AT`
Source: script computes it from token exchange/refresh response.

## Prerequisites

1. You have access to the repo and can run CSI scripts.
2. You have an Infisical project/environment/path for CSI secrets.
3. You have (or will create) an Infisical Machine Identity that can write secrets.

## Step 1: Create Meta app and Threads product

1. Go to Meta for Developers and create/select your app.
2. Add the **Threads API** product to the app.
3. Configure OAuth redirect URI to your callback URL (example: `https://your-redirect.example/callback`).
4. Confirm app is in a usable mode for your account (dev mode is fine for initial setup).
5. Copy and save:
   - App ID -> `THREADS_APP_ID`
   - App Secret -> `THREADS_APP_SECRET`

Official docs:
- https://developers.facebook.com/docs/threads/get-started
- https://developers.facebook.com/docs/threads/reference

## Step 2: Prepare Infisical Machine Identity creds (one-time)

These are used by the sync script to write secrets. Export them in your shell/session:

```bash
export INFISICAL_CLIENT_ID="..."
export INFISICAL_CLIENT_SECRET="..."
export INFISICAL_PROJECT_ID="..."
export INFISICAL_ENVIRONMENT="dev"
export INFISICAL_SECRET_PATH="/"
```

Notes:
- These are **not** the Threads secrets.
- They are credentials for the script to write into Infisical.

## Step 3: Generate Threads consent URL

Run:

```bash
cd /home/kjdragan/lrepos/universal_agent/CSI_Ingester/development
scripts/csi_run.sh uv run python3 scripts/csi_threads_auth_bootstrap.py \
  --print-auth-url \
  --app-id "<THREADS_APP_ID>" \
  --redirect-uri "https://your-redirect.example/callback"
```

You will get `THREADS_AUTH_URL=...`.

## Step 4: Approve consent and copy OAuth code

1. Open `THREADS_AUTH_URL` in browser.
2. Approve access.
3. After redirect, copy the `code` query parameter from the callback URL.
   - Example redirect:
   - `https://your-redirect.example/callback?code=AAA...&state=BBB...`
   - Use `AAA...` as `THREADS_OAUTH_CODE`.

## Step 5: Exchange token and produce JSON payload

Run:

```bash
cd /home/kjdragan/lrepos/universal_agent/CSI_Ingester/development
scripts/csi_run.sh uv run python3 scripts/csi_threads_auth_bootstrap.py \
  --mode exchange \
  --app-id "<THREADS_APP_ID>" \
  --app-secret "<THREADS_APP_SECRET>" \
  --auth-code "<THREADS_OAUTH_CODE>" \
  --redirect-uri "https://your-redirect.example/callback" \
  --skip-env-write \
  --infisical-json-file /tmp/threads-secrets.json
```

This produces `/tmp/threads-secrets.json` with all five Threads runtime secrets.

## Step 6: Bulk sync all Threads secrets to Infisical

Run:

```bash
cd /home/kjdragan/lrepos/universal_agent/CSI_Ingester/development
scripts/csi_run.sh uv run python3 scripts/csi_threads_infisical_sync.py \
  --updates-file /tmp/threads-secrets.json
```

Expected output includes:
- `SYNC_CREATED=...`
- `SYNC_UPDATED=...`
- `SYNC_TOTAL=...`

## Step 7: Verify from CSI side

Run probe:

```bash
cd /home/kjdragan/lrepos/universal_agent/CSI_Ingester/development
scripts/csi_run.sh uv run python3 scripts/csi_threads_probe.py \
  --config-path config/config.yaml \
  --source all \
  --limit 5 \
  --max-terms 3
```

## Ongoing token refresh (same flow, no manual secret editing)

When rotating token:

```bash
cd /home/kjdragan/lrepos/universal_agent/CSI_Ingester/development
scripts/csi_run.sh uv run python3 scripts/csi_threads_auth_bootstrap.py \
  --mode refresh \
  --app-id "<THREADS_APP_ID>" \
  --app-secret "<THREADS_APP_SECRET>" \
  --skip-env-write \
  --infisical-json-file /tmp/threads-secrets.json

scripts/csi_run.sh uv run python3 scripts/csi_threads_infisical_sync.py \
  --updates-file /tmp/threads-secrets.json
```

## Optional: automate refresh + sync daily (systemd timer)

If you run CSI on VPS with systemd, use the built-in automation job:

1. Ensure these keys exist in `/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env`:
   - `INFISICAL_CLIENT_ID`
   - `INFISICAL_CLIENT_SECRET`
   - `INFISICAL_PROJECT_ID`
   - `INFISICAL_ENVIRONMENT=dev`
   - `INFISICAL_SECRET_PATH=/`
2. Install/enable timers:

```bash
sudo /opt/universal_agent/CSI_Ingester/development/scripts/csi_install_systemd_extras.sh
```

3. Verify:

```bash
systemctl status csi-threads-token-refresh-sync.timer
journalctl -u csi-threads-token-refresh-sync.service -n 100 --no-pager
```

## Troubleshooting quick checks

1. `ERROR=THREADS_APP_SECRET is required`
- Pass `--app-secret` or ensure it is available in environment.

2. `threads_auth_code_exchange_failed`
- Verify redirect URI exactly matches app config.
- Ensure the `code` is fresh and copied fully.

3. `ERROR=Missing required Infisical settings`
- Export `INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`, `INFISICAL_PROJECT_ID`.

4. Probe returns no events
- Confirm app/account permissions and seed terms.
- Test `--source owned` first, then seeded/broad.

## Relevant scripts

- Auth bootstrap: `scripts/csi_threads_auth_bootstrap.py`
- Infisical bulk sync: `scripts/csi_threads_infisical_sync.py`
- Live probe: `scripts/csi_threads_probe.py`
