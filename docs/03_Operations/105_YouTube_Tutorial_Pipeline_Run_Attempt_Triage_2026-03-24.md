# YouTube Tutorial Pipeline Run/Attempt Triage (2026-03-24)

## Purpose

This report audits the notification-center evidence for the YouTube tutorial pipeline after the run/attempt refactor work. The goal was to answer four concrete questions:

1. Did the new run/attempt model actually back the YouTube tutorial hook flow?
2. Did the recent scary tutorial notifications represent real failures or false negatives?
3. When retries happened, did they happen as durable attempts or via some older path?
4. What code changes were required to make the notifications more accurate and more reassuring?

## Evidence Reviewed

The findings below are based on direct inspection of:

- `AGENT_RUN_WORKSPACES/activity_state.db`
- `AGENT_RUN_WORKSPACES/runtime_state.db`
- recent hook workspaces under `AGENT_RUN_WORKSPACES/session_hook_yt_*`
- the live hook/notification code paths in:
  - `src/universal_agent/hooks_service.py`
  - `src/universal_agent/services/tutorial_telegram_notifier.py`
  - `src/universal_agent/gateway_server.py`

## Executive Summary

The good news:

- The notification center evidence shows the tutorial pipeline is often producing valid artifacts successfully.
- We confirmed recent successful tutorial completions for multiple videos.
- We found and fixed a real false-negative bug that was turning some successful tutorial runs into `youtube_tutorial_interrupted` notifications with `retry queued`.
- We improved the success notification messaging so a recovered/retried tutorial can explicitly say that the later attempt completed normally.

The original audit limitation was:

- At the time of the first audit, the YouTube tutorial hook pipeline was **not yet backed by the new durable run/attempt model** in production behavior.
- At that time, the flow was still primarily driven by `session_hook_yt_*` workspaces plus `pending_hook_recovery.json` startup-recovery markers.
- At that time, `runtime_state.db` contained `runs = 1` and `run_attempts = 0` for the tutorial-hook cases reviewed here.

So the first audit was **not** a clean end-to-end validation of the new run/attempt architecture for the tutorial pipeline. It was a mixed result:

- the run/attempt refactor worked in other areas,
- but the tutorial webhook path still follows the legacy session/recovery-marker model,
- and a separate manifest-validation bug made some successful runs look like failures.

## Closure Update

That gap is now closed in code.

The follow-up migration landed these changes:

- `src/universal_agent/workflow_admission.py` now exposes the lifecycle helpers needed to drive real durable attempts:
  - `mark_running(...)`
  - `mark_blocked(...)`
  - `mark_needs_review(...)`
  - `queue_retry(...)`
- `src/universal_agent/hooks_service.py` now admits YouTube tutorial webhook work through `WorkflowAdmissionService`, creates/advances durable `run_attempts`, and uses runtime DB state as the primary recovery source.
- `src/universal_agent/cron_service.py` now admits cron executions through `WorkflowAdmissionService` and records durable workflow run/attempt context for retries and completion.
- `src/universal_agent/gateway_server.py` now emits cron retry notifications with workflow metadata, and the gateway notification layer continues to auto-resolve stale tutorial failure notifications when later success/recovery arrives.

Current code-state summary:

- New tutorial webhook work now creates `run_kind = "youtube_tutorial_hook"` durable runs.
- Tutorial retries now create additional `run_attempts` under the same run instead of relying only on `pending_hook_recovery.json`.
- New cron dispatches now create `run_kind = "cron_job_dispatch"` durable runs and attempts.
- Tutorial and cron notifications now include `run_id`, `attempt_id`, and `attempt_number`.

The remaining marker files are now compatibility or coordination artifacts, not the primary lifecycle authority:

- `pending_local_ingest.json` remains the local-ingest coordination artifact.
- `pending_hook_recovery.json` remains a legacy compatibility input during the migration window.

## Agent And Skill Instruction Audit

I also checked the active YouTube agent and skill instructions to see whether they were
still teaching the old session model.

### What was already correct

- `.claude/agents/youtube-expert.md` already treated durable tutorial output as
  `UA_ARTIFACTS_DIR/youtube-tutorial-creation/...`, not as a session-workspace concern.
- `src/universal_agent/prompt_assets/capabilities.md` already described
  `youtube-tutorial-creation` as a durable-artifact skill under `UA_ARTIFACTS_DIR`.

### What was still outdated

- `.claude/skills/youtube-transcript-metadata/SKILL.md` still showed transient output
  examples under `CURRENT_SESSION_WORKSPACE/...`
- `.claude/skills/youtube-tutorial-creation/SKILL.md` still used
  `CURRENT_SESSION_WORKSPACE` in its transient ingest and transcript-cleanup examples

### What was changed

Those skill files were updated in this pass so:

- `CURRENT_RUN_WORKSPACE` is now the primary documented scratch/output variable
- `CURRENT_SESSION_WORKSPACE` is only referenced as a legacy alias during migration

### Conclusion

The session-shaped behavior was **not primarily coming from the YouTube agent or tutorial
skill contract**. That drift was corrected, and the hook dispatcher gap has now also been
closed in code. The remaining session terminology on this path is now limited to true live
provider-session concepts and short-term compatibility readers.

## What The Notification Center Is Actually Reading

The relevant notification-center evidence is in `AGENT_RUN_WORKSPACES/activity_state.db`, not in `runtime_state.db`.

Observed counts during this audit:

- `activity_state.db.activity_events = 2402`
- `youtube_tutorial_ready = 26`
- `youtube_tutorial_started = 25`
- `youtube_tutorial_interrupted = 9`
- `youtube_ingest_failed = 18`

By contrast:

- `runtime_state.db.activity_events = 0`
- `runtime_state.db.runs = 1`
- `runtime_state.db.run_attempts = 0`

This matters because the notification-center feed can look active and detailed even when the durable run/attempt store was not actually tracking that particular pipeline yet. These counts are historical audit evidence from before the follow-up migration landed.

## High-Level State Of Tutorial Notifications

Across 45 tutorial-linked video timelines in `activity_state.db`:

- `22` currently end in `youtube_tutorial_ready`
- `5` currently end in `youtube_tutorial_interrupted`
- `0` currently end in `youtube_tutorial_failed`
- `18` currently end in other ingest-stage states such as `youtube_ingest_failed`
- `2` older interrupted/failure notifications were already auto-resolved after later recovery/success

That means the pipeline is not broadly dead. Most tutorial-linked timelines end in a usable success state. The problem is that some of the scary interruption notifications are either:

- real interrupted runs awaiting startup recovery, or
- false negatives where the artifacts were already created successfully.

## Key Finding At Audit Time: The Tutorial Hook Flow Was Still Session-Based

The tutorial webhook pipeline still uses hook-session workspaces and marker-based recovery:

- hook sessions are created/resumed by `HooksService._resolve_or_create_webhook_session(...)`
- interrupted retries are persisted via `pending_hook_recovery.json`
- restart/backfill recovery is driven by `HooksService.recover_interrupted_youtube_sessions(...)`
- the notification kinds are emitted directly from `hooks_service.py`

At audit time, this flow did **not** call durable run admission or attempt creation in the hook service path.

### Practical consequence

When a tutorial hook run fails dispatch, it does **not** become:

- one durable run
- with attempt 1, attempt 2, attempt 3

Instead it currently becomes:

- one `session_hook_yt_*` workspace
- plus `pending_hook_recovery.json`
- plus a later startup/backfill recovery attempt if the service restarts or the recovery loop picks it up

That was the main reason the original tutorial notifications did not behave like the new run/attempt model you expected.

## Triage Of The Recent Tutorial Notifications

### 1. `PG6w8_HEn-o`

- Latest event: `youtube_tutorial_ready`
- Time: `2026-03-25T00:55:56.383821+00:00`
- Session: `session_hook_yt_UC4FK5DEcMLB3CyJcbJfZEJA_PG6w8_HEn-o`
- Classification: completed normally

Evidence:

- `sync_ready.json` shows `state = completed`, `ready = true`
- `execution_summary.tool_calls = 27`
- no `pending_hook_recovery.json` remains

Conclusion:

- This one completed normally.

### 2. `O7T_5uXhWyk`

- Latest event: `youtube_tutorial_ready`
- Time: `2026-03-25T00:55:55.677459+00:00`
- Session: `session_hook_yt_UCZ2UamYbfBiUJN6zABwOQtg_O7T_5uXhWyk`
- Classification: completed normally

Evidence:

- `sync_ready.json` shows `state = completed`, `ready = true`
- `execution_summary.tool_calls = 8`
- no `pending_hook_recovery.json` remains

Conclusion:

- This one completed normally.

### 3. `7AO4w4Y_L24`

- Latest event: `youtube_tutorial_interrupted`
- Time: `2026-03-25T00:53:53.091095+00:00`
- Session: `session_hook_yt_UC0C-17n9iuUQPylguM1d-lQ_7AO4w4Y_L24`
- Classification: **false-negative interruption**

Evidence:

- activity event says: `hook_dispatch_failed; retry 1/2 queued for startup recovery`
- workspace contains `pending_hook_recovery.json`
- `sync_ready.json` says `state = dispatch_failed`
- but the transcript and trace show the specialist successfully created:
  - `manifest.json`
  - `README.md`
  - `CONCEPT.md`
- `sdk_result_messages` show `subtype = success`, `stop_reason = end_turn`, `is_error = false`
- the final tool output states: `The YouTube tutorial creation run has been completed successfully.`

