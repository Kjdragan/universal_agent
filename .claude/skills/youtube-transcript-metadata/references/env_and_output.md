# Env And Output Reference

## Proxy Environment Variables

Credential fallback order:

1. `PROXY_USERNAME` / `PROXY_PASSWORD`
2. `WEBSHARE_PROXY_USER` / `WEBSHARE_PROXY_PASS`

Optional location filters (comma-separated country codes):

1. `PROXY_FILTER_IP_LOCATIONS`
2. `PROXY_LOCATIONS`
3. `YT_PROXY_FILTER_IP_LOCATIONS`
4. `WEBSHARE_PROXY_LOCATIONS`

## Script Output Shape

Top-level fields:

- `ok` (`true|false`)
- `status` (`succeeded|failed`)
- `video_url`, `video_id`
- `transcript_text`, `transcript_chars`, `transcript_truncated`
- `transcript_quality_score`, `transcript_quality_pass`
- `source` (transcript source)
- `metadata` (yt-dlp fields)
- `metadata_status`, `metadata_source`, `metadata_error`, `metadata_failure_class`
- `attempts` (structured method-level details)

Failure classes:

- `invalid_video_target`
- `request_blocked`
- `api_unavailable`
- `empty_or_low_quality_transcript`
