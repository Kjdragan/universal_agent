# Webshare Rotating Residential Proxy — Setup & Operations Runbook

**Last updated:** 2026-03-13  
**Maintainer:** Kevin / UA team  
**Relevant pipeline:** YouTube transcript ingestion (`youtube-expert` pipeline, `youtube_playlist_watcher`)

---

## 1. What this proxy does and why we need it

The Universal Agent YouTube tutorial pipeline fetches transcripts from YouTube using
`youtube-transcript-api` and metadata via `yt-dlp`. YouTube aggressively rate-limits
and blocks requests from **datacenter IP addresses** (including the VPS). A residential
rotating proxy is required to appear as a normal residential internet user.

**Without a working proxy, every YouTube ingest attempt will fail with `proxy_connect_failed`.**

---

## 2. Account details

| Field | Value |
|---|---|
| Provider | [Webshare.io](https://proxy.webshare.io) |
| Product | **Rotating Residential** (not "Proxy Server" / not "Static Residential") |
| Dashboard login | Google SSO — Kevin's Google account |
| Dashboard URL | `https://dashboard.webshare.io` |
| Plan | 3 GB / month bandwidth |

> [!IMPORTANT]
> Webshare has **three separate products**: "Proxy Server" (static datacenter, CANCELLED),
> "Static Residential", and "Rotating Residential". We use **Rotating Residential only**.
> Each product has its own credentials and endpoint hostname. Do not mix them up.

---

## 3. Correct proxy configuration

These are the **exact values** that must be in Infisical. All four must be correct simultaneously.

| Infisical secret | Correct value | Notes |
|---|---|---|
| `PROXY_USERNAME` | `rotatingproxyua-rotate` | Note the **`-rotate` suffix** — without it the proxy returns 404 |
| `PROXY_PASSWORD` | *(from Webshare dashboard)* | 15-char alphanumeric string |
| `WEBSHARE_PROXY_HOST` | `p.webshare.io` | The **rotating residential** endpoint — not `proxy.webshare.io` |
| `WEBSHARE_PROXY_PORT` | `80` | Standard port for both HTTP and HTTPS CONNECT |

Additionally, these aliases are set (kept in sync):

| Infisical secret | Correct value |
|---|---|
| `WEBSHARE_PROXY_USER` | `rotatingproxyua-rotate` |
| `WEBSHARE_PROXY_PASS` | *(same as PROXY_PASSWORD)* |
| `PROXY_HOST` | `p.webshare.io` |
| `PROXY_PORT` | `80` |
| `WEBSHARE_API_KEY` | *(from Webshare dashboard → API → Keys)* |

> [!CAUTION]
> **`proxy.webshare.io` is the WRONG host** — it belongs to the old static "Proxy Server" plan
> (now cancelled). Using it causes `Tunnel connection failed: 404 Not Found` on HTTPS CONNECT,
> which is Webshare's way of saying "this credential is not valid for this endpoint."
> The correct rotating residential endpoint is **`p.webshare.io`**.

---

## 4. How the code resolves these secrets

`src/universal_agent/youtube_ingest.py` — `_build_webshare_proxy_config()`:

```python
username = (
    os.getenv("PROXY_USERNAME")          # checked first
    or os.getenv("WEBSHARE_PROXY_USER")  # fallback
    or ""
).strip()

domain_name = (
    os.getenv("WEBSHARE_PROXY_HOST")     # checked first
    or os.getenv("PROXY_HOST")           # fallback
    or "p.webshare.io"                   # default
).strip()
```

**Implication:** `PROXY_USERNAME` and `WEBSHARE_PROXY_HOST` must always be correct because
they take priority over the `WEBSHARE_PROXY_*` aliases.

The environment is loaded at startup by `infisical_loader.initialize_runtime_secrets()`,
which loads from the Infisical **`development`** environment on local_workstation and the
**`production`** environment on VPS.

---

## 5. Verify proxy is working

### Quick check (skip slow HTTP probe):

```bash
uv run python scripts/check_webshare_proxy.py --skip-http
```

**Expected healthy output:**
```json
{
  "proxy": { "host": "p.webshare.io", "port": 80, "warnings": [] },
  "probes": {
    "tcp":           { "ok": true, "latency_ms": 80 },
    "https_connect": { "ok": true, "http_status": 200, "response_snippet": "{\"ip\":\"<residential_ip>\"}" },
    "youtube_https": { "ok": true, "http_status": 204 }
  },
  "ok": true
}
```

**Key signals:**

| Signal | Meaning |
|---|---|
| `tcp.ok = true` | Proxy server reachable on the network |
| `https_connect.ok = true` | Credentials accepted; CONNECT tunnel works |
| `youtube_https.ok = true` | YouTube reachable through the proxy |
| `https_connect` response IP is residential | Proxy routing correctly through residential IP |
| `http.ok = false` (HTTP non-tunnel) | **Normal** — rotating residential only supports HTTPS CONNECT, not plain HTTP forwarding |

### Full check including credentials offline verification:

```bash
uv run python scripts/check_webshare_proxy_credentials.py
```

---

## 6. Failure modes and what they mean

### `proxy_connect_failed` — `Tunnel connection failed: 404 Not Found`

**Most common production failure.** TCP connects fine but HTTPS CONNECT gets 404.

**Causes (in order of likelihood):**
1. **Wrong username** — `rotatingproxyua` instead of `rotatingproxyua-rotate`
2. **Wrong host** — `proxy.webshare.io` instead of `p.webshare.io`
3. **Stale/rotated password** — Webshare credentials regenerated on the dashboard

**Fix:** See §7 below.

---

### `proxy_connect_failed` — `getaddrinfo failed` / `Name or service not known`

DNS resolution failure for the proxy host. Can happen if:
- `WEBSHARE_PROXY_HOST` is misspelled
- VPS network issue

**Fix:** Check `WEBSHARE_PROXY_HOST` value. Should be `p.webshare.io`.

---

### `proxy_not_configured`

`PROXY_USERNAME` or `PROXY_PASSWORD` are empty/missing.

**Fix:** Run `uv run python scripts/check_webshare_proxy_credentials.py` to confirm what's loaded,
then run the update script (§7).

---

### `proxy_auth_failed` — `407 Proxy Authentication Required`

Credentials set and host correct, but password is wrong.

**Fix:** Regenerate password in Webshare dashboard and update Infisical (§7).

---

### `proxy_quota_or_billing`

Monthly bandwidth exhausted or plan suspended.

**Fix:** Check [Webshare dashboard → Rotating Residential → Subscription](https://dashboard.webshare.io)
for remaining bandwidth. Upgrade plan or wait until next billing cycle.

---

## 7. Updating proxy credentials in Infisical

### Step 1 — Get current credentials from Webshare dashboard

1. Go to [https://dashboard.webshare.io](https://dashboard.webshare.io), sign in with Google
2. Navigate to **Rotating Residential → Proxy List**
3. Set **Connection Method = Rotating Proxy Endpoint**, **Authentication Method = Username/Password**
4. Copy the values shown:
   - Domain Name: `p.webshare.io`
   - Proxy Port: `80`
   - Proxy Username: `rotatingproxyua-rotate` *(check this matches)*
   - Proxy Password: *(copy the current value)*

### Step 2 — Update Infisical via the helper script

```bash
uv run python scripts/update_webshare_proxy_credentials.py
# Enter username and password when prompted
```

Or non-interactively:

```bash
uv run python scripts/update_webshare_proxy_credentials.py \
  --username "rotatingproxyua-rotate" \
  --password "<new_password>" \
  --environments development production
```

The script updates both `PROXY_USERNAME` + `PROXY_PASSWORD` in the specified Infisical environments.

> [!NOTE]
> If you also need to update the host (e.g., Webshare changes their endpoint), run this
> additional one-liner to fix `WEBSHARE_PROXY_HOST`:
> ```bash
> uv run python scripts/infisical_upsert_secret.py \
>   --environment development --environment production \
>   --secret "WEBSHARE_PROXY_HOST=p.webshare.io"
> ```

### Step 3 — Verify connectivity

```bash
uv run python scripts/check_webshare_proxy.py --skip-http
# Expect: ok: true, https_connect.ok: true, youtube_https.ok: true
```

### Step 4 — Deploy to production

```bash
git add -A && git commit -m "fix(proxy): update Webshare credentials in Infisical"
git push origin develop
# CI auto-deploys to staging; then promote to production via GitHub Actions
```

### Step 5 — Clear ingest cooldowns (optional, for immediate retry)

Failed videos are placed in a `proxy_connect_failed` cooldown. After redeploying, they
retry automatically on the next playlist poll cycle. To force immediate retry on the VPS:

```bash
# Trigger a manual playlist poll via the ops API
curl -s -X POST http://localhost:8001/api/v1/ops/youtube-playlist-watcher/poll \
  -H "Authorization: Bearer $UA_HOOKS_TOKEN" | jq .
```

Or restart the gateway service:
```bash
sudo systemctl restart universal-agent-gateway
```

---

## 8. Where proxy configuration lives in the codebase

| File | Purpose |
|---|---|
| `src/universal_agent/youtube_ingest.py` | `_build_webshare_proxy_config()` — builds the `WebshareProxyConfig` object; defines env var priority |
| `src/universal_agent/hooks_service.py` | `_call_local_youtube_ingest_worker()` — calls the ingest endpoint; handles `proxy_connect_failed` cooldowns |
| `src/universal_agent/infisical_loader.py` | Loads all secrets from Infisical into env at startup |
| `scripts/check_webshare_proxy.py` | Live proxy transport probe (TCP + HTTPS CONNECT + YouTube) |
| `scripts/check_webshare_proxy_credentials.py` | Offline config validator (checks env var shape, no live probes) |
| `scripts/update_webshare_proxy_credentials.py` | Updates `PROXY_USERNAME` + `PROXY_PASSWORD` in Infisical |

---

## 9. Infisical environments mapping

| Infisical environment | Used by | VPS service |
|---|---|---|
| `development` | Local workstation runtime | N/A |
| `production` | VPS runtime | `universal-agent-gateway.service` |

The VPS loads secrets from the `production` Infisical environment at startup via
`INFISICAL_ENVIRONMENT=production` in the systemd service env file.

> [!IMPORTANT]
> When you update proxy credentials, always update **both** `development` and `production`
> environments. The `update_webshare_proxy_credentials.py` script defaults to both.

---

## 10. Common mistakes checklist

- [ ] **Username missing `-rotate` suffix** — `rotatingproxyua` ≠ `rotatingproxyua-rotate`
- [ ] **Using `proxy.webshare.io`** — the static/legacy host; rotating residential uses `p.webshare.io`
- [ ] **Updated dashboard password but forgot to update Infisical** — the two are not linked
- [ ] **Updated `WEBSHARE_PROXY_PASS` but not `PROXY_PASSWORD`** — code prefers `PROXY_PASSWORD`
- [ ] **Updated Infisical but forgot to redeploy** — running service holds old env until restart
- [ ] **Only updated `development` Infisical but not `production`** — VPS still has old creds
- [ ] **Checking `proxy.webshare.io/dashboard` TCP reachability** — that will always succeed (`ok: true`), but HTTPS CONNECT to the wrong host returns 404

---

## 11. Webshare API (for programmatic credential operations)

The Webshare REST API can be used to inspect or rotate credentials without visiting the dashboard.

**API key location:** Webshare dashboard → API → Keys → `WEBSHARE_API_KEY` in Infisical

```python
import httpx

api_key = os.environ["WEBSHARE_API_KEY"]

# Get current proxy config
resp = httpx.get(
    "https://proxy.webshare.io/api/v2/proxy/config/",
    headers={"Authorization": f"Token {api_key}"}
)
config = resp.json()
print(config["username"], config["password"])

# Regenerate password
resp = httpx.post(
    "https://proxy.webshare.io/api/v2/proxy/config/",
    headers={"Authorization": f"Token {api_key}"},
    json={}  # POST to /config/ triggers password reset on some plans
)
```

> [!NOTE]
> The Webshare REST API `/api/v2/proxy/config/` endpoint primarily targets the **static Proxy Server**
> product. For Rotating Residential, credential management is mainly via the dashboard.
> The API key is still useful for bandwidth usage queries and IP authorization management.

---

## 12. Incident log

| Date | Failure | Root cause | Fix |
|---|---|---|---|
| 2026-03-13 | `proxy_connect_failed: 404` on all videos | `PROXY_USERNAME=rotatingproxyua` (missing `-rotate`) AND `WEBSHARE_PROXY_HOST=proxy.webshare.io` (legacy host) | Updated both in Infisical dev+prod |
| 2026-03-13 | Duplicate "New Tutorial Video Detected" Telegram notifications | `_loop()` and `poll_now()` both emitted notifications independently for same video | Added `_dispatched_this_session` dedup set to `YouTubePlaylistWatcher` |
