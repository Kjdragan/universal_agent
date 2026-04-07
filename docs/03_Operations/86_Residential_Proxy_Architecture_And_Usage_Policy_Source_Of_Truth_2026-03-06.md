# 86. Residential Proxy Architecture and Usage Policy Source of Truth (2026-03-06)

## Purpose

This document is the canonical source of truth for residential proxy usage in Universal Agent.

It defines where the residential proxy is used, why it exists, which use cases are approved, which are explicitly disallowed, what env vars control it, and what operational failure signatures matter.

## Executive Summary

Universal Agent uses a **two-tier transcript fetching architecture**:

1. **Primary: Desktop Transcript Worker** — runs on Kevin's desktop (residential IP), fetches transcripts locally without any proxy. This is the default and preferred path.
2. **Fallback: Webshare rotating residential proxy** — used only when the desktop worker is unavailable or when a local fetch fails.

The residential proxy remains available for a narrow set of approved cases where datacenter IPs are known to be blocked or degraded:
- YouTube transcript fetching (fallback when desktop worker fails)
- YouTube metadata extraction that is explicitly **metadata-only**
- approved TGTG proxy inheritance

The residential proxy is **cost-sensitive** and should not be treated as a generic project-wide scraping tunnel.

Current canonical implementation includes:
- `src/universal_agent/desktop_transcript_worker.py` — **primary** desktop residential IP transcript worker
- `src/universal_agent/youtube_ingest.py` — VPS proxy-based fallback path
- `src/universal_agent/gateway_server.py`
- `src/universal_agent/hooks_service.py`
- `src/universal_agent/tgtg/config.py`

Current runtime topology for YouTube transcript fetching is:
- **Desktop-primary** for transcript fetching via residential IP (no proxy needed)
- VPS proxy as fallback when desktop is unavailable or local fetch fails
- The desktop must be running and connected via SSH to the VPS for the primary path to work
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

### Primary Path: Desktop Transcript Worker

Implementation:
- `src/universal_agent/desktop_transcript_worker.py`

> [!IMPORTANT]
> The desktop transcript worker requires Kevin's desktop to be running.
> Without it, transcript fetching falls back to the VPS proxy path.

The desktop transcript worker:
- Runs on the desktop's residential IP — **no proxy needed**
- Queries the VPS CSI database (SSH) for videos with `transcript_status='failed'`
- Fetches transcripts via `youtube-transcript-api` locally
- Falls back to Webshare rotating proxy on local failure (configurable, default: ON)
- Writes successful transcripts back to the CSI database
- Has configurable rate limiting, circuit breaker, and batch caps
- Classifies failures loudly (self-imposed caps vs YouTube blocks vs circuit breaker)

This eliminates most proxy usage for YouTube transcript fetching, significantly reducing proxy bandwidth costs.

### Fallback Path: VPS Proxy

Implementation:
- `src/universal_agent/youtube_ingest.py`

The VPS proxy path is the legacy approach. It builds a Webshare proxy configuration from env and passes it into `youtube-transcript-api` when available.

Primary credential env vars:
- `PROXY_USERNAME`
- `PROXY_PASSWORD`

Alias/fallback env vars also supported in code:
- `WEBSHARE_PROXY_USER`
- `WEBSHARE_PROXY_PASS`
- `PROXY_FILTER_IP_LOCATIONS`
- `PROXY_LOCATIONS`
- `YT_PROXY_FILTER_IP_LOCATIONS`
- `WEBSHARE_PROXY_LOCATIONS`

Behavior:
- if credentials are missing, proxy mode becomes disabled
- if proxy module support is unavailable, proxy mode reports module unavailability
- if proxy is required and unavailable, the ingest hard fails instead of falling back silently to the VPS datacenter IP

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

TGTG can inherit shared Webshare credentials if explicit `TGTG_PROXIES` are not set.

Current behavior:
- if `TGTG_PROXIES` is set, use that explicit list
- otherwise, if shared Webshare credentials are present, build a single rotating residential URL from them

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

## Explicitly Disallowed Uses

The residential proxy must **not** be used for:
- video binary downloads
- generic web scraping by default
- random experimentation against unknown targets without explicit approval
- expensive bandwidth-heavy data transfer that is not part of an approved path
- retry loops that could burn through bandwidth (use one-off only)

The reason is both operational and financial:
- residential proxy traffic costs money
- bandwidth waste can exhaust quota quickly
- using the proxy indiscriminately creates silent cost and reliability regressions

## Current Policy

### Approved Uses

Approved now:
- YouTube transcript fetching via the desktop worker (residential IP, no proxy)
- YouTube transcript fetching via VPS proxy when desktop is unavailable
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

Primary credential env vars:
- `PROXY_USERNAME`
- `PROXY_PASSWORD`

Alias/fallback env vars seen in code:
- `WEBSHARE_PROXY_USER`
- `WEBSHARE_PROXY_PASS`
- `WEBSHARE_PROXY_HOST`
- `WEBSHARE_PROXY_PORT`
- `WEBSHARE_PROXY_LOCATIONS`

Canonical default Webshare residential endpoint:
- `p.webshare.io:80` (rotating residential)

Operational note:
- `proxy.webshare.io:80` is a **legacy static-proxy host** and should not be used. If this appears in configuration, update `WEBSHARE_PROXY_HOST` in Infisical to `p.webshare.io`.

Operational env vars:
- `UA_YOUTUBE_INGEST_REQUIRE_PROXY`
- `UA_HOOKS_YOUTUBE_INGEST_URLS`
- `UA_TUTORIAL_BOOTSTRAP_TARGET_ROOT`
- `PROXY_FILTER_IP_LOCATIONS`
- `PROXY_LOCATIONS`
- `YT_PROXY_FILTER_IP_LOCATIONS`

TGTG-specific env surface:
- `TGTG_PROXIES`

## Operational Health Signals

Healthy state for YouTube ingest with residential proxy:
- proxy credentials are present
- proxy mode resolves to `webshare`
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
- `scripts/check_webshare_proxy.py`
- `scripts/check_webshare_proxy_credentials.py`

Related tests and behavior references:
- `tests/unit/test_youtube_ingest.py`
- `tests/gateway/test_youtube_ingest_endpoint.py`
- `tests/test_desktop_transcript_worker.py`

## Bottom Line

The canonical residential proxy policy in Universal Agent is:
- **use the desktop transcript worker (residential IP) as the primary YouTube transcript path**
- **fall back to Webshare residential proxy when desktop is unavailable or local fetch fails**
- **require proxy for VPS-only YouTube transcript ingestion unless explicitly in local dev mode**
- **never send video binary through the proxy**
- **treat the proxy as a costly shared capability, not a default project-wide network path**
- **surface misconfiguration loudly through ingest failures and hook notifications**
- **treat `proxy_connect_failed` as a first-class proxy transport incident, not a generic API outage**
- **the desktop must be running for the primary transcript fetch path to work**
