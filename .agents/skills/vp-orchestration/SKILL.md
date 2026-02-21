---
name: vp-orchestration
description: "Operate external primary VP agents through tool-first mission control (`vp_*` tools) with deterministic lifecycle handling and artifact handoff."
user-invocable: true
risk: medium
---

# VP Orchestration Skill

Use this skill whenever work should be delegated to external primary VP runtimes (for example `vp.general.primary` or `vp.coder.primary`).

## Rules
- Use internal `vp_*` tools as the only control plane.
- Do not call VP HTTP endpoints via shell/curl.
- Keep orchestration deterministic: dispatch, observe, wait, then hand off artifacts.

## Dispatch Criteria
- Choose `vp.coder.primary` for coding/build/refactor tasks in external project paths.
- Choose `vp.general.primary` for research/analysis/content tasks not requiring repository edits.
- Include constraints and budget when known.
- Provide an idempotency key for replay-safe dispatch when a stable request identity exists.

## Standard Flow
1. Call `vp_dispatch_mission` with `vp_id`, objective, mission type, constraints, budget, and priority.
2. Immediately report mission id and queued status.
3. For short tasks, call `vp_wait_mission` with bounded timeout.
4. On terminal status, call `vp_get_mission` for final state and failure detail.
5. If mission completed with workspace output, call `vp_read_result_artifacts` and summarize key files.

## Poll/Wait Policy
- Prefer `vp_wait_mission` over manual polling loops.
- Use short poll intervals (2-5 seconds) and explicit timeout bounds.
- If timeout is reached, surface current state and next checkpoint time.

## Failure and Recovery
- If dispatch returns retryable lock contention, retry after short delay.
- If mission fails, report failure detail from mission events before proposing a retry.
- If mission is no longer needed, call `vp_cancel_mission` with reason.

## Artifact Handoff
- Treat `result_ref=workspace://...` as the authoritative artifact location.
- Use `vp_read_result_artifacts` for concise artifact index + excerpts.
- Summarize what was produced, where it lives, and what remains.

## CODIE Guardrails
- Do not target UA internal repository/runtime paths for CODIE missions.
- Use allowlisted external handoff/workspace paths for CODIE code execution.
