# YouTube Demo Unification — Next-Phase Plan

> **Status:** 🟡 Deferred until ClaudeDevs Intel v2 has been live-verified for a meaningful period (operator preference, 2026-05-16). Tracked here so the idea isn't lost.
> **Owner:** AI Coder, with operator gates on Phase boundaries.
> **Companion:** [`claudedevs_intel_v2_remaining_work.md`](claudedevs_intel_v2_remaining_work.md) (the prior plan whose Phase B established the demo workspace pattern this work generalizes).
> **Created:** 2026-05-16 (post gateway-wedge incident + Phase C cleanup).

## Why this doc exists

The ClaudeDevs Intel v2 effort built a sophisticated multi-skill demo loop:

```
cody-scaffold-builder
  → cody-task-dispatcher (Task Hub claim/route)
    → cody-implements-from-brief (Cody builds in /opt/ua_demos/<id>/)
      → cody-progress-monitor (Simone tracks)
        → cody-work-evaluator (Simone scores against ACCEPTANCE.md)
          → vault-demo-attach (on pass, link to entity page)
```

In parallel, the project has a separate `youtube-tutorial-creation` skill that processes YouTube videos into durable learning artifacts (`CONCEPT.md`, `IMPLEMENTATION.md`, runnable `implementation/` repos). The two systems were built independently for different content sources but share substantial overlap in what they're trying to do — turn external content into durable, runnable demos with provenance.

The operator question that triggered this plan (2026-05-16): _"Since we've spent a lot of work on the Anthropic demo part, should we use that functionality for our general YouTube demo creation? It has nothing to do with Anthropic — it's just about creating demos for any YouTube videos that would benefit from that approach."_

**Recommendation: yes, but as a complementary path, not a replacement.** The CSI demo pattern's runnable-demo half (workspace + manifest + evaluation + iteration + vault-attach) is content-agnostic and would substantially improve YouTube demos. But the YouTube skill's `CONCEPT.md` (standalone tutorial doc readable without watching the video) is a genuinely different artifact worth preserving.

## Current state — two parallel systems

| Dimension | `youtube-tutorial-creation` skill | CSI demo workspace flow |
|---|---|---|
| Trigger | Operator provides URL, or webhook with YouTube URL | Task Hub `cody_scaffold_request` row (from CSI tier-3 demo-triage approval) |
| Producer | `youtube-transcript-metadata` skill → tutorial creation skill | `claude_code_intel.queue_follow_up_tasks` → triage drawer → operator approval |
| Output structure | `CONCEPT.md` + `IMPLEMENTATION.md` + `manifest.json` + `visuals/` + `research/` + `implementation/` (when `learning_mode=concept_plus_implementation`) | `BRIEF.md` + `ACCEPTANCE.md` + `business_relevance.md` + `manifest.json` + `BUILD_NOTES.md` + `run_output.txt` |
| Storage | `<artifacts_root>/youtube-tutorial-creation/{YYYY-MM-DD}/{video-slug}__{HHMMSS}/` | `/opt/ua_demos/<entity-slug>__<demo-id>/` with vanilla `.claude/settings.json` |
| Evaluation | None — one-shot output | `cody-work-evaluator` runs `evaluate_demo()` → `EvaluationReport` → pass/iterate/defer verdict |
| Iteration | None | Multi-round via `write_feedback_file()` + `reissue_cody_demo_task_with_feedback()` (cap 5 iterations) |
| Vault linkage | None — flat directory | `## Demos` section appended to vault entity page via `attach_demo_to_vault_entity()` |
| Endpoint discipline | None | `manifest.endpoint_hit == endpoint_required` verification in evaluator |
| Dispatcher | Ad-hoc / hook-triggered | Task Hub claim → `route_all_to_simone` → Cody |

## Why the CSI pattern is the right framework for YouTube demos

1. **The workspace primitives are content-agnostic.** `provision_demo_workspace`, vanilla `.claude/settings.json`, the manifest discipline, the BRIEF/ACCEPTANCE pair — none of this is Anthropic-specific. The Anthropic-ness lives entirely in `endpoint_profile=anthropic_native`. Adding `endpoint_profile=none` (or a new `youtube_implementation` profile) lets the same scaffolding work for any subject matter.

