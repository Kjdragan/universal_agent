# 86. Residential Proxy Architecture and Usage Policy Source of Truth (2026-03-06)

## Purpose

This document is the canonical source of truth for residential proxy usage in Universal Agent.

It defines where the residential proxy is used, why it exists, which use cases are approved, which are explicitly disallowed, what env vars control it, and what operational failure signatures matter.

## Executive Summary

Universal Agent supports **two rotating residential proxy providers** for YouTube transcript fetching on the VPS:

| Provider | Endpoint | Port | Config Class | Default |
|---|---|---|---|---|
| **Webshare** | `p.webshare.io` | `80` | `WebshareProxyConfig` | ✅ (default) |
| **DataImpulse** | `gw.dataimpulse.com` | `823` | `GenericProxyConfig` | — |

The active provider is selected by the `PROXY_PROVIDER` env var (default: `webshare`).

> [!NOTE]
> The desktop transcript worker was decommissioned in April 2026. All transcript
> fetching now runs on the VPS via `youtube_ingest.py` with residential proxy.

The residential proxy is used for a narrow set of approved cases where datacenter IPs are known to be blocked or degraded:
- YouTube transcript fetching (primary path via `youtube_ingest.py`)
- YouTube metadata extraction that is explicitly **metadata-only**
- approved TGTG proxy inheritance

The residential proxy is **cost-sensitive** and should not be treated as a generic project-wide scraping tunnel.

Current canonical implementation includes:
- `src/universal_agent/youtube_ingest.py` — primary VPS transcript fetching with dual-provider proxy routing
- `src/universal_agent/gateway_server.py`
- `src/universal_agent/hooks_service.py`
- `src/universal_agent/tgtg/config.py`

Current runtime topology for YouTube transcript fetching is:
- VPS-primary for all transcript fetching via rotating residential proxy (Webshare or DataImpulse)
- VPS-primary for playlist watching, hook ingest, artifact generation, and repo bootstrap

## Why the Residential Proxy Exists

Some external services, especially YouTube transcript fetching from server-hosted datacenter IPs, can block or challenge requests aggressively.

The residential proxy exists to:
- avoid known datacenter IP blocking
- preserve reliability for approved ingestion paths
- allow transcript and metadata retrieval without moving the entire system to a residential network

It is not intended for broad indiscriminate scraping.

## Canonical Current Uses

## 1. YouTube Transcript Fetching

### Primary Path: Dual-Provider Residential Proxy

Implementation:
- `src/universal_agent/youtube_ingest.py`

The VPS transcript fetch path uses rotating residential proxies to bypass YouTube's datacenter IP blocking. It is exposed via the gateway endpoint `/api/v1/youtube/ingest` and called by the CSI enrichment pipeline.

The proxy provider is selected by `PROXY_PROVIDER` (default: `webshare`):

| Provider | Builder function | Config Class |
|---|---|---|
| `webshare` | `_build_webshare_proxy_config()` | `WebshareProxyConfig` |
| `dataimpulse` | `_build_dataimpulse_proxy_config()` | `GenericProxyConfig` |

The router function `_build_proxy_config()` reads `PROXY_PROVIDER` and dispatches to the appropriate builder.

Credential env vars by provider:

| Provider | Username env | Password env | Host env | Port env |
|---|---|---|---|---|
| webshare | `PROXY_USERNAME` / `WEBSHARE_PROXY_USER` | `PROXY_PASSWORD` / `WEBSHARE_PROXY_PASS` | `WEBSHARE_PROXY_HOST` | `WEBSHARE_PROXY_PORT` |
| dataimpulse | `DATAIMPULSE_PROXY_USER` | `DATAIMPULSE_PROXY_PASS` | `DATAIMPULSE_PROXY_HOST` | `DATAIMPULSE_PROXY_PORT` |

Behavior:
- if credentials are missing for the selected provider, proxy mode becomes disabled
- if proxy module support is unavailable, proxy mode reports module unavailability
- if proxy is required and unavailable, the ingest hard fails instead of falling back silently to the VPS datacenter IP
- API-first metadata (YouTube Data API v3), yt-dlp as metadata fallback (no proxy for metadata)
- **Pre-ingest triage gate** screens metadata BEFORE consuming proxy bandwidth (see §1a below)



