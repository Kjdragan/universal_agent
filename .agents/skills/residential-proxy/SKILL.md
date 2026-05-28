---
name: residential-proxy
description: Route web requests through the project's rotating residential proxy (provider selected by PROXY_PROVIDER — default DataImpulse, Webshare available for failover) to bypass datacenter IP blocks. Use when scraping gets blocked because the VPS datacenter IP is detected, when agent-browser or playwright-cli encounters a Cloudflare "access denied" or bot-detection wall before even reaching a CAPTCHA, when you need to fetch content from a site that rate-limits or blocks server IPs, or when a previous scraping attempt failed with a 403/503 status. This skill provides the proxy URL that can be passed to Playwright, curl, httpx, or the captcha-solver skill. It does NOT solve CAPTCHAs itself — for that, chain with the captcha-solver skill. IMPORTANT: residential proxy bandwidth costs real money. Only use when standard fetching fails due to IP-based blocking.
---

# Residential Proxy Skill

This skill provides one-off access to the project's **rotating residential proxy** for situations where the VPS's datacenter IP gets blocked before you can even attempt to scrape content. The active provider is selected by `PROXY_PROVIDER` (default `dataimpulse`; `webshare` available as failover).

## When to use this

- A scraping attempt returned 403, 503, "Access Denied", or Cloudflare's "Just a moment..." page
- The VPS IP is flagged by a target site's bot-detection before reaching any CAPTCHA
- You need the content from behind an IP-reputation wall and normal browser approaches fail
- You want to chain: **residential proxy → captcha-solver** for maximum bypass capability

## When NOT to use this

- Standard fetching works fine (always try without proxy first)
- The content is behind login/auth (proxy doesn't help with that)
- Downloading large files or video binaries (exceeds bandwidth budget)

## Bandwidth Budget

We have a **3 GB/month** cap on residential proxy bandwidth. Typical web pages are 50-500 KB, so normal scraping is fine. But avoid routing large downloads or repeated bulk operations through the proxy.

## Usage

### Option 1: Get the proxy URL (for use with any tool)

The proxy URL format depends on `PROXY_PROVIDER`:
```
dataimpulse: http://<user>:<pass>@gw.dataimpulse.com:823   (default)
webshare:    http://<user>:<pass>@p.webshare.io:80
```

To resolve it from Infisical secrets at runtime:

```bash
uv run .agents/skills/residential-proxy/scripts/get_proxy_url.py
```

This prints the proxy URL to stdout. Credentials are loaded from Infisical via the project's `infisical_loader`.

### Option 2: Fetch a URL through the proxy (quick one-shot)

```bash
uv run .agents/skills/residential-proxy/scripts/proxy_fetch.py <URL> [--out /tmp/output.html] [--timeout 20]
```

This fetches the URL through the residential proxy and prints or saves the response body. Useful for quick verification that a site is accessible through the proxy.

### Option 3: Pass proxy to Playwright (for browser-based scraping)

```bash
PROXY_URL=$(uv run .agents/skills/residential-proxy/scripts/get_proxy_url.py)
# Then use in Playwright:
# browser = playwright.chromium.launch(proxy={"server": proxy_url})
```

### Option 4: Chain with captcha-solver

When a site blocks by IP AND has a CAPTCHA, use both skills together:

```bash
PROXY_URL=$(uv run .agents/skills/residential-proxy/scripts/get_proxy_url.py)
uv run .agents/skills/captcha-solver/scripts/solve_with_nopecha.py \
  "<URL>" \
  --proxy "$PROXY_URL" \
  --out-html /tmp/bypassed.html \
  --out-cookies /tmp/bypassed_cookies.json \
  --wait-time 20
```

### Option 5: Verify proxy health

```bash
uv run scripts/check_proxy.py          # honors PROXY_PROVIDER (default: dataimpulse)
uv run scripts/check_proxy.py --provider webshare    # explicit override
```

This runs the project's standard proxy health check (TCP + HTTPS CONNECT + YouTube probe) against the selected provider.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `proxy_not_configured` | Missing credentials for active provider | Check `PROXY_PROVIDER` then verify `DATAIMPULSE_PROXY_USER`/`DATAIMPULSE_PROXY_PASS` (or `PROXY_USERNAME`/`PROXY_PASSWORD` for webshare) in Infisical |
| `404 Not Found` on CONNECT | Wrong host or username | Confirm `gw.dataimpulse.com:823` (dataimpulse) or `p.webshare.io:80` (webshare) |
| `407 Proxy Auth Required` | Wrong password | Regenerate in provider dashboard, update Infisical |
| Timeouts | Bandwidth exhausted | Check provider dashboard for remaining quota (see also `get_dataimpulse_usage_stats()` in `youtube_ingest.py`) |

## Important rules

1. **Try without proxy first.** Only escalate to the proxy when you hit an IP-based block.
2. **Don't use in loops.** One-off usage only. No retry loops that could burn through bandwidth.
3. **Chain with captcha-solver when needed.** The proxy gets you past IP blocks; the captcha-solver handles CAPTCHA challenges. Use them together for maximum effectiveness.
4. **Small payloads only.** HTML pages are fine. Video/binary downloads are NOT.
