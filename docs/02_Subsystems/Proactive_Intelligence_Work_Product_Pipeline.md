# Proactive Intelligence Work Product Pipeline

**Status:** Phase 1/2 foundation implemented; proactive task history/recap foundation in progress
**Last updated:** 2026-04-29
**Owner:** Kevin Dragan
**Related systems:** Task Hub, Proactive Pipeline, CSI, LLM Wiki, AgentMail, CODIE, ATLAS, tutorial pipeline, GWS MCP

## Product Directive

Universal Agent should be an abundant proactive work producer. Idle and overnight agent capacity should be used aggressively to create potentially useful work products without waiting for Kevin to ask.

The scarce resource is not agent effort. The scarce resource is Kevin's review attention and any side effect that changes the outside world.

Core rule:

> Work-product creation is abundant. Human interruption, external publication, production deployment, deletion, and irreversible side effects are governed.

This means agents may freely produce drafts, analyses, code repos, signal briefs, wiki entries, tutorial builds, experiments, cleanup branches, and review candidates. Rejection, silence, and ignored artifacts are normal outcomes and should feed learning rather than discourage future production.

## Decisions From Product Interview

1. Agents should work hard during idle time and generate many proactive artifacts. There is no product requirement to avoid "wasted" internal work.
2. Proactive outputs should become durable first-class records automatically, even when Kevin did not ask for them.
3. Generated artifacts are inventory, not obligations. They should not all become ordinary open Task Hub tasks.
4. Simone may send many proactive emails, but each email must clearly say that it is a review candidate.
5. Every proactive email must include the final work product or a durable link/attachment plus concise review framing.
6. CODIE may automatically create complete working repos, but they must be private by default.
7. CODIE should proactively improve the Universal Agent codebase and announce reviewable PRs. It must not merge, deploy, or push to production automatically.
8. CODIE may freely choose cleanup targets because rejected PRs are expected preference feedback.
9. Silence is not rejection. Non-response should barely move preference weights.
10. Preference learning should primarily tune surfacing and ranking, not suppress generation.
11. Implementation should proceed phase by phase. Finish, test, and operationally validate one phase before starting the next.

## Code-Verified Current State

The repository already contains several foundations this plan should reuse:

- Proactive signal cards are stored in SQLite by `src/universal_agent/proactive_signals.py`.
- Dashboard feedback already records feedback on proactive cards and can distill feedback into `docs/proactive_signals/generation_rules.md`.
- The current morning report in `src/universal_agent/services/proactive_advisor.py` is a deterministic heartbeat prompt snapshot, not the full proactive intelligence email product described here.
- AgentMail inbound already maps trusted emails into Task Hub through `src/universal_agent/services/email_task_bridge.py` with thread-level deduplication.
- GWS is already partially integrated through `src/universal_agent/services/gws_mcp_bridge.py` and `src/universal_agent/services/gws_event_listener.py`; do not build a parallel Google wrapper unless a specific gap remains.
- The tutorial pipeline already supports tutorial artifacts and repo bootstrap concepts. Feature work should enhance that lane rather than inventing a second pipeline.

## Target Architecture

Add a durable proactive artifact layer that sits beside Task Hub and proactive signal cards.

The artifact layer records every generated work product:

- source signal: CSI event, tutorial video, heartbeat reflection, CODIE cleanup mission, ATLAS research mission, Discord signal, manual seed
- artifact type: signal brief, convergence brief, wiki entry, tutorial build, private repo, CODIE PR, research report, infographic, audio overview, operational improvement
- lifecycle state: produced, candidate, surfaced, accepted, rejected, archived
- delivery state: not surfaced, digest queued, emailed, email failed, reviewed
- feedback state: score, text, tags, explicit action, silence aging
- durable locations: run workspace, artifact path, wiki URL/key, private GitHub repo URL, PR URL, dashboard URL, source URLs

Task Hub remains the execution system. The artifact registry is the inventory and learning system.

The Proactive Task History dashboard should read from Task Hub plus the artifact/recap stores. Its primary unit is the proactive work item, not only a surfaced artifact. Each work item should expose:

- lifecycle stage: opportunity, queued, running, completed, or needs attention
- source and scoring context
- the latest assignment/session lineage
- a three-panel session link for run log, transcript, and workspace audit
- durable work products and delivery evidence
- an evaluator recap describing the original idea, what was implemented, known issues, success assessment, and recommended next action

Implementation update as of 2026-04-29:

- `task_hub.list_proactive_work_tasks()` now returns proactive Task Hub items across queued/running/completed/needs-attention states, while excluding user-directed dashboard quick-add work unless it is explicitly marked as proactive.
- The `/api/v1/dashboard/proactive-task-history` endpoint now returns opportunities, lifecycle counts, artifacts, session links, delivery evidence, and recap data for each proactive work item.
- `proactive_work_recaps` stores durable evaluator recaps keyed by task id. The current implementation builds a session-evidence bundle from Task Hub metadata, latest assignment, session workspace, `transcript.md`, `run.log`, and `work_products/`, evaluates it with a high-capability LLM when enabled, and falls back to deterministic session-evidence evaluation if the model call fails.
- The terminal proactive outcome hook now writes both the outcome record and the recap record after complete/block/review/park/approve actions.
- The React Proactive Task History page now includes lifecycle filters, opportunity cards, evaluator recap blocks, artifact/evidence links, and a three-panel session opener.
- Feedback on proactive history tasks can now create a fresh `proactive_feedback_continuation` Task Hub item when Kevin explicitly asks for continuation or follow-up. The continuation item links back to the original task and carries prior workspace context for safe reuse.
- Proactive history rows now include continuation chain summaries, and ToDo execution prompts surface prior workspace, prior recap, and feedback context for continuation tasks.

Recommended state model:

| State | Meaning |
| --- | --- |
| produced | A work product exists. No Kevin action required. |
| candidate | The system believes it may be worth review. |
| surfaced | Simone emailed or digested it for Kevin. |
| accepted | Kevin replied positively, merged a PR, asked follow-up, or otherwise endorsed it. |
| rejected | Kevin explicitly rejected the artifact, topic, or approach. |
| archived | Retained for history but no longer active in review ranking. |

## Phase 1 - Artifact Inventory, Review Email, and Explicit Feedback

Goal: make proactive production durable and learnable using existing signal cards and tutorial artifacts.

Implementation status as of 2026-04-15:

- Implemented `src/universal_agent/services/proactive_artifacts.py` for durable artifact inventory, review delivery mapping, state transitions, and feedback storage.
- Implemented `src/universal_agent/services/proactive_feedback.py` for numbered/freeform feedback parsing and inbound reply handling.
- Implemented `src/universal_agent/services/proactive_preferences.py` for explicit-feedback preference signals and review ranking.
- Implemented `src/universal_agent/services/intelligence_reporter.py` for review-email and digest composition/sending via existing AgentMail service objects.
- Wired `EmailTaskBridge.materialize()` so replies to known proactive review threads are consumed as feedback and do not create normal Task Hub items.
- Wired `AgentMailService` so proactive feedback replies are not queued for normal email-handler execution.
- Added dashboard endpoints for listing artifacts, previewing/sending digest emails, sending individual review emails, and recording artifact feedback.

Scope:

1. Add a proactive artifact registry table in the runtime/activity DB.
2. Create a small service, for example `ProactiveArtifactRegistry`, for upsert/list/state transitions.
3. Create an email review reporter service that sends concise AgentMail review emails with final product links or attachments.
4. Add standard feedback footers to proactive review emails:
   - `1 useful`
   - `2 interesting but not now`
   - `3 not relevant`
   - `4 wrong direction`
   - `5 more like this`
   - freeform text allowed
5. Add an inbound feedback pre-filter before normal `EmailTaskBridge.materialize()` processing.
6. Feedback replies to proactive report threads must update the artifact/preference records and must not create new normal Task Hub items.
7. Store explicit feedback in both the artifact registry and the existing proactive signal feedback surface when the artifact originated from a proactive signal card.
8. Add a simple preference/ranking table in SQLite. Do not depend on the LLM Wiki internal vault for Phase 1.

Acceptance criteria:

- A proactive artifact record can be created for an existing signal card.
- Simone can email Kevin a review candidate with a durable artifact link or attachment.
- Replying `1` records positive feedback and does not create a new Task Hub item.
- Replying `3 wrong topic` records score and freeform feedback.
- Freeform-only replies are stored.
- No reply does not materially penalize the topic.
- The next candidate list is ranked using accumulated explicit feedback.

Tests:

- Unit tests for artifact registry schema idempotence and state transitions.
- Unit tests for feedback parser patterns.
- Integration test: inbound feedback reply is intercepted before email-to-task materialization.
- Integration test: non-feedback email still follows the existing email task bridge path.

## Phase 2 - Digest and Surfacing Policy

Goal: email can be frequent, but it must be clear, useful, and review-oriented.

Implementation status as of 2026-04-15:

- Implemented digest composition from proactive artifacts.
- Existing proactive signal cards sync into artifact inventory before digest ranking.
- Digest ranking uses explicit preference signals without suppressing generation.
- Dashboard digest preview endpoint is available; send endpoint uses the existing initialized AgentMail service.

Scope:

1. Build an intelligence digest composer that reads artifact inventory, proactive signal cards, tutorial notifications, CODIE PR artifacts, and wiki entries.
2. Send daily digest email when meaningful work was produced. **Status (2026-04-27):** Automated via `proactive_digest_agent.py` cron job at 8 AM CT daily. Previously, digest could only be sent through the dashboard API endpoint manually.
3. Send individual review emails for high-ranked candidates.
4. Do not impose a hard low email cap. Instead, use clear review framing and batch lower-ranked items.
5. Include source, why surfaced, final product, and exact review request in every email.

Email contract:

- Subject prefixes should identify review intent, for example `[Simone Review]`, `[UA Signal Review]`, `[UA Build Review]`, `[UA Digest]`, `[UA PR Review]`.
- First lines should say that Simone made the artifact proactively and wants review when convenient.
- Emails must never imply Kevin is obligated to act.
- Emails must include the final product or durable link/attachment.
- "No reply is fine" should be explicit for speculative artifacts.

Acceptance criteria:

- Daily digest includes new proactive artifacts and links to final products.
- Individual review email includes concise framing plus full artifact access.
- Email records map to artifact IDs for reply feedback.
- Email failures are retried or surfaced as artifact delivery failures without crashing generation.

## Phase 3 - CODIE Proactive PR Lane

Goal: let CODIE work freely on Universal Agent improvements while preserving review and deployment safety.

Implementation status as of 2026-04-15:

- Implemented `src/universal_agent/services/proactive_codie.py` for review-gated CODIE cleanup task creation and CODIE PR artifact registration.
- Added dashboard endpoints to queue proactive CODIE cleanup work and register resulting draft PRs as review artifacts.
- Cleanup tasks are ordinary Task Hub work items with explicit `code_change` workflow metadata and hard instructions not to merge, push to `main`, or deploy.
- Registered CODIE PRs become `codie_pr` proactive artifacts and are eligible for the same review email, digest, and feedback loop as other proactive work products.

Implementation update as of 2026-04-29:

- CODIE's nightly cleanup cron is now a deterministic script command (`!script universal_agent.scripts.codie_cleanup_enqueue`) rather than an LLM prompt that asks the cron agent to call Python helpers.
- Gateway startup auto-ensures the `codie_proactive_cleanup` cron job and upgrades the legacy `cron_codie_cleanup` job in place when present.
- Queued CODIE cleanup tasks now carry explicit `target_agent=vp.coder.primary`, `codebase_root=/home/kjdragan/lrepos/universal_agent`, `complexity_target=low_to_medium`, and `expected_work_product=pull_request_to_develop` metadata.
- CODIE cleanup briefs now require a low/medium complexity scope, prefer simplification over expansion, permit use of an installed Claude Code simplify/cleanup skill as a bounded helper, require red-green TDD evidence for behavior-touching changes, and keep a PR to `develop` as the required final work product unless no worthwhile improvement is found.

Scope:

1. Add a scheduled/idle CODIE cleanup mission generator.
2. CODIE may inspect the repo and choose its own cleanup/fix target.
3. CODIE delivers changes as draft PRs against `develop`.
4. CODIE uses red-green TDD for behavior-touching cleanup: write or update a focused failing regression first when practical, implement the smallest safe fix, then rerun the focused test to green.
5. Mechanical-only cleanup that cannot produce a meaningful failing regression must explain why red-green was not applicable and still include focused verification evidence.
6. Simone sends a `[UA PR Review]` email with PR link, rationale, risk, red-green evidence or exception rationale, tests run, and what she wants Kevin to review.
7. Merge and deployment remain manual/review-gated.
8. PR acceptance/rejection updates preference signals for future CODIE cleanup choices.

Constraints:

- No direct pushes to `main`.
- No production deployment.
- No silent merges.
- Follow the repo deployment contract: feature work targets `develop`; production is `main` fast-forward only after validation.

Acceptance criteria:

- CODIE can create a branch and draft PR for a small cleanup.
- Behavior-touching PRs include red-green TDD evidence in the PR body and review email.
- Mechanical-only PRs include a concise explanation for why red-green was not applicable plus focused verification output.
- Simone announces the PR by email as a review candidate.
- Rejected/closed PR feedback updates the preference model without disabling future proactive cleanup.

## Phase 4 - Tutorial Build Automation

Goal: build-oriented videos produce private working repos automatically.

Implementation status as of 2026-04-15:

- Implemented `src/universal_agent/services/proactive_tutorial_builds.py` for queueing CODIE tutorial-build tasks from video metadata/extraction plans.
- Tutorial build tasks carry explicit private-repo policy: private by default, no public publication without Kevin approval, local git artifact fallback if GitHub is unavailable.
- Added dashboard endpoints to queue tutorial build tasks and register completed private repos or local fallback artifacts.
- Completed tutorial builds become `tutorial_build` proactive artifacts eligible for review email, digest, and feedback.

Scope:

1. Reuse the existing YouTube tutorial pipeline and repo bootstrap path.
2. Add CSI auto-route for videos classified as build-oriented/tutorial-intensive.
3. Extract implementation plans from transcripts using structured LLM output.
4. Delegate private repo creation/build work to CODIE.
5. Create private GitHub repos automatically when credentials are available.
6. If GitHub access is unavailable, retain a complete local git repo artifact and email the fallback.
7. Send `[UA Build Review]` emails with repo link or artifact link, source video, run commands, and tests performed.

Acceptance criteria:

- Build-oriented CSI video can create a tutorial build artifact without playlist insertion.
- CODIE produces a runnable repo or a documented failure artifact.
- GitHub repos are private by default.
- Public publication requires later explicit approval.

## Phase 5 - Cross-Channel Convergence Detection

Goal: detect when independent creators cover the same topic and produce synthesis briefs.

Implementation status as of 2026-04-15:

- Implemented `src/universal_agent/services/proactive_convergence.py` for topic signature storage, no-embedding convergence matching, convergence event records, and ATLAS brief task creation.
- Added dashboard endpoint to upsert topic signatures and optionally detect/queue convergence in one call.
- Convergence brief requests become Task Hub work items plus `convergence_brief_task` proactive artifacts, so they enter the same review email/digest/feedback loop.
- Matching currently uses deterministic topic overlap with an injectable matcher seam for a future LLM-based matcher.

Scope:

1. Add topic signature extraction for CSI-ingested video transcripts.
2. Store topic signatures in CSI or runtime DB with source video/channel metadata.
3. Implement LLM-based matching first. Do not block on embeddings.
4. Detect clusters across independent channels within a configurable window.
5. Generate convergence briefs through ATLAS.
6. Register briefs as proactive artifacts and email review candidates.

Recommended defaults:

- window: 72 hours
- minimum independent channels: 2
- cooldown per topic cluster: 24 hours

Acceptance criteria:

- Two or more channels covering the same topic generate a convergence artifact.
- ATLAS produces a synthesis brief with consensus, divergence, and "why Kevin should care".
- Feedback updates topic/source preferences.

## Phase 6 - Preference Model Maturation