2. **The evaluation/iteration loop is genuinely valuable** for YouTube demos. Today, when `youtube-tutorial-creation` produces an `implementation/`, you get whatever Cody happened to write first try. With the CSI loop you get:
   - **ACCEPTANCE.md** describing what "good" looks like (extracted from the transcript's stated goals + visual analysis)
   - **`cody-work-evaluator`** scoring the artifact (does it actually run? does the output match ACCEPTANCE's claims?)
   - **Multi-round iteration** with FEEDBACK.md when first-shot output is wrong
   - **Audit trail** in manifest.json (versions, model used, endpoint hit, run output)

3. **Vault attachment closes the semantic loop.** Successful YouTube demos could attach to a knowledge entity page describing the *technique* (e.g., "RAG with PostgreSQL", "LangGraph agents", "FastAPI dependency injection"). That makes them discoverable later as proven, runnable examples of a technique. Right now YouTube outputs live in a flat artifacts directory with no semantic linkage to the broader knowledge base.

4. **Cody-as-builder is content-agnostic.** `cody-implements-from-brief` (`.claude/skills/cody-implements-from-brief/SKILL.md`) doesn't care where the brief came from. It reads BRIEF.md/ACCEPTANCE.md and builds. The producer (CSI vs YouTube vs operator-direct) doesn't matter to the builder.

5. **One demo system to maintain beats two.** The current YouTube `implementation/` schema and the CSI demo manifest schema will drift over time. Each fix to one needs porting to the other. Each new evaluation metric, each new manifest field, each new safety check — all double work. Consolidating gives one place to fix bugs and add features.

## Why NOT to retire `youtube-tutorial-creation` entirely

The YouTube skill produces something the CSI flow doesn't: **CONCEPT.md** — a standalone tutorial doc readable *without watching the video*. That's a genuinely different artifact from a runnable demo, and it has value on its own:

- Retrieval input for research grounding (PR 16's mechanism)
- Briefing input (operator's daily intel)
- Source for entity/concept page creation in the vault

So the YouTube skill stays in the picture — it just stops being responsible for the runnable-demo half.

## Proposed architecture

Keep both, with cleaner boundaries:

```
YouTube URL arrives
  → youtube-transcript-metadata (transcript + metadata fetch)
  → youtube-tutorial-creation
      → produces CONCEPT.md + README.md + visuals/ + research/
      → produces (or matches) an entity/concept page in the appropriate vault
      → IF learning_mode == "concept_plus_implementation":
          → enqueues youtube_demo_request Task Hub row
              with metadata.brief_source = path to CONCEPT.md
              with metadata.entity_slug = the resolved entity
              with metadata.endpoint_profile = "none" (default for non-Anthropic)
                or "anthropic_native" (if CONCEPT.md indicates Claude-related)
                or "gemini_native" / "openai_native" (future)
  → Simone claims youtube_demo_request
      → reads CONCEPT.md → drafts BRIEF.md, ACCEPTANCE.md, business_relevance.md
      → provisions /opt/ua_demos/<entity-slug>__yt-<video-id>/
          with the endpoint_profile from metadata
          with SOURCES/ pre-populated with CONCEPT.md + IMPLEMENTATION.md
      → dispatches cody_demo_task (same source_kind as CSI demos)
  → Cody builds via cody-implements-from-brief (no changes)
  → Simone evaluates via cody-work-evaluator (no changes)
  → On pass: vault-demo-attach links the workspace to its entity page (no changes)
```

## What needs to change

### PR 14a — Generalize `provision_demo_workspace`

**Need:** Today `provision_demo_workspace` (in `src/universal_agent/services/cody_scaffold.py` or adjacent) hardcodes Anthropic-SDK scaffolding. Generalize to accept `endpoint_profile`.

**Scope:**
- Add `endpoint_profile` parameter with allowed values: `anthropic_native | gemini_native | openai_native | none`.
- For `none`, provision the workspace skeleton (vanilla `.claude/settings.json`, manifest skeleton, BRIEF/ACCEPTANCE template) without any SDK-specific env or scaffolding.
- For non-Anthropic profiles, set the appropriate SDK env vars and provision a minimal SDK-aware scaffold.
- Backward-compat default: `endpoint_profile=anthropic_native` so existing CSI flow is unchanged.

**Risk:** Low. Pure additive change with safe defaults.

**Tests:** Unit tests asserting workspace contents per profile.

**Estimate:** ~80 lines + tests.

### PR 14b — Add `youtube_demo_request` source_kind + Task Hub plumbing

**Need:** YouTube tutorial creation needs to enqueue a request that flows through Simone→Cody just like CSI's `cody_scaffold_request`.

**Scope:**
- New constant `SOURCE_KIND_YOUTUBE_DEMO_REQUEST = "youtube_demo_request"`.
- New helper analogous to `_build_followup_task_payload` that builds a Task Hub upsert for YouTube-sourced demos (`build_youtube_demo_request_payload`).
- Routing in `dispatch_service.route_all_to_simone` already covers any task whose `preferred_vp == "simone_direct"` — the new payload sets that.
- Heartbeat directive in `memory/HEARTBEAT.md`: add a section parallel to the existing `CSI demo-triage approvals → Phase 2 scaffold` directive but for `youtube_demo_request` rows.

**Risk:** Low-medium. New source_kind but reuses every downstream component.

**Tests:** Mocked Task Hub claim; assert routing to Simone; assert downstream payload shape.

**Estimate:** ~150 lines + tests.

### PR 14c — Producer wiring in `youtube-tutorial-creation`

**Need:** After the YouTube tutorial skill writes its artifacts, it should optionally enqueue a `youtube_demo_request`.

**Scope:**
- New module-level helper `enqueue_youtube_demo_request(conn, *, video_slug, concept_md_path, learning_mode, endpoint_profile_hint)`.
- The `youtube-tutorial-creation` skill SKILL.md is updated to call this helper when `learning_mode=concept_plus_implementation` is in effect.
- `youtube_daily_digest` (the scheduled digest cron) is updated to optionally trigger this for tutorials it processes.
- Entity-page reuse: if the transcript topic matches an existing entity in any vault (e.g., "FastAPI", "RAG", "LangGraph"), reuse it. Otherwise create a new entity page in a `youtube-tutorials` vault (new, parallel to `claude-code-intelligence`).

**Risk:** Medium. The entity-matching heuristic across multiple vaults is the substantive new logic.

**Tests:** Synthetic transcripts; assert correct entity resolution; assert request enqueued only when learning_mode warrants.

**Estimate:** ~250 lines + tests.

### PR 14d — Optional: `youtube-tutorials` vault scaffolding

**Need:** If most YouTube tutorials don't map to existing CSI vault entities, they need their own vault.

**Scope:**
- Add a `youtube-tutorials` lane to `config/intel_lanes.yaml`.
- Provision `artifacts/knowledge-vaults/youtube-tutorials/` on first use.
- The PR 17 lane-aware refactor (already shipped 2026-05-16) makes the rest of the plumbing automatic.

**Risk:** Low — additive config.

**Tests:** Lane loader test; vault scaffolding test.

**Estimate:** ~80 lines + tests.

### Total scope

| PR | Estimate | Risk |
|---|---|---|
| PR 14a | ~80 lines + tests | Low |
| PR 14b | ~150 lines + tests | Low-medium |
| PR 14c | ~250 lines + tests | Medium |
| PR 14d | ~80 lines + tests | Low |
| **Total** | **~560 lines + tests** | **Medium overall** |

## Trigger conditions for starting this work

Don't start until:

1. **CSI Intel v2 has run live for a meaningful period.** Operator preference (2026-05-16): "test out the CSI intelligence for a bit and once we're comfortable with it and it's nailed down." Concrete check: a minimum of 5 organic tier-3 demo-triage approvals have flowed end-to-end (scaffold → build → evaluate → attach) without operator intervention. Currently the system has run this path 3 times historically; need ~5+ more in the post-gateway-fix era to feel confident.

2. **The YouTube tutorial pain is real, not theoretical.** Concrete check: at least one occasion where the operator has wished the YouTube tutorial's `implementation/` had gone through an evaluation/iteration loop. If this doesn't materialize, the unification is unjustified.

3. **No Phase C carry-over.** PRs 13 + 17 are landing in PR #304. PR 6c is already shipped. PR 12 (backfill replay) is operator-parked. No other Phase C work should be in flight when starting this.

## Migration safety considerations

- **Don't break the existing YouTube path.** Until PR 14c is operator-verified, both the old and new paths should coexist. Gate via `UA_YOUTUBE_DEMO_REQUEST_ENABLED=0` by default during the testing window.
- **Don't fill up `/opt/ua_demos/`.** Extend the existing `_ensure_vp_coder_workspace_pruning_cron_job` cron (gateway_server.py) to cover YouTube-derived workspaces by glob pattern.
- **Entity-matching false positives.** PR 14c's "match an existing entity from a YouTube transcript" heuristic could attach demos to the wrong entity. Mitigate via LLM-judged matching (similar to CSI's existing tier-classifier) plus a confidence threshold.
- **Vault contradictions.** PR 13's monthly lint sweep (just landed in PR #304) will catch any contradictions between YouTube-derived entity pages and CSI-derived ones. Good — this is a feature, not a bug.

## Decision log

- **2026-05-16:** Operator (Kevin) accepted the analysis above and agreed to defer until CSI v2 has live-fired through the loop a few more times. Plan captured here so it doesn't get lost.

## How to update this doc

Same convention as the parent plan doc:

1. When the trigger conditions above are met, add a "Status moved to active" entry at the top.
2. Update each PR's section with shipped commit SHA after merge.
3. Update the "Trigger conditions" section to reflect what was actually verified.
4. After all four sub-PRs ship, mark the plan complete and archive.
