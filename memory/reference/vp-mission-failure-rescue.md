# Handling `vp_mission_failure` items (rescue-evaluator posture)

> Moved verbatim from `memory/HEARTBEAT.md` (R4 context diet, 2026-07-18). Read this
> whenever you claim a task with `source_kind="vp_mission_failure"`.

When you claim a task with `source_kind="vp_mission_failure"`, you are operating in the **rescue-evaluator** posture (one of your four context-dependent roles — see also "observer" for VP successes, "full executor" for `chat_panel`/`simone_chat`, and "router" for everything else). You are NOT a fallback executor. Your job is to evaluate the failure and pick a rescue verb.

1. Read `metadata.brief_path` (the VP's own BRIEF.md from self-briefing, if produced — may be `None` for older missions), `metadata.transcript_tail` (last 2 KB of subprocess output), `metadata.failure_mode` (one of: `vp_self_reported`, `goal_cap_hit`, `subprocess_crash`, `auth_failure`, `workspace_guard`, `timeout`, `operator_cancel`, `missing_completion_attestation`, `unspecified`), and `metadata.failure_count` (count of failures in this rescue chain; high counts mean prior rescue attempts didn't stick).
2. Choose **exactly one** of four actions:

   | Verb | When to use |
   |---|---|
   | `vp_dispatch_mission_retry(mission_id, additional_guidance, max_additional_turns=None)` | Failure was self-reported or `/goal` cap-hit AND your guidance addresses the gap. Same chain, same brief, additional guidance prepended. |
   | `vp_dispatch_mission_redispatch_fresh(mission_id, additional_context)` | Failure was a crash, env corruption, workspace contamination — situations where prior state might be the problem. Same chain, fresh workspace. |
   | `escalate_vp_failure_to_operator(mission_id, summary, why_escalating, recommended_action=None)` | Failure is auth (`auth_failure`) / workspace-guard (`workspace_guard`) / config-shaped (Simone can't fix) OR `failure_count >= 3` OR you choose not to retry. Creates a `chat_panel` task to Kevin. |
   | `task_hub_task_action(task_id="vp_failure:<mission_id>", action="complete", note="ambient — failure_count=N, no action this cycle")` | You're choosing not to act this cycle. Failure becomes context for next occurrence; `failure_count` will tell future-you whether to escalate. |

3. **Do NOT attempt to fix the VP's underlying work yourself.** You are an evaluator and dispatcher in this posture, not a fallback executor. If the work needs a human (operator), escalate. If it needs a fresh agent attempt, retry/redispatch.

4. **The rescue verbs apply ONLY to the `vp_failure:<mission_id>` item — NEVER `complete` the SOURCE task.** The source task's lifecycle belongs to the VP worker's terminal sync (attestation guard + demo finalize), not to you. Specifically for `failure_mode="missing_completion_attestation"` where the transcript shows the work was actually done: the correct verb is `vp_dispatch_mission_retry(mission_id, additional_guidance="The build is done — write COMPLETION.md per the self-brief-and-attest Phase 5 attestation protocol, nothing else")` — a cheap retry that re-enters the normal completion path and populates the finalize evidence deterministically. Completing the source task directly skips manifest synthesis, mechanical checks, and dashboard registration. (Code now enforces this: a non-operator `complete` on a `tutorial_build`/`cody_demo_task` row without worker-finalize evidence routes to `needs_review` with `completion_requires_demo_finalize` — incident 2026-06-11, task `tutorial-build:f08d721d27eaaea4`.)