Goal: feedback changes ranking and surfacing without suppressing abundant generation.

Implementation status as of 2026-04-15:

- Not implemented beyond the Phase 1 explicit-feedback signal table and ranking helper.
- No decay job exists yet.
- Preference context is not yet injected into ATLAS/CODIE mission briefs.
- Weekly preference report is not yet implemented.

Scope:

1. Implement explicit signal weights:
   - positive reply, merge, follow-up: strong positive
   - explicit rejection or PR close with reason: strong negative
   - dashboard archive/delete: mild negative
   - silence: neutral or extremely weak negative
2. Add time decay with a half-life around 14 days.
3. Track topic, source/channel, format, artifact type, depth, and delivery preferences.
4. Inject preference context into ATLAS and CODIE mission briefs.
5. Generate a weekly preference report.

Important rule:

Preferences primarily tune ranking and surfacing. They should only suppress generation after repeated explicit negative feedback.

Acceptance criteria:

- Preference scores alter digest ordering.
- Delegation prompts include compact preference context.
- Weekly report summarizes learned interests and invites correction.
- Silence does not materially punish a topic.

## Phase 7 - GWS Enhancements

Goal: use GWS as an enhancement layer without making it a prerequisite for proactive intelligence.

Implementation status as of 2026-04-15:

- Not implemented in this feature branch beyond reusing the already-existing GWS MCP/event-listener scaffolding.
- Digest emails do not yet include calendar context.
- Review-block scheduling and Drive archival remain planned enhancements.

Scope:

1. Reuse existing GWS MCP bridge and event listener work.
2. Verify `gws` availability and auth health through existing ops/status patterns.
3. Add calendar context to digest when available.
4. Optionally schedule review blocks for high-value artifacts.
5. Optionally archive selected artifacts to Drive.

Constraints:

- Features 1-6 must work if GWS is unavailable.
- GWS auth failure should be reported, not fatal.
- Do not create a separate direct Google API wrapper unless the MCP/CLI path cannot support a specific required operation.

## Implementation Notes

Recommended new module boundaries:

- `src/universal_agent/services/proactive_artifacts.py` - registry schema and state transitions
- `src/universal_agent/services/proactive_feedback.py` - feedback parser and preference update helpers
- `src/universal_agent/services/intelligence_reporter.py` - digest and review email composition
- `src/universal_agent/services/proactive_preferences.py` - preference model and ranking

Prefer wiring into existing systems:

- AgentMail outbound: use the existing AgentMail service/tooling. Do not create a new email sender.
- AgentMail inbound: intercept report feedback before `EmailTaskBridge.materialize()`.
- Task Hub: use it for execution and follow-up tasks, not as the only artifact inventory.
- Tutorial pipeline: enhance the current pipeline and bootstrap job model.
- GWS: reuse the MCP bridge and event listener.

## Rollout Gates

Each phase must be implemented, tested, and observed before the next starts.

Minimum verification per phase:

1. Unit tests for new parsers/schema/ranking logic.
2. Integration tests for ingress/egress paths.
3. Manual smoke test in local runtime.
4. No regressions in existing Task Hub, AgentMail, and tutorial pipeline tests touched by the phase.
5. Documentation update if behavior changes.

## Initial Implementation Order

Start with Phase 1. Do not start with convergence detection or tutorial build expansion. The feedback loop is the keystone that makes all later proactive generation learnable.

Phase 1 should be considered complete only when Kevin can receive a proactive review email, reply with feedback, and see that feedback stored without generating a normal inbound Task Hub task.

## Current Completion Snapshot

Completed foundations:

1. Proactive artifact inventory schema and service.
2. Review email and digest composition/sending through existing AgentMail service objects.
3. Feedback reply interception before normal `EmailTaskBridge` Task Hub materialization.
4. SQLite preference signals from explicit artifact feedback.
5. Dashboard endpoints for artifact listing, digest preview/send, individual review send, and feedback.
6. Existing proactive signal cards sync into artifact inventory.
7. CODIE cleanup work can be queued as review-gated Task Hub work.
8. CODIE draft PRs can be registered as review artifacts.
9. Tutorial build work can be queued as private-repo CODIE work.
10. Completed tutorial private repos/local fallbacks can be registered as review artifacts.
11. Topic signatures can be stored and deterministic cross-channel convergence can queue ATLAS brief tasks.

