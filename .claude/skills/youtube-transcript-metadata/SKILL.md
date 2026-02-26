---
name: youtube-transcript-metadata
description: Fetch YouTube transcript text and video metadata together in one step (parallel extraction), with optional Webshare residential proxy support and quality/error classification. Use when any agent needs reliable YouTube transcript + metadata retrieval, either as a standalone task or as the ingestion stage for larger YouTube workflows.
---

# YouTube Transcript + Metadata

Use this as the core YouTube ingestion building block.

## Workflow

1. Normalize the target (`video_url` or `video_id`).
2. Run transcript + metadata extraction in parallel:
- Transcript source of truth: `youtube-transcript-api` (`YouTubeTranscriptApi().fetch(...)`)
- Metadata source: `yt-dlp`
3. Enforce transcript quality threshold before claiming success.
4. Return structured JSON with explicit status/error classes for both transcript and metadata.

## Run The Script

Script path:
- `scripts/fetch_youtube_transcript_metadata.py`

From repo root:

```bash
uv run .claude/skills/youtube-transcript-metadata/scripts/fetch_youtube_transcript_metadata.py \
  --url "https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
  --language en \
  --pretty
```

Optional output files:

```bash
uv run .claude/skills/youtube-transcript-metadata/scripts/fetch_youtube_transcript_metadata.py \
  --url "https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
  --json-out "$CURRENT_SESSION_WORKSPACE/work_products/youtube_ingest.json" \
  --transcript-out "$CURRENT_SESSION_WORKSPACE/work_products/transcript.txt"
```

Dependency self-test:

```bash
uv run .claude/skills/youtube-transcript-metadata/scripts/fetch_youtube_transcript_metadata.py --self-test
```

## Rules

- Transcript extraction must use `youtube-transcript-api` as source of truth.
- `yt-dlp` is allowed and expected for metadata extraction only.
- Do not use `YouTubeTranscriptApi.get_transcript(...)` legacy methods.
- Keep failure classes explicit (`request_blocked`, `api_unavailable`, `empty_or_low_quality_transcript`, `invalid_video_target`).
- If transcript fails but metadata succeeds, still return metadata in output.

## Proxy Configuration

The script supports both legacy and Webshare env names. See:
- `references/env_and_output.md`

## Composition

- For tutorial learning artifacts, use this skill first, then continue with `youtube-tutorial-creation` synthesis and artifact packaging.
- For quick one-off YouTube tasks, stop after this skill and return transcript + metadata payload directly.
