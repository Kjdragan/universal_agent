# Output Contract

## Run Folder

Write durable outputs under:

`UA_ARTIFACTS_DIR/youtube-tutorial-explainer/{YYYY-MM-DD}/{video-id-or-slug}__{HHMMSS}/`

## Required Files

1. `manifest.json`
2. `EXPLAINER.md`
3. `KEY_POINTS.md`
4. `sources.md`

## Optional Files

1. `CODE_APPENDIX.md`
2. `transcript.clean.txt`
3. `visuals/*`

## Status Values

Set `manifest.json.status` to one of:

1. `full`
2. `degraded_transcript_only`
3. `failed`

## Mode Values

Set `manifest.json.mode` to one of:

1. `explainer_only`
2. `explainer_plus_code`

Default mode is `explainer_only`.

## Minimal Manifest Shape

```json
{
  "skill": "youtube-tutorial-explainer",
  "status": "full",
  "mode": "explainer_only",
  "input": {
    "video_url": "https://www.youtube.com/watch?v=<id>",
    "video_id": "<id>",
    "source": "manual|composio|direct"
  },
  "extraction": {
    "transcript": "attempted_succeeded|attempted_failed|not_attempted",
    "visual": "attempted_succeeded|attempted_failed|not_attempted"
  },
  "notes": [
    "visual analysis skipped due to download failure; transcript-only mode used"
  ]
}
```
