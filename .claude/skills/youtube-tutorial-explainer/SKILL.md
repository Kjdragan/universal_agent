---
name: youtube-tutorial-explainer
description: |
  Create an explainer-first tutorial artifact from a YouTube video so the user can learn without watching the full video.
  Use when input is a YouTube URL or YouTube trigger payload (manual webhook or Composio trigger), and produce concise teachable notes with optional code only when it materially improves learning.
---

# YouTube Tutorial Explainer (V2)

Build a practical tutorial package from a YouTube video with this priority:

1. Teach clearly from transcript + visual evidence.
2. Add implementation/code only when it truly helps.
3. Do not fail if video download/vision fails and transcript is usable.

## When To Use

Use this skill when any of these are true:

1. User shares a YouTube URL and asks for a tutorial, summary, or implementation guide.
2. A webhook payload contains a YouTube video URL/video ID.
3. You need an "explain it like a tutorial" result with optional code appendix.

## Output Policy (Mandatory)

1. Use `CURRENT_SESSION_WORKSPACE` only for temporary downloads/processing.
2. Write durable outputs to `UA_ARTIFACTS_DIR`.
3. Create per-run folder:
`UA_ARTIFACTS_DIR/youtube-tutorial-explainer/{YYYY-MM-DD}/{video-id-or-slug}__{HHMMSS}/`

Minimum files:

1. `manifest.json`
2. `EXPLAINER.md` (primary deliverable)
3. `KEY_POINTS.md` (ultra-condensed checklist)
4. `sources.md`

Optional files:

1. `CODE_APPENDIX.md` (only when code is genuinely useful)
2. `transcript.clean.txt`
3. `visuals/` (keyframes/OCR/notes)

## Workflow

1. Normalize input:
   1. Accept direct YouTube URL.
   2. Accept webhook payload fields like `video_url`, `video_id`, `channel_id`, `mode`, `allow_degraded_transcript_only`.
2. Gather lightweight metadata (title, channel, duration, id) using `yt-dlp --print` fields (avoid huge metadata payloads).
3. Acquire transcript:
   1. First available path from `references/ingestion_and_tooling.md`.
   2. Normalize and dedupe into `transcript.clean.txt`.
4. Acquire visual evidence (best effort):
   1. Use `yt-dlp` clip/frame extraction as needed.
   2. Run Z.AI vision analysis when available.
   3. If visual fails, continue transcript-only and mark degraded mode in `manifest.json`.
5. Write explainer-first outputs:
   1. `EXPLAINER.md`: teach concepts, workflow, pitfalls, assumptions, where uncertainty exists.
   2. `KEY_POINTS.md`: concise operational checklist.
   3. `CODE_APPENDIX.md`: include only minimal/high-value code.
6. Finalize `manifest.json` with status:
   1. `full`
   2. `degraded_transcript_only`
   3. `failed`

## Quality Rules

1. Prefer tutorial clarity over exhaustive dumps.
2. Never claim visual findings without evidence.
3. Never treat missing video as automatic hard failure if transcript is sufficient.
4. Do not hardcode secrets or tokens in outputs.
5. Do not run `uv add`, `pip install`, or mutate project dependencies for one-off runs.

## Trigger Ingress

Supported trigger paths in this project:

1. `POST /api/v1/hooks/composio` (Composio-triggered)
2. `POST /api/v1/hooks/youtube/manual` (manual URL ingestion)

Use `references/composio_wiring_checklist.md` for setup/validation.

## References

1. Tool/API selection and fallback matrix:
   `references/ingestion_and_tooling.md`
2. Composio + manual webhook setup checklist:
   `references/composio_wiring_checklist.md`
3. Deliverable schema and status contract:
   `references/output_contract.md`

If a user asks for a "YouTube tutorial summary," default to this skill.