### 1a. Pre-Ingest Metadata Triage Gate

Added: 2026-04-18

Implementation:
- `src/universal_agent/youtube_ingest.py` → `_should_skip_video_by_metadata()`

The pre-ingest triage gate is a **zero-cost filter** that screens already-fetched metadata (from the free YouTube Data API v3 or yt-dlp fallback) to skip transcript fetches for videos unlikely to produce valuable transcripts. It runs BEFORE any proxy bandwidth is consumed.

**Filter criteria:**

| Gate | Threshold | Rationale |
|---|---|---|
| Too short | < 60 seconds | Promo clips, intros, shorts — minimal transcript value |
| Too long | > 5400 seconds (1.5 hr) | Likely livestream replay — excessive bandwidth for low-density content |
| Music category | `categoryId == 10` | Music video transcripts are lyrics-only filler |
| Live/upcoming broadcast | `liveBroadcastContent ∈ {live, upcoming}` | No transcript yet, or hours of low-density chat |

**Behavior:**
- Videos matching any gate are returned with `failure_class: "pre_ingest_triage"` and `status: "skipped"`
- The `pre_ingest_triage` failure class is registered as **non-retryable** — the hooks service will never retry these
- The response includes `proxy_bandwidth_saved: true` for telemetry
- The metadata is still returned in the response so downstream consumers can inspect it

> [!IMPORTANT]
> The triage gate is conservative by design — it only filters obvious low-value content.
> Borderline cases (e.g., 55-second tutorials) should err on the side of fetching.
> Thresholds are defined as module constants (`_MIN_DURATION_SECONDS`, `_MAX_DURATION_SECONDS`, `_MUSIC_CATEGORY_ID`).

### 1b. Transcript Truncation (NOT a Bandwidth Optimization)

> [!WARNING]
> The `max_chars` parameter in `ingest_youtube_transcript()` truncates the transcript **AFTER** the
> full transcript has already been downloaded through the residential proxy. This does NOT save
> proxy bandwidth — it only caps downstream processing size. To save actual proxy bandwidth,
> use the pre-ingest triage gate above which skips the transcript fetch entirely.
## 2. YouTube Metadata Extraction

Also in:
- `src/universal_agent/youtube_ingest.py`

Metadata extraction uses `yt-dlp` with:
- `skip_download=True`
- `download=False`

This is critical.

The approved use is **metadata only**.

The system must not route video binary downloads through the residential proxy, because that would waste bandwidth and quota rapidly.

## 3. TGTG Proxy Inheritance

Implementation:
- `src/universal_agent/tgtg/config.py`

TGTG can inherit shared residential proxy credentials if explicit `TGTG_PROXIES` are not set.

Current behavior:
- if `TGTG_PROXIES` is set, use that explicit list
- otherwise, if `TGTG_PROXY_FALLBACK=true` and `PROXY_PROVIDER` is set, build a single rotating residential URL from the selected provider's credentials (Webshare or DataImpulse)

This is an approved use.

## 4. Agent One-Off Proxy Skill (residential-proxy)

Added: 2026-04-07

Implementation:
- `.agents/skills/residential-proxy/SKILL.md`
- `.agents/skills/residential-proxy/scripts/get_proxy_url.py`
- `.agents/skills/residential-proxy/scripts/proxy_fetch.py`

The `residential-proxy` agent skill provides **one-off** access to the Webshare rotating residential proxy for situations where the VPS datacenter IP is blocked by a target site before any content or CAPTCHA is reachable.

This skill:
- Loads credentials from Infisical via `initialize_runtime_secrets()` (same pattern as `check_webshare_proxy.py`)
- Prints or returns the full proxy URL for use with any HTTP client, Playwright, or curl
- Includes a `proxy_fetch.py` convenience script that fetches a URL through the proxy and prints/saves the response

This is an **approved use** with cost-sensitivity rules:
- Try without proxy first — only escalate when you hit an IP-based block (403, 503, Cloudflare "Access Denied")
- One-off usage only, no retry loops
- Small HTML payloads only — never route binary or video downloads through this path
- 3 GB/month bandwidth cap applies