Conclusion:

- This should have been treated as a completed tutorial artifact package, not as a retry-queued interruption.
- This was caused by the manifest-validation bug fixed in this pass.

### 4. `O9p6vNHwFlA`

- Latest event: `youtube_tutorial_interrupted`
- Time: `2026-03-24T19:24:16.337632+00:00`
- Session: `session_hook_yt_UC3R57cNcy6hmpb-H4huxg6A_O9p6vNHwFlA`
- Classification: **false-negative interruption**

Evidence:

- activity event says: `hook_dispatch_failed; retry 1/2 queued for startup recovery`
- workspace contains `pending_hook_recovery.json`
- transcript shows a completed artifact package at:
  - `artifacts/youtube-tutorial-creation/O9p6vNHwFlA__2026-03-24/`
- `sdk_result_messages` show `subtype = success`, `stop_reason = end_turn`, `is_error = false`
- the final tool output states: `The YouTube tutorial creation run has completed successfully.`

Conclusion:

- This was another false-negative interruption caused by the same manifest-validation bug.

### 5. `xUlX6jvwVfM`

- Latest event: `youtube_tutorial_interrupted`
- Time: `2026-03-24T18:57:49.570779+00:00`
- Session: `session_hook_yt_UCgfe2ooZD3VJPB6aJAnuQng_xUlX6jvwVfM`
- Classification: **real interruption**

Evidence:

- workspace contains `pending_hook_recovery.json`
- transcript shows the hook request payload
- transcript explicitly records: `No tools called in this iteration`
- `sdk_result_messages` show `is_error = true`
- `tool_calls = 0`

Conclusion:

- This appears to be a genuine interrupted dispatch before the specialist actually executed useful work.
- This item still needs a real recovery attempt.

### 6. `W0vaSVCKIlY`

- Latest event: `youtube_tutorial_interrupted`
- Time: `2026-03-24T18:49:40.691055+00:00`
- Session: `session_hook_yt_UCBHcMCGaiJhv-ESTcWGJPcw_W0vaSVCKIlY`
- Classification: **false-negative interruption after degraded artifact creation**

Evidence:

- local ingest reported `failed_fail_open`
- transcript shows the pipeline still wrote a degraded artifact package:
  - `manifest.json`
  - `README.md`
  - `CONCEPT.md`
  - plus `youtube_ingest.json`
- the package explicitly documents `request_blocked` / transcript extraction failure
- despite that, the top-level hook notification ended as `youtube_tutorial_interrupted`

Conclusion:

- The ingestion did have a real upstream problem, but the tutorial pipeline still created the intended degraded package.
- This should have been surfaced as a completed degraded outcome, not as a retry-queued interruption.
- This was also affected by the manifest-validation bug.

### 7. `d_gp7TOsUSo`

- Latest event: `youtube_tutorial_interrupted`
- Time: `2026-03-18T16:59:45.571094+00:00`
- Classification: stale legacy pending interruption

Evidence:

- activity timeline exists
- corresponding hook workspace was not present during this audit

Conclusion:

- This looks like an older stale activity record without surviving local workspace evidence.
- It should be treated as legacy/stale rather than as current operational proof of a broken pipeline.

## Root Cause Of The False-Negative Tutorial Interruptions

The core bug was in tutorial artifact validation:

- `HooksService._find_recent_tutorial_manifest(...)` only matched manifests where `video_id` was stored at the top level
- several newer tutorial manifests store the authoritative video ID under `video.video_id`

Result:

- the specialist created a valid artifact package
- the validator failed to find that manifest
- the hook path raised `youtube_artifacts_missing_manifest`
- the outer dispatch handler downgraded the run to `hook_dispatch_failed`
- the notification center showed `YouTube Tutorial Failed — Retry Queued`

That is why `7AO4w4Y_L24`, `O9p6vNHwFlA`, and `W0vaSVCKIlY` looked scary even though the artifact package already existed.

## Code Fixes Applied In This Pass

### 1. Manifest discovery now understands nested video IDs

Changed in:

- `src/universal_agent/hooks_service.py`

What changed:

- tutorial manifest lookup now accepts either:
  - top-level `video_id`
  - nested `video.video_id`
- tutorial title extraction now also accepts nested `video.title`

Impact:

- valid tutorial artifact packages are no longer misclassified as missing.

### 2. Post-dispatch success salvage

Changed in:

- `src/universal_agent/hooks_service.py`

What changed:

- if a tutorial hook raises a post-dispatch error but the artifact package already validates successfully, the hook is now treated as completed instead of failed

Impact:

- successful artifact output is no longer re-labeled as `hook_dispatch_failed` just because a later cleanup/finalization step had a problem.

