# 45. YouTube Webhook Robustness And Gemini Video Analysis Implementation Ticket (2026-02-19)

## 1. Objective

Harden the YouTube webhook pipeline for production reliability and shift to a transcript-first + Gemini multimodal video-analysis workflow.

This ticket implements the following product decisions:

1. Stop using `yt-dlp` for transcript acquisition in webhook ingestion.
2. Use `youtube-transcript-api` as the single transcript source in ingestion worker.
3. Add anti-blocking mitigation through compliant traffic discipline (rate limits, backoff, dedupe, caching, quality gates), not public proxy rotation.
4. Add Gemini multimodal video analysis in the `youtube-tutorial-learning` skill to capture visual/audio evidence with timestamps.

## 2. Verified Current State

1. Webhook local ingest endpoint is active at `src/universal_agent/gateway_server.py:3715` (`/api/v1/youtube/ingest`).
2. Local ingest uses `ingest_youtube_transcript(...)` from `src/universal_agent/youtube_ingest.py:152`.
3. Current ingest still attempts `yt-dlp` first (`_run_yt_dlp_extract`) then falls back to `youtube-transcript-api`.
4. Hook orchestration and local ingest preflight are in `src/universal_agent/hooks_service.py:532` (`_prepare_local_youtube_ingest`).
5. YouTube webhook route target is `youtube-explainer-expert` in:
   1. `webhook_transforms/composio_youtube_transform.py:14`
   2. `webhook_transforms/manual_youtube_transform.py:13`
6. Skill and subagent definitions exist in:
   1. `.claude/skills/youtube-tutorial-learning/SKILL.md`
   2. `.claude/agents/youtube-explainer-expert.md`

## 3. Problem Statement

Recent VPS logs and session artifacts show mixed failure modes:

1. Frequent `yt-dlp` anti-bot failures (`Sign in to confirm you’re not a bot`, `nsig extraction failed`).
2. Intermittent webhook retries producing partial or empty session logs under `session_hook_yt_*_req-*`.
3. Some runs are not platform failures but payload mismatch issues (wrong `video_id` content vs expected tutorial).
4. Current visual-analysis path depends on optional tooling and is not standardized for deterministic production quality.

## 4. Scope

### In scope

1. Transcript ingest simplification to `youtube-transcript-api` only.
2. Ingestion anti-blocking resilience controls (compliant request shaping and dedupe).
3. Gemini multimodal video-analysis process in the YouTube skill.
4. Hook/session idempotency hardening for duplicate trigger bursts.
5. Tests and docs updates.

### Out of scope

1. Public/free proxy rotation.
2. Cookie-based account automation for YouTube scraping.
3. Full queue-service migration (can be a follow-up ticket).

## 5. Implementation Workstreams

## WS-A: Remove `yt-dlp` From Ingestion Worker

### Code touchpoints

1. `src/universal_agent/youtube_ingest.py`
2. `tests/unit/test_youtube_ingest.py`
3. `src/universal_agent/gateway_server.py` (request/response metadata consistency only)

### Required changes

1. Delete `yt-dlp` transcript path from `ingest_youtube_transcript(...)`.
2. Remove `_run_yt_dlp_extract(...)` and related attempt handling.
3. Make `youtube-transcript-api` the sole acquisition method.
4. Preserve `attempts` telemetry format with one method: `youtube_transcript_api`.
5. Keep `status` contract: `succeeded | failed` with explicit `error` codes.

### Acceptance criteria

1. Ingest no longer shells out to `yt-dlp`.
2. Unit tests cover success, import-fail, blocked/fail conditions.
3. Existing endpoint response shape remains backward-compatible.

## WS-B: Anti-Blocking Resilience (Compliant, Non-Proxy)

### Code touchpoints

1. `src/universal_agent/hooks_service.py`
2. `src/universal_agent/youtube_ingest.py`
3. `webhook_transforms/composio_youtube_transform.py`
4. `webhook_transforms/manual_youtube_transform.py`
5. `.env.sample`
6. `tests/test_hooks_service.py`

### Required changes

1. Add request pacing and retry discipline in hook ingest preflight:
   1. jittered retry backoff
   2. bounded retries
   3. short-term cooldown after repeated blocked responses
2. Add idempotency and dedupe for repeated webhook events for same canonical video target:
   1. stable dedupe key derived from authoritative `video_id` + event identity
   2. suppress duplicate in-flight dispatches
3. Add transcript quality gate in ingest result:
   1. minimum chars threshold
   2. reject low-information sign-off-only transcripts
4. Add explicit failure classes in ingest result metadata:
   1. `request_blocked`
   2. `empty_or_low_quality_transcript`
   3. `api_unavailable`
   4. `invalid_video_target`
5. Add env knobs:
   1. `UA_YT_INGEST_MIN_TRANSCRIPT_CHARS`
   2. `UA_YT_INGEST_DEDUPE_TTL_SECONDS`
   3. `UA_YT_INGEST_BLOCK_COOLDOWN_SECONDS`
   4. `UA_YT_INGEST_RETRY_JITTER_SECONDS`

### Acceptance criteria

1. Duplicate webhook bursts no longer create repeated empty `req-*` sessions for same event.
2. Low-quality transcript artifacts are marked degraded and clearly annotated.
3. Hook logs emit structured cause codes for each ingest failure.

## WS-C: Gemini Multimodal Video Analysis In Skill

