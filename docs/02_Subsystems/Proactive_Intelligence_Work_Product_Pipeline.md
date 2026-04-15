# Proactive Intelligence Work Product Pipeline

**Status:** Phase 1/2 foundation implemented; later automation phases planned  
**Last updated:** 2026-04-15  
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
2. Send daily digest email when meaningful work was produced.
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

Scope:

1. Add a scheduled/idle CODIE cleanup mission generator.
2. CODIE may inspect the repo and choose its own cleanup/fix target.
3. CODIE delivers changes as draft PRs against `develop`.
4. Simone sends a `[UA PR Review]` email with PR link, rationale, risk, tests run, and what she wants Kevin to review.
5. Merge and deployment remain manual/review-gated.
6. PR acceptance/rejection updates preference signals for future CODIE cleanup choices.

Constraints:

- No direct pushes to `main`.
- No production deployment.
- No silent merges.
- Follow the repo deployment contract: feature work targets `develop`; production is `main` fast-forward only after validation.

Acceptance criteria:

- CODIE can create a branch and draft PR for a small cleanup.
- Simone announces the PR by email as a review candidate.
- Rejected/closed PR feedback updates the preference model without disabling future proactive cleanup.

## Phase 4 - Tutorial Build Automation

Goal: build-oriented videos produce private working repos automatically.

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
