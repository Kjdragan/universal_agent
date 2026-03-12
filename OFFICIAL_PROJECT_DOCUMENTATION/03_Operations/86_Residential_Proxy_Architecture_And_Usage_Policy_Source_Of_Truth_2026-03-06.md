# 86. Residential Proxy Architecture and Usage Policy Source of Truth (2026-03-06)

## Purpose

This document is the canonical source of truth for residential proxy usage in Universal Agent.

It defines where the residential proxy is used, why it exists, which use cases are approved, which are explicitly disallowed, what env vars control it, and what operational failure signatures matter.

## Executive Summary

Universal Agent uses a **Webshare rotating residential proxy** for a narrow set of approved cases where datacenter IPs are known to be blocked or degraded.

Current primary approved uses are:
- YouTube transcript fetching
- YouTube metadata extraction that is explicitly **metadata-only**
- approved TGTG proxy inheritance

The residential proxy is **cost-sensitive** and should not be treated as a generic project-wide scraping tunnel.

Current canonical implementation is primarily centered on:
- `src/universal_agent/youtube_ingest.py`
- `src/universal_agent/gateway_server.py`
- `src/universal_agent/hooks_service.py`
- `src/universal_agent/tgtg/config.py`

Current runtime topology for the YouTube tutorial pipeline is:
- VPS-primary for playlist watching, hook ingest, artifact generation, and repo bootstrap
- local workstation as explicit dev fallback only
- VPS ingest should prefer loopback `http://127.0.0.1:8002/api/v1/youtube/ingest` unless an operator intentionally overrides it

## Why the Residential Proxy Exists

Some external services, especially YouTube transcript fetching from server-hosted datacenter IPs, can block or challenge requests aggressively.

The residential proxy exists to:
- avoid known datacenter IP blocking
- preserve reliability for approved ingestion paths
- allow transcript and metadata retrieval without moving the entire system to a residential network

It is not intended for broad indiscriminate scraping.

## Canonical Current Uses

## 1. YouTube Transcript Fetching

Primary implementation:
- `src/universal_agent/youtube_ingest.py`

The YouTube ingest path builds a Webshare proxy configuration from env and passes it into `youtube-transcript-api` when available.

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

## Explicitly Disallowed Uses

The residential proxy must **not** be used for:
- video binary downloads
- generic web scraping by default
- random experimentation against unknown targets without explicit approval
- expensive bandwidth-heavy data transfer that is not part of an approved path

The reason is both operational and financial:
- residential proxy traffic costs money
- bandwidth waste can exhaust quota quickly
- using the proxy indiscriminately creates silent cost and reliability regressions

## Current Policy

### Approved Uses

Approved now:
- YouTube transcript fetching via the dedicated ingest path
- YouTube metadata-only extraction paired with transcript workflows
- approved TGTG use
- explicitly user-authorized additional use cases when anti-bot behavior justifies the cost and the user understands the tradeoff

### Requires Explicit Approval

Any new non-YouTube, non-TGTG use should be considered opt-in and explicitly authorized by the user.

### Never Allowed by Default

- binary video fetch through proxy
- broad general scraping through the proxy

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
- `p.webshare.io:80`

Operational note:
- `proxy.webshare.io:80` is a stale default for the residential ingest path and may surface as proxy CONNECT failures such as `Tunnel connection failed: 404 Not Found`.

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

Related tests and behavior references:
- `tests/unit/test_youtube_ingest.py`
- `tests/gateway/test_youtube_ingest_endpoint.py`

## Bottom Line

The canonical residential proxy policy in Universal Agent is:
- **use Webshare residential proxy for approved anti-bot-sensitive paths**
- **require it for VPS YouTube transcript ingestion unless explicitly in local dev mode**
- **never send video binary through it**
- **treat it as a costly shared capability, not a default project-wide network path**
- **surface misconfiguration loudly through ingest failures and hook notifications**
- **treat `proxy_connect_failed` as a first-class proxy transport incident, not a generic API outage**