## 5. CAPTCHA Solver Chaining (captcha-solver + residential-proxy)

Added: 2026-04-07

Implementation:
- `.agents/skills/captcha-solver/SKILL.md`
- `.agents/skills/captcha-solver/scripts/solve_with_nopecha.py`

The `captcha-solver` skill uses the NopeCHA browser extension to automatically solve CAPTCHAs (Cloudflare Turnstile, reCAPTCHA, hCaptcha). It now accepts a `--proxy` flag for chaining with the residential proxy:

```bash
PROXY_URL=$(uv run .agents/skills/residential-proxy/scripts/get_proxy_url.py)
uv run .agents/skills/captcha-solver/scripts/solve_with_nopecha.py \
  "<URL>" --proxy "$PROXY_URL" --out-html /tmp/bypassed.html --wait-time 30
```

Chaining rationale:
- The residential proxy gets past IP-reputation blocks (datacenter IP rejected before any challenge)
- The captcha solver handles CAPTCHA challenges that appear after IP validation passes
- Together they provide maximum bypass capability for stubborn anti-bot sites

> [!IMPORTANT]
> The captcha solver has a **100 attempts/day** limit (NopeCHA free tier). Use judiciously.

## 6. Full Payload A/V Fetching (Native VPS + PoT)

Added: 2026-04-08

Implementation:
- `.agents/skills/youtube-media/SKILL.md`
- `.agents/skills/youtube-media/scripts/fetch_youtube_media.py`

When the agent requires raw media blobs (a full audio stream `.m4a` or video `.mp4`), the system must strictly avoid using the rotating residential proxy. Heavy A/V downloads are approved **only** when routed natively on the VPS IP combined with the `bgutil-ytdlp-pot-provider` PoT token generator to bypass YouTube signatures natively. 

This hybrid extraction strategy bifurcates traffic to avoid datacenter IP bans under rapid-fire API limits, while rescuing the project from astronomical residential proxy gigabyte charges on multi-megabyte media payloads.

## Explicitly Disallowed Uses

The residential proxy must **not** be used for:
- video binary downloads (Media payloads must route natively via PoT, never the proxy)
- generic web scraping by default
- random experimentation against unknown targets without explicit approval
- expensive bandwidth-heavy data transfer that is not part of an approved path
- retry loops that could burn through bandwidth (use one-off only)
- **YouTube video description link fetching** — links extracted from video descriptions (GitHub repos, Kaggle datasets, documentation pages) are standard public web resources that should be fetched using direct connections

The reason is both operational and financial:
- residential proxy traffic costs money
- bandwidth waste can exhaust quota quickly
- using the proxy indiscriminately creates silent cost and reliability regressions

## Current Policy

### Approved Uses

Approved now:
- YouTube transcript fetching via VPS Webshare rotating residential proxy
- YouTube metadata-only extraction paired with transcript workflows
- approved TGTG use
- **one-off agent scraping** via the `residential-proxy` skill when a target site blocks the VPS datacenter IP
- **captcha bypass chaining** via `residential-proxy` → `captcha-solver` when a site blocks by IP AND has a CAPTCHA
- explicitly user-authorized additional use cases when anti-bot behavior justifies the cost and the user understands the tradeoff

### Requires Explicit Approval

Any new non-YouTube, non-TGTG, non-one-off use should be considered opt-in and explicitly authorized by the user.

### Never Allowed by Default

- binary video fetch through proxy
- broad general scraping through the proxy
- automated retry loops through the proxy

## Enforced Guardrails

## 1. Require-Proxy Hard Fail for YouTube

Primary implementation:
- `src/universal_agent/gateway_server.py`
- `src/universal_agent/youtube_ingest.py`

The gateway reads:
- `UA_YOUTUBE_INGEST_REQUIRE_PROXY`

Default behavior is effectively:
- `1` on VPS / production-like operation
- only disable intentionally for local workstation development

When `require_proxy=True` and proxy credentials are missing, ingest returns:
- `error=proxy_not_configured`
- `failure_class=proxy_not_configured`

This prevents dangerous silent fallback to a datacenter IP that is likely to be blocked by YouTube.

