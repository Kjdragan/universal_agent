# Ingestion And Tooling Matrix

## Goal

Choose the lowest-risk combination that gives:

1. Reliable video identity/metadata.
2. Reliable transcript extraction.
3. Optional visual evidence extraction for Z.AI vision.

## Recommendation

Default stack for this project:

1. `yt-dlp` for metadata and media/frame access.
2. Transcript extraction via YouTube captions path (through `yt-dlp` caption download) and/or transcript API fallback.
3. Official YouTube API only when required for account-scoped operations (not for bulk polling).

This keeps costs low, avoids quota pressure, and preserves visual-analysis capability.

## Decision Matrix

### `yt-dlp`

Use for:

1. Video metadata (`title`, `id`, `duration`, `channel`) with `--print`.
2. Caption files (auto/manual) when available.
3. Video/frame extraction for vision analysis.

Pros:

1. No YouTube Data API quota consumption for most operations.
2. Strong support for URL variants and metadata fields.
3. Required path for Z.AI visual workflows.

Cons:

1. Can fail on specific videos/regions/age restrictions.
2. Large downloads can be slow.

### Transcript API approach (library/service)

Use for:

1. Fast transcript-only mode.
2. Long videos where full media download is unnecessary.

Pros:

1. Lightweight for text-first workflows.
2. Faster failure detection when captions are unavailable.

Cons:

1. No visual extraction support.
2. May fail on disabled captions/private-restricted videos.

### Official YouTube API

Use for:

1. Explicit account/channel management workflows.
2. Cases requiring authoritative API fields unavailable from other paths.

Pros:

1. Stable official schema and policy alignment.
2. Good for managed integrations.

Cons:

1. Quota/rate cost for polling-heavy designs.
2. Extra auth complexity.

## Runtime Strategy

1. Normalize URL/video ID.
2. Try lightweight metadata (`yt-dlp --print`).
3. Attempt transcript acquisition.
4. Attempt visual extraction only when needed and feasible.
5. If visual fails but transcript exists:
   1. Set status `degraded_transcript_only`.
   2. Continue and produce tutorial outputs.
6. Only return `failed` when no usable transcript/evidence can be obtained.

## Long Video Handling

For long videos:

1. Prioritize transcript-first flow.
2. For visual analysis, sample selected windows/frames instead of full download.
3. Document sampling strategy in `manifest.json` and `EXPLAINER.md`.
