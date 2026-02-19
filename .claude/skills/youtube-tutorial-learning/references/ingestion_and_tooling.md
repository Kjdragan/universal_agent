# Ingestion and Tooling Matrix

## Goal

Choose the most reliable path for:

1. Video identity and lightweight metadata.
2. Transcript extraction.
3. Optional visual evidence extraction.

## Recommended Stack

1. `youtube-transcript-api` as the transcript source of truth.
2. Gemini multimodal video understanding for audio+visual analysis.
3. Lightweight YouTube metadata via URL parsing plus oEmbed/API calls.
4. Official YouTube API only when account-scoped operations are explicitly required.

## Decision Matrix

### `youtube-transcript-api`

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
2. Run lightweight metadata extraction (URL parse + oEmbed/API).
3. Attempt transcript extraction via `youtube-transcript-api`.
4. Attempt Gemini multimodal video analysis when feasible.
5. If visual fails but transcript exists:
   1. Continue with `degraded_transcript_only`.
   2. Still emit complete required artifacts.
6. Return `failed` only when no usable evidence can be extracted.