## 2. Alerting in Hooks Service

Implementation:
- `src/universal_agent/hooks_service.py`

The hooks service currently treats these as proxy-alert classes:
- `proxy_quota_or_billing`
- `proxy_auth_failed`
- `proxy_not_configured`
- `proxy_connect_failed`

Current alert posture includes:
- human-readable failure formatting
- explicit `PROXY ALERT` language
- CRITICAL notification when the proxy is not configured at all

This is important because missing proxy configuration can block the entire YouTube transcript path.

## Configuration Surface

### Provider Selection

- `PROXY_PROVIDER` — selects active provider: `webshare` (default) or `dataimpulse`

### Webshare Credential Env Vars

- `PROXY_USERNAME` / `WEBSHARE_PROXY_USER`
- `PROXY_PASSWORD` / `WEBSHARE_PROXY_PASS`
- `WEBSHARE_PROXY_HOST` (default: `p.webshare.io`)
- `WEBSHARE_PROXY_PORT` (default: `80`)
- `WEBSHARE_PROXY_LOCATIONS`

Canonical default Webshare residential endpoint:
- `p.webshare.io:80` (rotating residential)

Operational note:
- `proxy.webshare.io:80` is a **legacy static-proxy host** and should not be used. If this appears in configuration, update `WEBSHARE_PROXY_HOST` in Infisical to `p.webshare.io`.

### DataImpulse Credential Env Vars

- `DATAIMPULSE_PROXY_USER`
- `DATAIMPULSE_PROXY_PASS`
- `DATAIMPULSE_PROXY_HOST` (default: `gw.dataimpulse.com`)
- `DATAIMPULSE_PROXY_PORT` (default: `823`)

Canonical default DataImpulse residential endpoint:
- `gw.dataimpulse.com:823`

### Operational Env Vars

- `UA_YOUTUBE_INGEST_REQUIRE_PROXY`
- `UA_HOOKS_YOUTUBE_INGEST_URLS`
- `UA_TUTORIAL_BOOTSTRAP_TARGET_ROOT`
- `PROXY_FILTER_IP_LOCATIONS`
- `PROXY_LOCATIONS`
- `YT_PROXY_FILTER_IP_LOCATIONS`

TGTG-specific env surface:
- `TGTG_PROXIES`
- `TGTG_PROXY_FALLBACK`

## Operational Health Signals

Healthy state for YouTube ingest with residential proxy:
- proxy credentials are present for the selected provider
- proxy mode resolves to `webshare` or `dataimpulse` (matching `PROXY_PROVIDER`)
- transcript fetch succeeds without bot-block failure signatures
- metadata extraction succeeds without downloading video content
- hooks service does not emit proxy alert notifications

Unhealthy state indicators:
- `proxy_not_configured`
- `proxy_auth_failed`
- `proxy_quota_or_billing`
- `proxy_connect_failed`
- repeated ingest failures with bot-block style errors
- proxy mode unexpectedly `disabled` in an environment that requires it

Operational diagnostics:
- terminal hook ingest failures should persist `local_ingest_result.json` in the run workspace
- use `scripts/check_proxy.py --provider <webshare|dataimpulse>` to verify TCP, HTTP, HTTPS, and YouTube connectivity through the proxy
- use `scripts/check_youtube_ingress_readiness.py --probe-video-id <public_video_id> --json` to verify end-to-end proxy-backed ingest from the active runtime

## Cost and Safety Rules

This is a cost-sensitive resource.

Operator and implementation policy should assume:
- every byte matters
- the proxy should be used only where it materially improves success rate
- bulk or binary traffic through the proxy is a red flag
- if a new feature wants proxy usage, it should justify the need and the expected data footprint

## Current Gaps and Follow-Up Items

1. **Policy centralization was previously fragmented**
   - proxy rules lived across code, tests, and discussion context
   - this doc is intended to centralize them

2. **More explicit env documentation could help**
   - some proxy env vars appear only in code paths and not yet in a single operator-facing table elsewhere

3. **Approved-use expansion should remain deliberate**
   - future additions should be documented here when approved rather than growing by ad hoc convention