### Code touchpoints

1. `.claude/skills/youtube-tutorial-learning/SKILL.md`
2. `.claude/skills/youtube-tutorial-learning/references/ingestion_and_tooling.md`
3. `.claude/skills/youtube-tutorial-learning/references/output_contract.md`
4. `.claude/agents/youtube-explainer-expert.md`
5. New script: `.claude/skills/youtube-tutorial-learning/scripts/gemini_video_analysis.py`
6. Optional helper docs: `.claude/skills/youtube-tutorial-learning/references/gemini_video_analysis.md`

### Required changes

1. Make Gemini video analysis the primary visual-analysis path for public YouTube URLs.
2. Use Gemini multimodal input via YouTube URL (`file_data.file_uri`) first.
3. Model default for video analysis: `gemini-2.5-pro` (env override supported).
4. Add script interface:
   1. inputs: `--video-url`, `--out-dir`, `--model`, optional `--fps`, optional clip ranges
   2. outputs:
      1. `visuals/gemini_video_analysis.json`
      2. `visuals/gemini_video_analysis.md`
      3. `visuals/timeline_events.json`
5. Force timestamped output format (`MM:SS`) and include confidence tags per event.
6. Update skill workflow to merge transcript + Gemini visual timeline into synthesis docs.

### Acceptance criteria

1. Every successful run includes Gemini visual analysis artifacts unless explicitly marked unavailable.
2. Manifest captures visual extraction status and model used.
3. `CONCEPT.md` references timestamped visual findings when present.

## WS-D: Manifest And Output Contract Hardening

### Code touchpoints

1. `.claude/skills/youtube-tutorial-learning/references/output_contract.md`
2. `.claude/skills/youtube-tutorial-learning/SKILL.md`
3. `webhook_transforms/composio_youtube_transform.py`
4. `webhook_transforms/manual_youtube_transform.py`

### Required changes

1. Extend required manifest fields:
   1. `ingest.failure_class`
   2. `ingest.transcript_quality_score`
   3. `visual.model`
   4. `visual.source` (`gemini_youtube_url`)
   5. `authoritative_video_id` and `authoritative_video_url`
2. Keep authoritative payload enforcement and mismatch detection.
3. Ensure degraded mode still produces full durable package.

### Acceptance criteria

1. Manifest structure is deterministic across success/degraded/failure runs.
2. Video mismatch is explicitly classified and never misreported as network failure.

## WS-E: Observability And Ops Readiness

### Code touchpoints

1. `src/universal_agent/hooks_service.py`
2. `src/universal_agent/gateway_server.py`
3. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/29_Hybrid_Youtube_Ingestion_LocalWorker_Runbook_2026-02-18.md`
4. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/42_Hybrid_Local_VPS_Webhook_Operations_Source_Of_Truth_2026-02-18.md`

### Required changes

1. Emit structured log fields for ingest events:
   1. `video_id`, `session_id`, `failure_class`, `retry_count`, `dedupe_hit`, `transcript_chars`, `quality_pass`
2. Add operator checks for:
   1. dedupe hit rate
   2. blocked error rate
   3. degraded completion rate
3. Document runbook queries and expected thresholds.

### Acceptance criteria

1. Operator can diagnose failures from logs without opening individual run transcripts.
2. Post-deploy checks include YouTube ingest health signals.

## 6. Test Plan

### Unit

1. `tests/unit/test_youtube_ingest.py`
   1. success via transcript API
   2. import failure
   3. blocked/failure classification
   4. low-quality transcript gate
2. `tests/test_webhook_transforms.py`
   1. authoritative payload propagation unchanged
   2. dedupe key behavior for repeated events

### Integration

1. `tests/test_hooks_service.py`
   1. local ingest success path
   2. pending/deferred path
   3. dedupe/in-flight suppression path
   4. timeout + retry accounting

### Manual VPS verification

1. Trigger repeated webhook payload for same video within dedupe TTL.
2. Confirm one active dispatch path and no duplicate empty `req-*` sessions.
3. Validate artifact bundle includes transcript + Gemini visual outputs.

## 7. Rollout Plan

1. Land WS-A and WS-B together.
2. Deploy to VPS and run a controlled 5-video webhook soak.
3. Land WS-C and WS-D.
4. Deploy and run end-to-end artifact quality review.
5. Land WS-E docs/ops updates and close ticket.

## 8. Risks And Mitigations

1. Risk: transcript API may still be intermittently blocked on cloud IPs.
   1. Mitigation: strict dedupe, pacing, retry discipline, quality gates, clear degraded mode.
2. Risk: Gemini YouTube URL video input behavior/rate limits change.
   1. Mitigation: model/config env overrides and fallback to transcript-only degraded mode.
3. Risk: cost/latency increases from multimodal analysis.
   1. Mitigation: clip windows, lower media resolution for long videos, and optional context caching follow-up.

## 9. Definition Of Done

1. `yt-dlp` is removed from webhook transcript ingestion path.
2. Ingestion is idempotent and resilient under duplicate webhook bursts.
3. Skill consistently produces transcript + Gemini visual-analysis evidence (or explicit degraded reason).
4. Manifest and logs make failure causes operationally diagnosable.
5. Tests pass for ingest/hook/transform paths and runbook docs are updated.

## 10. Immediate Next Action

Implement WS-A and WS-B first, deploy, and run a 5-video webhook soak before enabling WS-C by default.
