---
name: youtube-transcript-metadata
description: >
  Fetch a YouTube video's full transcript text and rich metadata together in one parallel step.
  Use this skill whenever the user provides a YouTube URL or video ID and needs any of:
  transcript text, video title, channel name, upload date, view/like counts, description,
  or duration. Also use it for ingestion into larger workflows (tutorial creation, analysis,
  summarization) — it's the recommended first step for any YouTube content pipeline.
  Trigger on phrases like "get me the transcript", "grab the YouTube video", "fetch the
  content of this video", "what does this video say", "extract text from YouTube",
  "get the captions", or any time a YouTube URL appears in context and content access is needed.
---

# YouTube Transcript + Metadata

Use this skill as the **core YouTube ingestion building block**. It fetches transcript text
and video metadata in parallel using well-tested libraries with explicit error classes.

---

## Quick Start

```bash
# From repo root — fetch transcript + metadata and pretty-print to stdout
uv run .claude/skills/youtube-transcript-metadata/scripts/fetch_youtube_transcript_metadata.py \
  --url "https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
  --pretty

# Save JSON output + raw transcript text to files
uv run .claude/skills/youtube-transcript-metadata/scripts/fetch_youtube_transcript_metadata.py \
  --url "https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
  --json-out "$CURRENT_RUN_WORKSPACE/work_products/youtube_ingest.json" \
  --transcript-out "$CURRENT_RUN_WORKSPACE/work_products/transcript.txt"

# Verify dependencies are installed
uv run .claude/skills/youtube-transcript-metadata/scripts/fetch_youtube_transcript_metadata.py --self-test
```

> **Note:** The `on_pre_bash_inject_workspace_env` hook auto-injects `UV_CACHE_DIR=/tmp/uv_cache`.
> Include it explicitly (`UV_CACHE_DIR=/tmp/uv_cache`) only if calling outside the hook chain.
>
> **Workspace note:** `CURRENT_RUN_WORKSPACE` is the canonical durable workspace variable.
> `CURRENT_SESSION_WORKSPACE` may still exist as a legacy alias during migration.

---

## CLI Reference

| Flag | Default | Description |
|------|---------|-------------|
| `--url` | — | Full YouTube URL (watch, shorts, live, youtu.be) |
| `--video-id` | — | 11-character YouTube video ID (alternative to `--url`) |
| `--language` | `en` | Preferred transcript language; falls back to English |
| `--timeout-seconds` | `120` | Network timeout for metadata extraction |
| `--max-chars` | `180000` | Truncation limit for transcript text |
| `--min-chars` | `160` | Minimum chars required to pass quality check |
| `--json-out` | — | Optional path to write full JSON result |
| `--transcript-out` | — | Optional path to write plain-text transcript |
| `--pretty` | off | Pretty-print JSON to stdout |
| `--self-test` | off | Check imports only, no network calls |

---

## Workflow

1. **Normalize** the target (`--url` or `--video-id`) → extract 11-char video ID.
2. **Parallel extraction** using two threads:
   - **Transcript**: `youtube-transcript-api` (`YouTubeTranscriptApi().fetch(...)`)
   - **Metadata**: `yt-dlp` (skip_download mode)
3. **Quality check** transcript against `min_chars` threshold and repetition heuristics.
4. **Return** structured JSON with explicit `ok`, `status`, and `failure_class` fields.

---

## Output Interpretation

The script always returns a JSON object. The most important top-level fields:

| Field | Type | Meaning |
|-------|------|---------|
| `ok` | bool | `true` = transcript succeeded AND quality passed |
| `status` | str | `"succeeded"` or `"failed"` |
| `video_url` | str | Normalized URL |
| `video_id` | str | 11-char video ID |
| `transcript_text` | str | Full transcript (only present on success) |
| `transcript_chars` | int | Character count of transcript text |
| `transcript_truncated` | bool | `true` if transcript was cut at `max_chars` |
| `transcript_quality_score` | float | 0.0–1.0 quality score |
| `source` | str | Always `"youtube_transcript_api"` |
| `metadata` | dict | Rich video metadata from yt-dlp (see below) |
| `metadata_status` | str | `"attempted_succeeded"` or `"attempted_failed"` |
| `failure_class` | str | Error category if `ok=false` (see Error Handling) |
| `attempts` | list | Per-method detail for debugging |

**`metadata` sub-fields:**

| Field | Type | Meaning |
|-------|------|---------|
| `title` | str | Video title |
| `channel` | str | Channel/uploader name |
| `channel_id` | str | YouTube channel ID |
| `upload_date` | str | `YYYYMMDD` format |
| `duration` | int | Duration in seconds |
| `view_count` | int | View count at time of fetch |
| `like_count` | int | Like count (may be null if hidden) |
| `description` | str | Full video description |
| `webpage_url` | str | Canonical video URL |

> **Partial success is possible**: if transcript fetch fails but metadata succeeds,
> the response has `ok=false` but `metadata` is still populated — use it.

---

## Error Handling

When `ok=false`, check `failure_class` to decide how to proceed:

| `failure_class` | Meaning | Recommended action |
|-----------------|---------|-------------------|
| `invalid_video_target` | URL/ID could not be parsed | Ask user to verify the URL |
| `request_blocked` | IP blocked, captcha, or 429/403 | Enable Webshare proxy; retry once |
| `proxy_quota_or_billing` | Proxy credits exhausted | Alert user; check proxy billing |
| `proxy_auth_failed` | Bad proxy credentials | Check `PROXY_USERNAME`/`PROXY_PASSWORD` env vars |
| `api_unavailable` | Library import failed or unexpected error | Check dependency install; run `--self-test` |
| `empty_or_low_quality_transcript` | Transcript too short or repetitive | Video may have no captions; try a different language |

> If metadata succeeded but transcript failed, you can still use `metadata` fields
> (title, channel, description, etc.) for downstream tasks.

---

## Rules

- Transcript extraction **must** use `youtube-transcript-api` as source of truth.
- `yt-dlp` is for **metadata extraction only** — do not use it for transcript text.
- Do **not** use `YouTubeTranscriptApi.get_transcript(...)` (legacy API, deprecated).
- Keep failure classes explicit — never swallow errors silently.
- If transcript fails but metadata succeeds, still return metadata in the output.

---

## Proxy Configuration

Proxy support uses Webshare residential proxies. See full details in:

- `references/env_and_output.md`

**Quick check: do you need a proxy?**

- No proxy needed: most public videos on a clean residential or VPN IP
- Proxy recommended: datacenter/CI/CD environments that get IP-blocked by YouTube
- Proxy required: if you see `request_blocked` failure class

---

## Composition

| Scenario | Action |
|----------|--------|
| Tutorial creation | Run this skill first → then `youtube-tutorial-creation` for synthesis |
| Summarization / analysis | Run this skill → use `transcript_text` + `metadata` as context |
| Quick one-off YouTube task | Stop after this skill; return transcript + metadata payload directly |
| Metadata only needed | Run this skill; use `metadata` even if transcript fails |