Outstanding product work:

1. Live automation wiring from CSI/tutoral/proactive runtime events into the new services.
2. LLM-based topic signature extraction and matching.
3. Preference decay and stronger preference model snapshots.
4. Preference context injection into ATLAS/CODIE mission briefs.
5. Weekly preference report.
6. GWS calendar context in digest.
7. Optional review-block scheduling and Drive archival.
8. Live smoke tests for AgentMail delivery, inbound feedback reply handling, CODIE PR workflow, private GitHub repo creation, and CSI post-ingest convergence.

## Detailed Next Build Plan

### Packet 1 - Runtime Hook Wiring

Goal: connect existing producers to the proactive artifact services so artifacts appear without manual dashboard API calls.

Implementation status as of 2026-04-15:

- `proactive_signals.upsert_generated_card()` now immediately syncs signal cards into proactive artifact inventory.
- Tutorial bootstrap completion paths register completed repo/local artifact outputs as tutorial build artifacts.
- CODIE VP worker completion path detects GitHub PR URLs in completed coder mission output and registers PR artifacts.
- CSI proactive signal sync now also creates topic signatures, queues convergence brief tasks when independent channels converge, and auto-routes build-oriented RSS videos into private tutorial build tasks.
- Remaining hook work: direct in-process CSI Ingester post-enrichment hook and live validation against production CSI data are still pending.

Work items:

1. Wire proactive signal sync into the relevant heartbeat/report cycle if digest generation is enabled.
2. Identify the existing CSI post-ingest or UA CSI event handling path and call `upsert_topic_signature()` after transcript analysis metadata exists.
3. Identify tutorial pipeline completion events and call `register_tutorial_build_artifact()` when a repo bootstrap succeeds or local fallback artifact exists.
4. Identify CODIE/VP mission completion metadata that includes PR URLs and call `register_pr_artifact()`.
5. Ensure each hook is idempotent: same source event should update an existing artifact, not create duplicates.

Likely files to inspect first:

- `src/universal_agent/gateway_server.py`
- `src/universal_agent/proactive_signals.py`
- `src/universal_agent/services/youtube_playlist_watcher.py`
- `src/universal_agent/hooks_service.py`
- `src/universal_agent/vp/worker_loop.py`
- `src/universal_agent/durable/state.py`
- `src/universal_agent/services/intelligence_reporter.py`

Acceptance criteria:

- Existing proactive signal cards appear in proactive artifact inventory without manual sync calls.
- A tutorial-ready notification or bootstrap-ready event can create/update a tutorial build artifact.
- A PR URL surfaced from CODIE/VP metadata can create/update a `codie_pr` artifact.
- Re-running the same event does not duplicate artifacts.

Verification:

```bash
uv run pytest tests/unit/test_proactive_intelligence_phase1.py \
  tests/gateway/test_proactive_artifacts_endpoint.py \
  tests/gateway/test_proactive_signals_endpoint.py -q
```

### Packet 2 - LLM Topic Signature Extraction and Matching

Goal: replace the current deterministic convergence foundation with LLM-assisted topic judgment while preserving deterministic fallback.

Implementation status as of 2026-04-15:

- Added LLM topic signature extraction from title/summary/transcript with strict JSON output and deterministic fallback.
- Added LLM-assisted convergence matching with deterministic overlap fallback.
- Added dashboard endpoint for extraction + optional convergence detection in one call.
- CSI sync path now populates topic signatures from existing `rss_event_analysis`; remaining work is live CSI Ingester post-enrichment invocation.

Work items:

1. Add a function that extracts a topic signature from transcript/title/summary using a cheap model and strict JSON output.
2. Add a matcher function that takes one new signature and recent candidate signatures and returns matched video IDs with a short reason.
3. Keep deterministic overlap matcher as fallback when LLM fails.
4. Add config/env controls for enabling LLM matching, window hours, min channels, cooldown hours, and model name.
5. Store LLM extraction/match reasons in metadata for auditability.