### 3. Recovery-aware ready messaging

Changed in:

- `src/universal_agent/hooks_service.py`
- `src/universal_agent/services/tutorial_telegram_notifier.py`

What changed:

- when a tutorial succeeds after an automatic recovery/retry path, the ready notification now includes human-readable attempt counts
- example shape:
  - `artifacts are ready after automatic recovery on attempt 2/3`

Impact:

- future notifications are more reassuring and make the recovery behavior explicit instead of forcing the operator to infer it from an earlier failure event.

## Validation After The Fix

Targeted and adjacent regression coverage passed:

- `tests/test_hooks_service.py`
- `tests/unit/test_tutorial_pipeline_comprehensive.py`
- `tests/unit/test_tutorial_telegram_dedup.py`
- `tests/unit/test_tutorial_notification_dedup.py`
- `tests/unit/test_hooks_youtube_resilience.py`
- `tests/gateway/test_ops_api.py -k "youtube_tutorial or youtube_hook_recovery or csi_reports_fallbacks_to_runs_when_notifications_empty"`

Observed passing results during this audit:

- `5 passed`
- `3 passed`
- `35 passed`
- `1 passed, 111 deselected`

Python compile checks also passed for the touched modules.

## Direct Answer To The Original Run/Attempt Question

### Did the new run-based system work here?

At audit time, no.

In current code, yes for new occurrences of this pipeline:

- new tutorial webhook dispatches are admitted through `WorkflowAdmissionService`
- new tutorial retries create durable `run_attempts`
- startup recovery now prefers runtime DB state and only uses legacy marker files as compatibility fallback

### Did we have second attempts or third attempts under one run?

At audit time, not in the durable run/attempt sense.

In current code, that behavior is now represented as first-class durable attempts under one run for newly admitted tutorial webhook work.

### Did the process ultimately complete?

For the most recent reviewed tutorial items:

- `PG6w8_HEn-o`: yes
- `O7T_5uXhWyk`: yes
- `7AO4w4Y_L24`: yes in practice, but falsely reported as interrupted before this fix
- `O9p6vNHwFlA`: yes in practice, but falsely reported as interrupted before this fix
- `W0vaSVCKIlY`: degraded artifact package completed, but falsely reported as interrupted before this fix
- `xUlX6jvwVfM`: no, this one still appears to be a real interrupted run awaiting recovery

## Remaining Compatibility Layer

The primary architectural gap described earlier is no longer the current code state.

What remains on this path is narrower:

1. keep live provider session ids as `session_hook_yt_*`, because they are true runtime sessions
2. keep `pending_local_ingest.json` as the local-ingest coordination artifact
3. keep reading legacy `pending_hook_recovery.json` during the migration window
4. accept that historical activity rows from before this patch remain session-shaped evidence

So the pipeline now behaves primarily like:

- `run` + `attempt 1` + `attempt 2`

with compatibility readers for legacy hook markers instead of marker files being the primary retry record.

## Bottom Line

The first real-world check of the refactor produced a mixed but useful result:

- the original audit correctly found that the tutorial webhook path was still outside the durable run/attempt model,
- the audit also found and fixed a manifest-validation bug that made some successful tutorial runs look failed,
- and the follow-up migration has now moved both tutorial hooks and cron admission onto the durable run/attempt model.

The historical notification evidence in this document still matters because it explains why the first audit looked wrong. But that specific architectural holdout is no longer the current code state.

## Wider Migration Audit

I also re-checked the rest of the project for remaining session-shaped durable workflow behavior and instruction drift.

### Confirmed runtime holdouts addressed in this packet

- The YouTube tutorial webhook path is now admitted through `WorkflowAdmissionService` and creates durable `run_attempts`.
- The cron runner now admits execution through `WorkflowAdmissionService` and records durable run/attempt context.

The remaining session usages on these two paths are now either:

- true live provider-session concepts, or
- compatibility marker readers kept temporarily during migration.

### Confirmed instruction drift

The following sub-agent / skill guidance needed correction because it taught `CURRENT_SESSION_WORKSPACE` as the primary durable scratch location even after the run-workspace refactor:

- `research-specialist`
- `trend-specialist`
- `modular-research-report-expert`
- `gemini-url-context-scraper`
- `grok-x-trends`
- `banana-squad`
- `pdf`
- `reddit-intel`
- `logfire-eval`
- `nano-banana-pro`
- `graph-draw`

These instruction updates were applied so they now prefer `CURRENT_RUN_WORKSPACE` and treat `CURRENT_SESSION_WORKSPACE` as a legacy alias where compatibility still matters.

### Report writer status

The `report-writer` sub-agent was not the problematic example. Its current instruction file is already mostly neutral and tool-driven and does not hard-code `CURRENT_SESSION_WORKSPACE` as the durable output contract.