## Source Files That Define Current Truth

Primary implementation:
- `src/universal_agent/youtube_ingest.py`
- `src/universal_agent/gateway_server.py`
- `src/universal_agent/hooks_service.py`
- `src/universal_agent/tgtg/config.py`

Agent skill implementation:
- `.agents/skills/residential-proxy/SKILL.md`
- `.agents/skills/residential-proxy/scripts/get_proxy_url.py`
- `.agents/skills/residential-proxy/scripts/proxy_fetch.py`
- `.agents/skills/captcha-solver/SKILL.md`
- `.agents/skills/captcha-solver/scripts/solve_with_nopecha.py`

Diagnostic scripts:
- `scripts/check_proxy.py` — provider-agnostic probe (supports `--provider webshare` and `--provider dataimpulse`)
- `scripts/purge_youtube_backlog.py` — operational reset covering **both** YouTube pipelines:
  - Steps 1-3: Playlist Watcher (state file + stale runs + signal cards)
  - Steps 4-5: CSI RSS Channel Feed (source_state in csi.db + dedupe keys)
- `scripts/check_webshare_proxy.py` (legacy, Webshare-only)
- `scripts/check_webshare_proxy_credentials.py` (legacy, Webshare-only)

> [!CAUTION]
> **Proxy Provider Switch Checklist:** When switching providers (e.g., Webshare → DataImpulse):
> 1. Update `PROXY_PROVIDER` in Infisical
> 2. Run `purge_youtube_backlog.py` (covers both pipelines)
> 3. Restart the gateway and CSI Ingester services
> 4. Verify proxy connectivity with `check_proxy.py --provider <new_provider>`
Related tests and behavior references:
- `tests/unit/test_youtube_ingest.py`
- `tests/gateway/test_youtube_ingest_endpoint.py`

## DataImpulse Bandwidth Telemetry

Added: 2026-04-18

Implementation:
- `src/universal_agent/youtube_ingest.py` → `get_dataimpulse_usage_stats()`

The DataImpulse User API (port 777) provides real-time bandwidth usage stats. The `get_dataimpulse_usage_stats()` helper function makes a direct HTTPS request (NOT through the proxy) to `https://gw.dataimpulse.com:777/api/stats` with Basic Auth using the same proxy credentials.

**Response fields:**

| Field | Description |
|---|---|
| `total_traffic` | Total bandwidth allocation |
| `traffic_used` | Bandwidth consumed so far |
| `traffic_left` | Remaining bandwidth |
| `used_threads` | Currently active proxy threads |

**Usage:**
```python
from universal_agent.youtube_ingest import get_dataimpulse_usage_stats

stats = get_dataimpulse_usage_stats()
if stats.get("ok"):
    print(f"Bandwidth remaining: {stats['traffic_left']}")
else:
    print(f"API error: {stats.get('error')}")
```

This can be integrated into the health heartbeat, cron jobs, or manual diagnostics to monitor bandwidth consumption and implement threshold-based throttling.

> [!NOTE]
> The telemetry API uses the same `DATAIMPULSE_PROXY_USER`/`DATAIMPULSE_PROXY_PASS` credentials but on port 777
> (management API), not port 823 (proxy traffic). This is a free API call that does not consume bandwidth.

## Bottom Line

The canonical residential proxy policy in Universal Agent is:
- **use rotating residential proxy (Webshare or DataImpulse, selected by `PROXY_PROVIDER`) as the primary YouTube transcript path on the VPS**
- **require proxy for VPS YouTube transcript ingestion unless explicitly in local dev mode**
- **screen videos with the pre-ingest triage gate BEFORE consuming proxy bandwidth — filter by duration, category, and live status**
- **never send video binary through the proxy**
- **treat the proxy as a costly shared capability, not a default project-wide network path**
- **surface misconfiguration loudly through ingest failures and hook notifications — provider-aware error messages reference the active provider**
- **treat `proxy_connect_failed` as a first-class proxy transport incident, not a generic API outage**
- **maintain both providers' credentials in Infisical for failover resilience**
- **monitor bandwidth consumption via the DataImpulse User API (`get_dataimpulse_usage_stats()`) and implement threshold alerts**
