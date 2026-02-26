# Output Contract

## Run Folder

Write durable outputs under:

`<resolved_artifacts_root>/youtube-tutorial-creation/{YYYY-MM-DD}/{video-slug}__{HHMMSS}/`

## Required Files

1. `manifest.json`
2. `README.md`
3. `CONCEPT.md`
4. `IMPLEMENTATION.md`
5. `implementation/` (directory)

## Optional Files

1. `transcript.txt`
2. `transcript.clean.txt`
3. `youtube_ingest.json`
4. `visuals/gemini_video_analysis.md`
5. `research/*`

## Status Values

Set `manifest.json.status` to one of:

1. `full`
2. `degraded_transcript_only`
3. `failed`

## Mode Values

Set `manifest.json.learning_mode` to one of:

1. `concept_only`
2. `concept_plus_implementation`

## Path Rules (Mandatory)

1. Never use a literal `UA_ARTIFACTS_DIR` folder segment in output paths.
2. Invalid:
   1. `/opt/universal_agent/UA_ARTIFACTS_DIR/...`
   2. `UA_ARTIFACTS_DIR/...`
3. Always resolve the absolute artifacts root first, then append `youtube-tutorial-creation/...`.

## Minimal Manifest Shape

```json
{
  "skill": "youtube-tutorial-creation",
  "status": "full",
  "learning_mode": "concept_plus_implementation",
  "video_url": "https://www.youtube.com/watch?v=<id>",
  "video_id": "<id>",
  "source": "manual|composio|direct",
  "metadata": {
    "title": "string|null",
    "channel": "string|null",
    "duration": "number|null",
    "upload_date": "string|null",
    "metadata_status": "attempted_succeeded|attempted_failed|not_attempted",
    "metadata_source": "yt_dlp|other"
  },
  "extraction": {
    "transcript": "attempted_succeeded|attempted_failed|not_attempted",
    "metadata": "attempted_succeeded|attempted_failed|not_attempted",
    "visual": "attempted_succeeded|attempted_failed|not_attempted"
  },
  "notes": []
}
```
