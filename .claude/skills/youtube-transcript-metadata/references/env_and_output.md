# Env And Output Reference

## Proxy Environment Variables

Credential fallback order (first non-empty pair wins):

| Priority | Username var | Password var |
|----------|-------------|-------------|
| 1 (highest) | `PROXY_USERNAME` | `PROXY_PASSWORD` |
| 2 | `WEBSHARE_PROXY_USER` | `WEBSHARE_PROXY_PASS` |

Optional location filters (comma-separated ISO country codes, e.g. `US,CA,GB`):

| Priority | Variable |
|----------|----------|
| 1 | `PROXY_FILTER_IP_LOCATIONS` |
| 2 | `PROXY_LOCATIONS` |
| 3 | `YT_PROXY_FILTER_IP_LOCATIONS` |
| 4 | `WEBSHARE_PROXY_LOCATIONS` |

If no credentials are set, proxy is disabled and all requests go direct.

---

## Script Output Shape

### Full success example

```json
{
  "ok": true,
  "status": "succeeded",
  "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "video_id": "dQw4w9WgXcQ",
  "transcript_text": "We're no strangers to love...",
  "transcript_chars": 4821,
  "transcript_truncated": false,
  "source": "youtube_transcript_api",
  "transcript_quality_score": 0.8714,
  "transcript_quality_pass": true,
  "metadata": {
    "title": "Rick Astley - Never Gonna Give You Up (Official Music Video)",
    "channel": "Rick Astley",
    "channel_id": "UCuAXFkgsw1L7xaCfnd5JJOw",
    "upload_date": "20091025",
    "duration": 213,
    "view_count": 1600000000,
    "like_count": 16000000,
    "description": "The official video for ...",
    "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  },
  "metadata_status": "attempted_succeeded",
  "metadata_source": "yt_dlp",
  "metadata_error": null,
  "metadata_failure_class": null,
  "attempts": [
    {"method": "youtube_transcript_api", "ok": true, ...},
    {"method": "yt_dlp_metadata", "ok": true, ...}
  ]
}
```

### Failure example (transcript blocked, metadata OK)

```json
{
  "ok": false,
  "status": "failed",
  "error": "youtube_transcript_api_failed",
  "failure_class": "request_blocked",
  "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "video_id": "dQw4w9WgXcQ",
  "metadata": { "title": "...", "channel": "..." },
  "metadata_status": "attempted_succeeded",
  "metadata_source": "yt_dlp",
  "metadata_error": null,
  "metadata_failure_class": null,
  "attempts": [...]
}
```

---

## Top-Level Field Reference

| Field | Type | Present when |
|-------|------|-------------|
| `ok` | bool | Always |
| `status` | str | Always (`succeeded` / `failed`) |
| `video_url` | str | Always (normalized) |
| `video_id` | str | Always (or null if invalid) |
| `transcript_text` | str | `ok=true` only |
| `transcript_chars` | int | `ok=true` only |
| `transcript_truncated` | bool | `ok=true` only |
| `transcript_quality_score` | float | When transcript was evaluated |
| `transcript_quality_pass` | bool | `ok=true` only |
| `source` | str | When transcript was evaluated |
| `metadata` | dict | When metadata fetch was attempted (success or partial) |
| `metadata_status` | str | Always (`attempted_succeeded` / `attempted_failed`) |
| `metadata_source` | str | Always (`yt_dlp`) |
| `metadata_error` | str\|null | When metadata failed |
| `metadata_failure_class` | str\|null | When metadata failed |
| `error` | str | `ok=false` only |
| `failure_class` | str | `ok=false` only |
| `attempts` | list | Always — per-method detail |

---

## Failure Classes

| Class | Cause |
|-------|-------|
| `invalid_video_target` | URL or video ID could not be parsed |
| `request_blocked` | IP blocked, captcha challenge, HTTP 429/403 |
| `proxy_quota_or_billing` | Proxy credits exhausted or billing error |
| `proxy_auth_failed` | Proxy credentials rejected (407) |
| `api_unavailable` | Library import failed or unexpected API error |
| `empty_or_low_quality_transcript` | Transcript too short or repetitive content only |

---

## Quality Score Formula

`transcript_quality_score` is a float in `[0.0, 1.0]`:

```
score = 0.6 * min(chars / 6000, 1.0) + 0.4 * max(unique_line_ratio, 0.0)
```

- **Length component (60%)**: saturates at 6,000 characters
- **Uniqueness component (40%)**: ratio of unique lines to total lines

A transcript **fails** quality (`ok=false`) if:

- Fewer characters than `--min-chars` (default: 160)
- Contains only sign-off boilerplate AND is under 280 chars
- Unique-line ratio < 0.15 AND total chars < 500

---

## `attempts` Array

Each entry in `attempts` corresponds to one extraction method:

```json
{"method": "youtube_transcript_api", "ok": true, "transcript_text": "...", "source": "...", ...}
{"method": "yt_dlp_metadata",        "ok": true, "metadata": {...}, "source": "yt_dlp"}
```

Inspect `attempts` when debugging partial failures — it shows per-method errors while
the top-level result may report a consolidated failure class.
