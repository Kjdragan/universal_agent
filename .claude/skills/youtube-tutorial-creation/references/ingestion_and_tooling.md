# Ingestion and Tooling Matrix

## Goal

Choose the most reliable path for:

1. Video identity and lightweight metadata.
2. Transcript extraction.
3. Optional visual evidence extraction.

## Recommended Stack

1. Core ingestion script from `youtube-transcript-metadata` skill for parallel transcript+metadata extraction.
2. `youtube-transcript-api` as transcript source of truth.
3. `yt-dlp` for video metadata extraction.
4. Gemini multimodal video understanding for audio+visual analysis.
5. Official YouTube API only when account-scoped operations are explicitly required.

Core script path:

`/home/kjdragan/lrepos/universal_agent/.claude/skills/youtube-transcript-metadata/scripts/fetch_youtube_transcript_metadata.py`

## Decision Matrix

### `youtube-transcript-api` (transcript source of truth)

Use for:

1. Transcript retrieval with `YouTubeTranscriptApi().fetch(video_id)`.
2. Fast text-first evidence path for learning artifacts.
3. Consistent behavior across local and VPS workers.

Required API shape in this repo:

```python
from youtube_transcript_api import YouTubeTranscriptApi
api = YouTubeTranscriptApi()
transcript = api.fetch(video_id)
```

Do not use legacy `YouTubeTranscriptApi.get_transcript(...)` in this project.
Do not use `yt-dlp` transcript extraction in this project.

### `yt-dlp` (metadata only)

Use for:

1. Title/channel/duration/view stats/description in one request.
2. Metadata retrieval in parallel with transcript extraction.
3. Preserving useful context even when transcript extraction fails.

### Gemini multimodal video understanding

Use for:

1. Visual+audio analysis directly from the public YouTube URL.
2. Timestamped scene/event extraction.
3. Fallback when transcripts are weak and visual context is needed.

Recommended model:

1. `gemini-3-pro-preview` for highest-quality multimodal analysis.

### Official YouTube API

Use for:

1. Explicit channel/account workflows.
2. Cases where official API-only fields are required.

## Runtime Strategy

1. Normalize `video_url` and `video_id`.
2. Run core ingestion script (parallel transcript + metadata).
3. Keep transcript source of truth on `youtube-transcript-api`.
4. Attempt Gemini multimodal video analysis when feasible.
5. If visual fails but transcript exists:
   1. Continue with `degraded_transcript_only`.
   2. Still emit complete required artifacts.
6. If transcript fails but metadata succeeds:
   1. Preserve metadata in manifest.
   2. Mark transcript extraction failure class explicitly.
7. Return `failed` only when no usable evidence can be extracted.