Likely files:

- `src/universal_agent/services/proactive_convergence.py`
- `src/universal_agent/services/llm_classifier.py` or a new focused helper under `services/`
- `src/universal_agent/utils/model_resolution.py`

Acceptance criteria:

- Given a transcript summary, the system stores primary topics, secondary topics, key claims, and content type.
- Given two semantically similar but lexically different topic signatures, LLM matching can detect convergence.
- If the LLM call fails or returns bad JSON, deterministic fallback still works.

Verification:

```bash
uv run pytest tests/unit/test_proactive_convergence.py -q
```

### Packet 3 - Preference Maturation

Goal: make feedback shape ranking, mission context, and weekly summaries without suppressing generation.

Implementation status as of 2026-04-15:

- Added SQLite preference model snapshots derived from explicit feedback signals with exponential decay.
- Added compact delegation context generation.
- Proactive CODIE cleanup, tutorial build, and convergence brief task creation now inject available preference context into their task briefs.
- Generic VP dispatch via internal VP tools and ops dispatch now appends preference context when available, unless `constraints.skip_preference_context` is set.
- Added weekly preference report artifact/email composition and dashboard preview/send endpoints.
- Remaining work: richer dimensions/source preferences and live validation of preference context inside full ATLAS/CODIE missions.

Work items:

1. Add a preference model snapshot table or singleton row derived from `proactive_preference_signals`.
2. Implement time decay with a 14-day half-life.
3. Track dimensions: topic, source, artifact type, format, CODIE cleanup theme, tutorial source channel.
4. Add `get_delegation_context(task_type, topic_tags)` returning compact natural language for ATLAS/CODIE prompts.
5. Wire preference context into the ToDo/VP mission prompt path where delegated missions are created.
6. Add a weekly preference report artifact and optional email digest section.

Likely files:

- `src/universal_agent/services/proactive_preferences.py`
- `src/universal_agent/services/todo_dispatch_service.py`
- `src/universal_agent/heartbeat_service.py`
- `src/universal_agent/tools/vp_orchestration.py`
- `src/universal_agent/services/intelligence_reporter.py`

Acceptance criteria:

- Positive explicit feedback raises future ranking for matching topics/types.
- Negative explicit feedback lowers ranking for matching topics/types.
- Silence creates no meaningful penalty.
- Delegation prompts can include a compact preference block.
- Weekly preference report summarizes rising/declining interests.

Verification:

```bash
uv run pytest tests/unit/test_proactive_intelligence_phase1.py \
  tests/unit/test_proactive_codie.py \
  tests/unit/test_proactive_tutorial_builds.py \
  tests/unit/test_proactive_convergence.py -q
```

### Packet 4 - Live Delivery and Feedback Smoke Tests

Goal: verify real AgentMail delivery and inbound feedback loop against a controlled test artifact.

Work items:

1. Create a test artifact in the runtime DB.
2. Send a review email through the dashboard endpoint or service using Simone's initialized AgentMail service.
3. Confirm `proactive_artifact_emails` has message/thread identifiers.
4. Reply with `1 useful` from Kevin's Gmail/AgentMail route.
5. Confirm the inbound path records proactive feedback and does not create a normal `email:` Task Hub item.

Acceptance criteria:

- Kevin receives the review email.
- The reply updates artifact feedback and preference signals.
- No duplicate or unrelated Task Hub task is created.
- The artifact is marked reviewed/accepted for score `1` or `5`.

Suggested focused verification after manual smoke:

```bash
uv run pytest tests/unit/test_agentmail_official_mcp.py \
  tests/unit/test_agentmail_send_policy.py \
  tests/unit/test_agentmail_service.py \
  tests/unit/test_proactive_intelligence_phase1.py -q
```

### Packet 5 - GWS Digest Enhancements

Goal: enrich digests with calendar context without making GWS a hard dependency.

Implementation status as of 2026-04-15:

- Digest composition can now include injected calendar events.
- Tests cover digest calendar rendering without shelling out to `gws`.
- Added optional GWS calendar context provider for today's events; GWS absence/auth/CLI failures are nonfatal and leave digest sending unblocked.
- Dashboard digest preview/send can request calendar context with `include_calendar`.
- Remaining work: live GWS auth smoke test and optional review-block scheduling/Drive archival.

