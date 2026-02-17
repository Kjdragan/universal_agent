# Ingestion and Tooling Matrix

## Goal

Choose the most reliable path for:

1. Video identity and lightweight metadata.
2. Transcript extraction (primary + fallback).
3. Optional visual evidence extraction.

## Recommended Stack

1. `yt-dlp` for metadata and media/frame access.
2. `youtube-transcript-api` fallback for transcript-first recovery.
3. Official YouTube API only when account-scoped operations are explicitly required.

## Decision Matrix

### `yt-dlp`

Use for:

1. Metadata (`title`, `id`, `duration`, `channel`) using `--print`.
2. Captions via `--write-auto-subs` when available.
3. Frame/media sampling for visual evidence.

Known risk:

1. YouTube anti-bot challenge (`Sign in to confirm you’re not a bot`) can block extraction.

### `youtube-transcript-api`

Use for:

1. Transcript fallback when `yt-dlp` transcript path fails.
2. Fast text-first recovery mode.

Required API shape in this repo:

```python
from youtube_transcript_api import YouTubeTranscriptApi
api = YouTubeTranscriptApi()
transcript = api.fetch(video_id)
```

Do not use legacy `YouTubeTranscriptApi.get_transcript(...)` in this project.

### Official YouTube API

Use for:

1. Explicit channel/account workflows.
2. Cases where official API-only fields are required.

## Runtime Strategy

1. Normalize `video_url` and `video_id`.
2. Run lightweight metadata extraction.
3. Attempt transcript extraction.
4. Attempt visual extraction when feasible.
5. If visual fails but transcript exists:
   1. Continue with `degraded_transcript_only`.
   2. Still emit complete required artifacts.
6. Return `failed` only when no usable evidence can be extracted.
