# CLAUDE.md

This file provides quick working context for Claude (and other coding agents) in this repository.

## Project Description
`universal_agent` is a Python-based agent runtime and orchestration project.

It includes:
- Agent execution and orchestration logic under `src/universal_agent/`
- Operational docs under `docs/`
- Environment-driven feature flags and scheduler controls via `.env`

## Key Commands
- Install deps: `uv sync`
- Run app: `uv run python -m src.universal_agent.main`
- Run tests: `uv run pytest`
- Lint/format (if configured): `uv run ruff check .` / `uv run ruff format .`

## Git Workflow (MUST READ)
- **Read [`docs/deployment/ai_coder_instructions.md`](docs/deployment/ai_coder_instructions.md) before your first commit.** It defines the branch discipline, commit conventions, and `/ship` handoff protocol that all AI coders must follow.
- TL;DR: Work on `feature/latest2`. Push there. Never touch `develop` or `main`. Someone else runs `/ship`.

## Claude Execution Environments (MUST READ before touching anything Claude-related)
UA runs **TWO separate Claude environments side-by-side on the VPS**:

1. **ZAI-mapped (default everywhere except `/opt/ua_demos/`)** — cheap GLM models via the ZAI proxy. Used for all routine UA work, Cody's normal coding tasks, the ClaudeDevs intel cron, Simone heartbeats, etc.
2. **Anthropic-native (only inside `/opt/ua_demos/<demo-id>/`)** — real Claude models (Opus/Sonnet/Haiku) via the Max plan OAuth session. Used **only** for Phase 3 demo execution where the demo needs to exercise brand-new Anthropic features that the ZAI proxy may not have yet.

Mistaking one for the other is the #1 source of confusion. Before debugging anything Claude-related, **read [`docs/06_Deployment_And_Environments/09_Demo_Execution_Environments.md`](docs/06_Deployment_And_Environments/09_Demo_Execution_Environments.md)** — especially the decision tree and the CLI-vs-SDK auth wrinkle.

Operational runbook: [`docs/operations/demo_workspace_provisioning.md`](docs/operations/demo_workspace_provisioning.md).

## ClaudeDevs Intelligence v2 — Active Implementation Plan
The ClaudeDevs X intel pipeline is undergoing a v2 rebuild. Two living docs track it:

- **Design (what we're building):** [`docs/proactive_signals/claudedevs_intel_v2_design.md`](docs/proactive_signals/claudedevs_intel_v2_design.md) — the original 13-PR design with vault-as-canonical-product, append-dominant Memex maintenance, Phase 0–5 pipeline, Simone↔Cody orchestration.
- **Plan (what's left):** [`docs/proactive_signals/claudedevs_intel_v2_remaining_work.md`](docs/proactive_signals/claudedevs_intel_v2_remaining_work.md) — reconciled execution catalog. Cross-references original design § 16 PRs to the actual reconciled PRs (some shipped scaffolding only; their wiring is tracked separately). Lists what's shipped vs what's left across four phases. Updated after every ship — read this first when picking up the work.

## Working Rules
- Keep changes small and targeted.
- Do not commit secrets, credentials, or local state files.
- Prefer root-cause fixes over temporary workarounds.
- Update docs when behavior or operations change.

## Pre-Implementation Reading — DO NOT SKIP

**Why this section exists.** On 2026-05-06 an agent was minutes away from shipping ~50 lines of new orchestration logic into `memory/HEARTBEAT.md` (claim tasks, route to Simone, enforce concurrency cap, reset orphaned in-progress tasks) before the operator stopped them and asked "doesn't Task Hub already do this?" It does. Every line of the proposed addition was redundant with `services/dispatch_service.py` + `task_hub.py`, which the agent had not read. The actual missing piece was a 30-line *producer* change — the consumer side was already wired through `dispatch_sweep` + `route_all_to_simone`. Same class of error as the v2 shakedown: shipping without grounding.

**The rule:** before you propose new logic for any of the verbs below, grep for the verb in the canonical service module. If a function already exists, compose with it. If you can't tell whether something exists, you have NOT done your reading and you should NOT propose a change yet.

| If you're about to propose | Read first |
|---|---|
| Task claiming, routing, atomic dispatch, concurrency cap, queue rebuild, dedup | `src/universal_agent/services/dispatch_service.py` + `src/universal_agent/task_hub.py`. Heartbeats call `dispatch_sweep` → `claim_next_dispatch_tasks(limit=N)`. Every claimed task auto-routes to Simone via `route_all_to_simone`. |
| Stale / orphaned in-progress task recovery | `task_hub.py` — `UA_TASK_STALE_ENABLED` / `UA_TASK_STALE_MIN_AGE_MINUTES` env vars. Don't write a per-task reaper. |
| Cron registration (system jobs) | `gateway_server._register_system_cron_job` helper. Defaults `catch_up_on_restart=True`, takes `required_secrets`, handles update-vs-create. Do not hand-roll. |
| Artifact path resolution | `src/universal_agent/artifacts.py:resolve_artifacts_dir`. Default is `<repo-root>/artifacts`, NOT `AGENT_RUN_WORKSPACES`. Read this before writing any `find` or `ls` diagnostic. |
| URL fetching for CSI / linked-source enrichment | `services/csi_url_judge.enrich_urls` — three passes (pre-filter → LLM judge → fetch). The `trust_source=True` parameter bypasses the judge for official-handle lanes. |
| Research grounding (open-web search restricted to official sources) | `services/research_grounding.is_allowed` — separate code path from the URL judge. The `research_allowlist` in `intel_lanes.yaml` only gates this path, NOT tweet-link fetching. |
| Skill invocation by a principal | The skill's `SKILL.md` already documents the workflow. Don't re-document it in `HEARTBEAT.md`. |

**Specific anti-patterns shipped (or nearly shipped) here that must not recur:**

1. Writing "Simone, check Task Hub for source_kind X" in HEARTBEAT.md. Task Hub already routes every claimed task to Simone. The missing piece is always *producing* the task.
2. Writing "concurrency cap of N" in a directive. `claim_next_dispatch_tasks(limit=N)` is the cap.
3. Inventing a fallback artifact path. `artifacts.resolve_artifacts_dir` is canonical.
4. Adding catch-up / backfill logic per-cron. `_register_system_cron_job` already handles it.
5. Adding orphan-reset directives. The stale-task policy in Task Hub is the right knob.

**The 30-second pre-flight check before writing new code:**

```
grep -rn "<verb you're about to use>" \
  src/universal_agent/services/ \
  src/universal_agent/task_hub.py \
  src/universal_agent/cron_service.py \
  src/universal_agent/artifacts.py
```

If matches come back, read them before proposing anything. If you don't have time to read them, you don't have time to ship.

## Production Verification Rules — DO NOT SKIP

**Why this section exists.** Between 2026-04-15 and 2026-05-06 the v2 ClaudeDevs intel rebuild shipped 17 PRs with 439 passing unit tests and a "shakedown log" that declared the system green. After all of that, an operator pulled production state and found: (a) `/opt/ua_demos/` had only the smoke workspace — Phase 2/3 had never executed end-to-end despite Simone (the principal who owns Phase 2) being live and the `cody-scaffold-builder` skill being deployed, because `memory/HEARTBEAT.md` never directed her to scan new vault entities; (b) the linked-doc fetcher was silently dropping the actual documentation downstream phases were supposed to consume because an LLM judge gate was tagging official-handle links as "promotional"; (c) two consecutive sessions of "everything looks green" had concealed the gap. 439 unit tests caught none of these because each test stubbed the boundary it didn't own. The architecture diagram is not the system. Skill files on disk are not the same as the heartbeat directives that invoke them. Mocked end-to-end loops are not the same as production end-to-end runs.

**Note on principals vs. sub-agents.** UA has top-level Claude Code principals (Simone, Cody, Atlas — full orchestrator instances driven by heartbeats and dispatching their own sub-agents) and helper sub-agents (the entries in `.claude/agents/<name>.md` like `csi-supervisor`, `factory-supervisor`, `evaluation-judge`). They are different. Listing `.claude/agents/` will not show Simone or Cody — that does not mean they're missing. Simone's directive file is `memory/HEARTBEAT.md`. Cody runs as her downstream task executor via Task Hub. Diagnose presence of a principal by checking heartbeat sessions / daemon registration, not by `ls .claude/agents/`.

**These rules apply to every PR and every "phase complete" claim:**

1. **Skill deployed ≠ skill invoked.** Before declaring a phase complete, prove that *some invoker* in production is actually pointing at the skill by name. A skill file in `.claude/skills/<name>/` does nothing on its own. Skills are invoked from one of three places, and the check is different for each:
   - **Sub-agent invocation:** `grep -l <skill-name> /opt/universal_agent/.claude/agents/*.md` — must return at least one sub-agent definition.
   - **Principal heartbeat invocation (Simone, Cody, Atlas):** `grep -n <skill-name> /opt/universal_agent/memory/HEARTBEAT.md` — must return at least one directive.
   - **Task Hub-mediated invocation:** check that some producer enqueues a task type whose handler invokes the skill, AND that the consumer principal's directives tell it to claim that task type. Both ends required.

   At least one of the three checks must pass. If none do, the skill is dead code regardless of how many tests exercise it directly.

2. **Phase complete = real artifact on real disk.** A phase is not complete until a representative real-world artifact exists at the expected path on the VPS. Examples: a `cody_demo_task` row in Task Hub created by a non-test run; a `/opt/ua_demos/<id>/manifest.json` with `endpoint_hit=anthropic_native`; a vault entity page authored by a non-mocked Simone run. "Mechanical end-to-end loop synthesized in-memory" is NOT verification — it's a sanity check on the function-call graph, nothing more.

3. **Diagnostic commands must read the canonical resolver, not your guess.** Path resolution lives in code (e.g. `artifacts.py:resolve_artifacts_dir` defaults to `<repo-root>/artifacts`, not `AGENT_RUN_WORKSPACES`). Before you script a `find` or `ls`, read the resolver function. Do not invent fallback paths.

4. **No conflation of code paths under similar names.** "URL allowlist" exists in three different files for three different purposes (research grounding, csi_url_judge pre-filter, csi_url_judge LLM judge). Before you say "the allowlist blocks X," follow the call chain from the actual call site. Use grep on the call site, not on the term.

5. **Prove your claim before stating it.** When asserting how the system behaves ("Task Hub queueing is per-handle"), open the function that does the gating and read the body. Function names lie; bodies don't. If you don't have time to read the body, say "I think X but haven't confirmed" — never assert.

6. **End-of-PR production smoke is mandatory for any PR that touches a phase boundary.** A "phase boundary" PR is one whose value depends on a downstream agent picking up its output. Examples: scaffold-builder shipping → must verify a real Task Hub row was created. Cody implementation contract → must verify a real `manifest.json` was written. The shakedown log format is fine; what's NOT fine is letting "smoke deferred to operator" become permanent. If the smoke can't run from the dev box, schedule it on the VPS within 24 hours of merge AND record the result back in the PR thread.

7. **Sandbox honesty.** When working from a sandbox that can't SSH the VPS, say so up front. Don't loop the operator through 5 incremental commands when one consolidated command would do. Don't claim "I checked" when you can't.

8. **Branch-versus-deploy honesty.** A commit on `feature/latest2` is not deployed. A commit merged to `main` is not deployed if the GitHub Actions deploy hasn't completed. Never say "the fix is shipped" until the deploy workflow is green AND the live VPS state confirms the change took effect.

If a rule above isn't satisfiable for a given PR, say so explicitly in the commit message and the SHIP_HANDOFF, with a specific operator step to close the gap. The acceptable failure mode is "I shipped the code change but Phase 2 wiring still needs a Simone agent file deployed — see Followup #1." The unacceptable failure mode is silence.

## Caveats
- _(Living section — add caveats as we discover them.)_
- Deployment is automated via GitHub Actions: `develop` is integration/review only, and a push to `main` triggers the single production deploy workflow. Do not use ad hoc scripts, `ssh`, `rsync`, or `git pull`. See [AI Coder Instructions](docs/deployment/ai_coder_instructions.md) for the full protocol.
- **v2 Phase 2 wiring is missing in `memory/HEARTBEAT.md` as of 2026-05-06.** Simone (the heartbeat-driven principal who owns Phase 2) IS deployed and running. Cody and Atlas exist as downstream principals. The skills `cody-scaffold-builder`, `cody-task-dispatcher`, `cody-implements-from-brief`, `cody-work-evaluator`, `cody-progress-monitor` are all on disk. **What's missing is the directive in `memory/HEARTBEAT.md` telling Simone to scan new CSI vault entities each cycle and decide whether to invoke `cody-scaffold-builder`.** That's why `/opt/ua_demos/` has only `_smoke` despite the skills being present — Simone is busy with the directives she has and was never told to look at vault entities. v2 design called this "Heartbeat poll integration" in PR 8 but the actual HEARTBEAT.md edit was never made. Fix is ~10 lines in `memory/HEARTBEAT.md`.
