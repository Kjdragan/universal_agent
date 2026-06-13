# Output Contract

## Run Folder

Write durable outputs under:

`<resolved_artifacts_root>/youtube-tutorial-creation/{YYYY-MM-DD}/{video-slug}__{HHMMSS}/`

## Required Files

1. `manifest.json`
2. `README.md`
3. `CONCEPT.md`

## Recommended Files

1. `IMPLEMENTATION.md` (procedural usage runbook — never a code project)

## Optional Files

1. `transcript.txt`
2. `transcript.clean.txt`
3. `youtube_ingest.json`
4. `visuals/zai_video_analysis.md`
5. `research/*`

## Status Values

Set `manifest.json.status` to one of:

1. `full`
2. `degraded_transcript_only`
3. `failed`

## Mode Values

Set `manifest.json.learning_mode` to:

1. `concept_only` (always — the legacy concept-plus-implementation value is retired; runnable demos are built post-gate in `/opt/ua_demos` by the `tutorial_build` Task Hub lane)

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
  "learning_mode": "concept_only",
  "implementation_required": false,
  "video_url": "https://www.youtube.com/watch?v=<id>",
  "video_id": "<id>",
  "source": "manual|composio|direct",
  "metadata": {
    "title": "string|null",
    "channel": "string|null",
    "duration": "number|null",
    "upload_date": "string|null",
    "description": "string|null",
    "metadata_status": "attempted_succeeded|attempted_failed|not_attempted",
    "metadata_source": "yt_dlp|other"
  },
  "description_links": [
    {
      "url": "string",
      "type": "github_repo|kaggle_competition|kaggle_dataset|documentation|dataset|other",
      "fetched": "boolean",
      "resource_path": "string|null"
    }
  ],
  "extraction": {
    "transcript": "attempted_succeeded|attempted_failed|not_attempted",
    "metadata": "attempted_succeeded|attempted_failed|not_attempted",
    "visual": "attempted_succeeded|attempted_failed|not_attempted",
    "description_links": "attempted_succeeded|attempted_failed|not_attempted|skipped_no_links"
  },
  "notes": []
}
```

## Platform-Stamped Fields (do not author)

After the run completes, the hooks runtime stamps the building agent session
onto `manifest.json` (`hooks_service.py::_stamp_tutorial_manifest_build_session`):
`build_session_id`, `build_run_id`, `build_workspace_dir`. Never set, copy,
or remove these fields yourself — and never strip unrecognized fields when
rewriting the manifest.