Work items:

1. Reuse existing GWS MCP/event listener status checks to determine whether calendar context is available.
2. Add optional calendar section to `IntelligenceReporter.compose_daily_digest()`.
3. If GWS is unavailable or auth fails, include a non-fatal note only when useful; do not block digest sending.
4. Add tests with injected calendar provider rather than shelling out to `gws`.

Likely files:

- `src/universal_agent/services/intelligence_reporter.py`
- `src/universal_agent/services/gws_mcp_bridge.py`
- `src/universal_agent/services/gws_event_listener.py`

Acceptance criteria:

- Digest includes today's calendar context when provider returns events.
- Digest still sends when provider fails.
- No new direct Google API wrapper is introduced unless MCP/CLI cannot support the operation.

### Packet 6 - PR and Private Repo Live Operations

Goal: complete live operational validation for CODIE PRs and tutorial private repos.

Validation status as of 2026-04-15:

- GitHub CLI is authenticated locally as `Kjdragan` with `repo` scope and ADMIN access to `Kjdragan/universal_agent`.
- `gws` is not installed/available on this shell PATH, so live GWS calendar smoke tests must run in the deployed/runtime environment after GWS install/auth is confirmed.
- AgentMail environment variables are not present in this shell (`AGENTMAIL_API_KEY`, inbox id/address, and UA AgentMail enablement are unset), so live AgentMail send/reply smoke tests must run through the initialized deployed gateway service or an Infisical-backed runtime.
- No public repos, PR merges, production deploys, or real email sends were performed during this validation pass.

Work items:

1. Verify GitHub credentials through the existing GitHub app/CLI path, not ad hoc token probing in code.
2. Run a small CODIE cleanup task and ensure it creates a draft PR against `develop`.
3. Verify behavior-touching CODIE cleanup PRs include red-green TDD evidence, or mechanical-only PRs explain why red-green was not applicable.
4. Register that PR as a proactive artifact and send a `[UA PR Review]` email.
5. Run a controlled tutorial build task and ensure private repo creation or local fallback.
6. Register the result as a `tutorial_build` artifact and send a `[UA Build Review]` email.

Acceptance criteria:

- No public repos are created automatically.
- No merge/deploy happens automatically.
- Review emails include final product links and concise review framing.
- Kevin can accept/reject by reply and the preference system records it.

### Packet 7 - Final Documentation and Release Readiness

Goal: make the feature maintainable and ready for normal review.

Implementation status as of 2026-04-15:

- `docs/04_API_Reference/Ops_API.md` documents the proactive artifact endpoint surface.
- This subsystem plan documents implemented foundations, remaining live smoke tests, and safety boundaries.
- Remaining work: run live smoke tests in the deployed environment, then update this section with actual message IDs, PR URLs, private repo URLs/local artifact paths, and GWS status.

Work items:

1. Update this document's status table with exact implemented surfaces and remaining gaps.
2. Update `docs/02_Subsystems/Proactive_Pipeline.md` if runtime behavior changed.
3. Update API docs if proactive artifact endpoints become supported dashboard APIs.
4. Add an operator runbook section for manual smoke tests.
5. Run the broad focused regression suite.

Recommended verification:

```bash
uv run pytest tests/unit/test_proactive_convergence.py \
  tests/unit/test_proactive_tutorial_builds.py \
  tests/unit/test_proactive_codie.py \
  tests/unit/test_proactive_intelligence_phase1.py \
  tests/gateway/test_proactive_artifacts_endpoint.py \
  tests/gateway/test_proactive_signals_endpoint.py \
  tests/unit/test_proactive_signals.py \
  tests/unit/test_proactive_advisor.py \
  tests/unit/test_agentmail_ingress_tasks.py \
  tests/unit/test_agentmail_service.py \
  tests/unit/test_agentmail_official_mcp.py \
  tests/unit/test_agentmail_send_policy.py \
  tests/unit/test_task_hub_lifecycle.py -q
```
