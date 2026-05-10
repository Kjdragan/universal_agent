# CLAUDE.md

This file provides quick working context for Claude (and other coding agents) in this repository.

> 👉 **Start here for daily work:** [`docs/WORKFLOW.md`](docs/WORKFLOW.md) is the one-page operator index. The deeper walkthrough is [`docs/06_Deployment_And_Environments/11_Daily_Dev_Workflow.md`](docs/06_Deployment_And_Environments/11_Daily_Dev_Workflow.md). Anything Claude-environment-related: [`docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md`](docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md).

## Project Description
`universal_agent` is a Python-based agent runtime and orchestration project.

It includes:
- Agent execution and orchestration logic under `src/universal_agent/`
- Operational docs under `docs/`
- Environment-driven feature flags and scheduler controls via `.env`

## Problem-Solving Philosophy

When investigating or fixing issues, always solve the **root cause** holistically — never just cure symptoms with band-aids. Before implementing a fix, ask:

1. **Can we expand capabilities** rather than restrict them? (e.g., raise a system limit rather than cap our data to fit under it)
2. **Is there a proper architectural pattern** for this? (e.g., write large data to files instead of stuffing it into env vars)
3. **Are we losing information or functionality** with this approach? If yes, find a better way.

Defensive guards and safety nets are acceptable as a *last resort backstop*, but they must not be the primary fix. The primary fix should eliminate the problem at its source.

## Code-Verified Answers

When answering questions about how this system works — architecture, data flows, service interactions, agent pipelines, or any behavioral claim — you **MUST read the actual source code first** before responding. Do not answer from memory, assumptions, or general knowledge.

**Mandatory process:**

1. **Read before you speak.** If the user asks "how does X work?", open and read the relevant source files before forming your answer. Use `grep`, `Read`, and `find` to locate the code.
2. **Cite what you find.** Reference specific files, functions, and line numbers that support your explanation. If you cannot point to actual code, say "I need to check the code" — do not guess.
3. **Never fabricate pipeline steps.** This system has complex multi-agent pipelines (email triage, heartbeat dispatch, daemon sessions, VP orchestration). These have specific intermediaries, classifiers, and routing logic. Do not simplify or omit steps you haven't verified exist or don't exist.
4. **Distinguish what you know from what you're inferring.** If you've read the code and it's clear, state it with confidence. If you're extrapolating beyond what the code shows, explicitly flag it as an inference.
5. **When in doubt, investigate more.** It is always better to spend an extra 30 seconds reading code than to give a wrong answer that wastes the user's time and erodes trust.

**Why this matters:** A confident but incorrect architectural explanation is worse than saying "let me check." The user relies on accurate descriptions of their own system to make decisions. Wrong answers about agent pipelines, email flows, or session lifecycle can lead to flawed design decisions downstream.

## LLM-Native Intelligence Design

When designing intelligence, briefing, ideation, curation, or pattern-detection features, prefer using LLM reasoning over building custom Pythonic pseudo-reasoning systems unless scale, latency, auditability, or determinism clearly require code.

Default division of labor:

1. **Code collects and preserves evidence.** Store raw facts, source records, timestamps, links, tags, state, ownership, and retrieval metadata faithfully.
2. **Code gates and protects execution.** Use deterministic rules for safety boundaries, budget/concurrency limits, deduplication, auth, irreversible actions, and promotion into Task Hub work.
3. **LLMs synthesize meaning.** When the corpus is bounded enough to fit into a briefing, handoff, or retrieval context, let the LLM infer themes, neglected opportunities, recurring blockers, and recommended actions from the evidence.

Avoid creating elaborate programmatic trend/theme/scoring systems that attempt to imitate reasoning over small-to-medium corpora. These systems often add noise, hardcode brittle assumptions, and make the agent less capable than simply giving a strong LLM the right context and asking for explicit synthesis.

Use Pythonic aggregation when it solves a real systems problem: reducing a huge corpus to retrievable chunks, enforcing invariants, computing objective metrics, maintaining indexes, or producing deterministic eligibility decisions. Do not use it just because a pattern could be expressed in code.

For briefings and operator-intelligence surfaces, prefer this pattern:

`raw records -> durable knowledge blocks -> bounded retrieval context -> LLM synthesis -> gated action candidates`

Required briefing behavior: when recent knowledge blocks include surfaced ideas, repeated warnings, stalled work, or recurring observations, the briefing LLM should explicitly assess whether any pattern or opportunity is emerging. If action is warranted, it should propose a candidate through existing gates rather than directly creating uncontrolled work.

## Cross-Machine File Resolution (SSHFS)

The Universal Agent infrastructure includes a seamless, transparent file resolution bridge via SSHFS over Tailscale.

When executing on the VPS (`uaonvps`), agents have direct, native filesystem access to the local desktop environment at the exact same path.

- **The Path Guarantee**: The local desktop path `/home/kjdragan/...` is mounted onto the VPS at `/home/kjdragan/...`.
- **Capability Implication**: **Never** build custom "file fetcher" tools or syncing scripts to move files from the desktop to the VPS for agent tasks. Instead, simply refer to the absolute `/home/kjdragan/...` path directly. Standard OS operations (`cat`, Python `open()`, etc.) will seamlessly resolve over the SSHFS mount.
- **Architectural Tenet**: This demonstrates the core design philosophy of "expanding system capabilities at the OS level" rather than building complex, brittle agent workarounds.

## Screenshots
Operator's screenshots live in Google Drive folder `Awesome Screenshots` (id: `1PM22v6FKY7Z8ukJA83LF3Ru_xGw7_S9I`). When the operator says "get the latest screenshot" / "look at the screenshot from Drive" / similar, resolve via `mcp__33c2a029-2ddb-4320-9fba-2d9695495b50__search_files` with `query: "parentId = '1PM22v6FKY7Z8ukJA83LF3Ru_xGw7_S9I'"` and `orderBy: "recency"`, then `read_file_content { fileId }` for a natural-language description of the image.

## Key Commands
- Install deps: `uv sync`
- Run app: `uv run python -m src.universal_agent.main`
- Run tests: `uv run pytest`
- Lint/format (if configured): `uv run ruff check .` / `uv run ruff format .`

## Git Workflow (MUST READ)
- **Read [`docs/deployment/ai_coder_instructions.md`](docs/deployment/ai_coder_instructions.md) before your first commit.** It defines the branch discipline, commit conventions, `/ship` handoff protocol, and the **Agent-Type → Workflow Matrix** that all AI coders must follow.
- **Branch model (post-2026-05-10 simplification):** any branch → PR → `main` → Deploy. The `develop` branch was retired — staging never materialized and the chain was adding failure modes (silent no-op pushes, stale-branch divergence, mid-chain `git fetch` flakes) without integration value. See [`docs/06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md`](docs/06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md) for the full new model.
- TL;DR: work on a feature branch (Kevin's pseudo-trunk is `feature/latest2`; bot branches use `<bot>/<task-id>`), push, run `/ship` (or open the PR manually) to land in `main`. The merge to `main` triggers `.github/workflows/deploy.yml`. **Never push directly to `main`.**
- All PRs to `main` (and `feature/latest2`) are gated by [`.github/workflows/pr-validate.yml`](.github/workflows/pr-validate.yml) — `py_compile` on every changed `.py`, `ruff check`, `pytest tests/unit`, and a tripwire on `.py.bak` / `.swp` / `.orig` artifacts. **PR-Validate is the only pre-deploy gate** now that `develop` is gone, so don't merge red.
- `deploy.yml` has a `paths-ignore` filter (docs/, **.md, reports/, state/, artifacts/) so docs-only commits (e.g. nightly drift report, openclaw release sync) merging to `main` don't trigger a production restart. Mixed code+docs commits still deploy — that's the safe default.

## Claude Execution Environments (MUST READ before touching anything Claude-related)
UA runs **THREE Claude execution profiles** across the VPS and Kevin's desktop. Mistaking one for another is the #1 source of confusion in the system.

1. **Kevin's interactive coding** (Antigravity terminal, Antigravity IDE side panel, plain `claude` from any shell) → **Anthropic Max plan** (real Opus/Sonnet/Haiku via OAuth). Default everywhere after the inversion plan ships.
2. **UA autonomous agent runs** (Simone heartbeats, Atlas, Cody normal work, ClaudeDevs intel cron, dispatch sweep, etc.) → **ZAI proxy / GLM models** (cheap inference). ZAI vars are now injected at service-start by `initialize_runtime_secrets()` reading Infisical, NOT via user-global `~/.claude/settings.json`.
3. **Demo workspace execution** (`/opt/ua_demos/<id>/`) → **Anthropic Max plan**. Used for Phase 3 demo execution where the demo needs to exercise brand-new Anthropic features that ZAI may not have yet.

**Canonical reference (read this FIRST before touching any Claude env, settings.json, or Anthropic-related code):** [`docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md`](docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md) — the inversion plan, per-machine matrix, `zai()` shell function, Antigravity Remote-SSH workflow, acid tests, rollback.

Companion docs:
- [`docs/06_Deployment_And_Environments/09_Demo_Execution_Environments.md`](docs/06_Deployment_And_Environments/09_Demo_Execution_Environments.md) — demo path mechanics, decision tree, CLI-vs-SDK auth wrinkle.
- [`docs/operations/demo_workspace_provisioning.md`](docs/operations/demo_workspace_provisioning.md) — one-time setup runbook for `/opt/ua_demos/`.

## ClaudeDevs Intelligence v2 — Active Implementation Plan
The ClaudeDevs X intel pipeline is undergoing a v2 rebuild. Two living docs track it:

- **Design (what we're building):** [`docs/proactive_signals/claudedevs_intel_v2_design.md`](docs/proactive_signals/claudedevs_intel_v2_design.md) — the original 13-PR design with vault-as-canonical-product, append-dominant Memex maintenance, Phase 0–5 pipeline, Simone↔Cody orchestration.
- **Plan (what's left):** [`docs/proactive_signals/claudedevs_intel_v2_remaining_work.md`](docs/proactive_signals/claudedevs_intel_v2_remaining_work.md) — reconciled execution catalog. Cross-references original design § 16 PRs to the actual reconciled PRs (some shipped scaffolding only; their wiring is tracked separately). Lists what's shipped vs what's left across four phases. Updated after every ship — read this first when picking up the work.

## Working Rules
- Keep changes small and targeted.
- Do not commit secrets, credentials, or local state files.
- Prefer root-cause fixes over temporary workarounds.
- Update docs when behavior or operations change.

## Operating Hours / Dormancy Default

**Active window: 6:00 AM – 9:00 PM Houston time.** **Dormant window: 9:00 PM – 6:00 AM Houston time.**

By default, every cron job, polling loop, scheduled GitHub Actions workflow, or background service runs **only during the active window**. Use `default_timezone="America/Chicago"` (or `TZ=America/Chicago` in cron strings) so DST is handled automatically. GitHub Actions schedules are UTC-only — express in UTC and accept the 1h DST drift.

**Why:** the operator does not want infrastructure burning quota / firing emails / restarting processes / running LLM calls while he's asleep. Most "intelligence" surfaces are read in the morning anyway — generating them at 3 AM provides zero operational value but adds cost.

**Adding a new cron job:** check whether it qualifies as a documented exception (downstream-consumer dependency during dormancy / transient-data capture / latency-sensitive incident response). If none apply, schedule inside the active window. See [`docs/operations/operating_hours_dormancy.md`](docs/operations/operating_hours_dormancy.md) for the exception checklist + currently-registered exceptions.

A guard test (`tests/unit/test_cron_dormancy_defaults.py`) pins active-hour schedules and asserts new crons fall inside the active window unless they're listed as exceptions in the doc.

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
| Storing/loading any application secret (API keys, tokens, credentials) | `docs/deployment/secrets_and_environments.md` is the canonical guide. Infisical is the single source of truth. UA services call `initialize_runtime_secrets()` at startup; never read secrets from `.env`/`os.getenv` directly except for Infisical bootstrap creds. |
| Adding/touching anything in `.mcp.json` (especially `env.*` blocks) | `docs/deployment/secrets_and_environments.md` § "MCP Server Credentials". **Every value MUST be a `${VAR}` placeholder, never a literal token.** Resolution at runtime is via `scripts/claude_with_mcp_env.sh` which runs `initialize_runtime_secrets()`. The `infisical run` CLI is the WRONG primitive — it has its own auth context that doesn't exist headless on the VPS. |
| Deciding which web-fetch / search tool an agent should use (autonomous vs interactive vs demo) | `docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md` § "Tool Surface by Execution Mode". Autonomous mode → ZAI MCPs (`webReader`, `webSearchPrime`, `zai-mcp-server` vision). Interactive Anthropic Max sessions and demo workspaces → Claude built-ins (`WebFetch`, `WebSearch`). The model behind autonomous mode is GLM-5.1, which was trained on the ZAI MCP schemas — Claude built-ins under GLM are unreliable. The model behind interactive/demo is Claude — calling ZAI MCPs from there burns ZAI quota for an Anthropic-side use. |

**Specific anti-patterns shipped (or nearly shipped) here that must not recur:**

1. Writing "Simone, check Task Hub for source_kind X" in HEARTBEAT.md. Task Hub already routes every claimed task to Simone. The missing piece is always *producing* the task.
2. Writing "concurrency cap of N" in a directive. `claim_next_dispatch_tasks(limit=N)` is the cap.
3. Inventing a fallback artifact path. `artifacts.resolve_artifacts_dir` is canonical.
4. Adding catch-up / backfill logic per-cron. `_register_system_cron_job` already handles it.
5. Adding orphan-reset directives. The stale-task policy in Task Hub is the right knob.
6. Inlining a literal token into `.mcp.json` to satisfy "Claude Code Doctor says MCP needs <TOKEN>". Doctor is correctly diagnosing that the env var is unset in the parent process; the fix is to launch `claude` via `scripts/claude_with_mcp_env.sh` (or its alias), which runs the canonical Infisical bootstrap. The 2026-05-08 Hostinger token leak (`docs/operations/2026-05-08_hostinger_token_remediation.md`) is the cautionary tale.
7. Wrapping `claude` with `infisical run --env=… -- claude` (the CLI). The CLI requires its own interactive `infisical login` session that doesn't exist on the VPS for user `ua`; falls into a tty-only login prompt and fails non-tty. Use the Python SDK path (`initialize_runtime_secrets()`) instead — that's what `scripts/claude_with_mcp_env.sh` does.
8. Letting a background tool (Doctor / IDE plugin) auto-resolve `${VAR}` in `.mcp.json` to a literal value and then committing the diff. **If `git status` ever shows `.mcp.json` modified with a `${VAR}` → literal substitution, `git checkout -- .mcp.json` immediately.** Never commit the substitution.

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

9. **Verify the right artifact (Rule A).** Before any browser-based or HTTP-based end-to-end check against `app.clearspringcg.com` or `127.0.0.1:8002`, the agent MUST first hit `GET /api/v1/version` (no auth) and log the returned `commit_sha`, `branch`, and `process_started_at`. If the live SHA does not contain the change being verified, **STOP** — do not run the browser pass, do not declare anything verified. The acceptable response is: "live SHA is X, my change is on SHA Y which has not deployed yet — verification deferred until ship completes." The unacceptable response is burning a 5-minute browser session against stale code and reporting its findings as if they reflect the new behavior.

10. **Backend logic vs. UI rendering — different verification paths (Rule B).**
    - **Backend logic changes** (DB queries, scoring/ranking, route handlers, service-layer functions) → authoritative verification is direct Python invocation in dev: `PYTHONPATH=src uv run python -c "from universal_agent.services.X import Y; print(Y(...))"`. This is conclusive because it exercises the new code in-process. Browser verification of backend logic against production is only meaningful AFTER ship.
    - **UI rendering** (drawer layouts, hover states, optimistic updates, CSS, component composition) → verified post-deploy against `app.clearspringcg.com` via the agent-browser sub-agent. Pre-deploy local browser checks are acceptable when the change is `web-ui/` only (Next.js hot reload).
    - **Anti-pattern:** dispatching a browser agent against production to "verify the dedup I just wrote" before the dedup is on `main` and deployed. The browser is looking at the old code; its findings about the new behavior are noise.

11. **Ship-then-verify cadence for backend-touching work (Rule C).** When work touches gateway endpoints, DB queries, scoring logic, or service-layer code AND end-to-end browser confirmation is desired:
    1. Land code on `feature/latest2` with local Python verification of the new behavior (Rule B).
    2. Operator runs `/ship`.
    3. Wait for GH Actions deploy to go green AND `GET /api/v1/version` on production returns the new SHA.
    4. Then dispatch the browser agent against `app.clearspringcg.com`.
    5. Browser agent's first action is Rule A: hit `/api/v1/version`, log SHA, confirm it includes the change. If not, abort.

12. **Deploy-time service restart is already part of the workflow (Rule D — verified, not a gap).** `.github/workflows/deploy.yml` runs `sudo systemctl restart universal-agent-gateway universal-agent-api universal-agent-webui ...` after the rsync + venv sync. Gateway picks up Python changes on the next deploy by construction. There is NO separate "remember to restart the gateway" step needed. If a backend change is on `main` and the deploy workflow is green, the new code is live. Confirm via Rule A's `/api/v1/version` check, not by guessing.

If a rule above isn't satisfiable for a given PR, say so explicitly in the commit message and the SHIP_HANDOFF, with a specific operator step to close the gap. The acceptable failure mode is "I shipped the code change but Phase 2 wiring still needs a Simone agent file deployed — see Followup #1." The unacceptable failure mode is silence.

## Documentation Maintenance Rules

All documentation for this project MUST reside exclusively within the `docs/` directory. Creating any other documentation directories (such as `OFFICIAL_PROJECT_DOCUMENTATION/`) is strictly prohibited.

When asked to update or create documentation:

1. **Always Check the Indexes First**: You must consult `docs/README.md` and `docs/Documentation_Status.md` before proceeding.
2. **Update Over Create**: If a document already exists for your topic, update the existing file rather than creating a new one.
3. **Log New Documents**: If you must create a new file, you are required to add a link and description of that new file to both `docs/README.md` and `docs/Documentation_Status.md`.
4. **No Unindexed Files**: No document should exist in `docs/` without being linked from one of the two index files.

### Dynamic Documentation Maintenance (MANDATORY)

Documentation updates are **not optional follow-up work** — they are part of the implementation itself. When you make code changes that affect system behavior, architecture, routing, protocols, or configuration:

1. **Update docs during implementation, not after.** Treat documentation updates as a deliverable of the same work unit, not a separate task.
2. **Identify affected docs before coding.** Check `docs/README.md` and `docs/Documentation_Status.md` to find which existing documents cover the areas you are changing. Read them before you start coding so you understand the documented contract.
3. **Update canonical source-of-truth docs first.** If your change touches email routing, update `82_Email_Architecture`. If it touches VP delegation, update `03_VP_Workers_And_Delegation`. If it touches Task Hub, update `107_Task_Hub_Master_Reference`. Always update the canonical doc, not a peripheral reference.
4. **Include visual artifacts.** Mermaid diagrams, routing tables, and code-verified citations in doc updates — not just prose paragraphs.
5. **Update both indexes.** Any new doc must appear in both `docs/README.md` and `docs/Documentation_Status.md`. Existing doc updates should bump the "last updated" timestamp.
6. **When in doubt, update.** If you are unsure whether a change is "significant enough" to warrant a doc update, it is. Architecture drift caused by undocumented changes is worse than a minor redundant doc update.

## Implementation Plan Quality Standards

Implementation plans are decision documents — they must make complex system flows understandable at a glance. Text-only explanations are insufficient for this codebase's multi-agent architecture.

**Every implementation plan MUST include:**

1. **Mermaid sequence diagrams** for any multi-component interaction (email flows, task dispatch chains, agent delegation). Show the actual participants, message payloads, and decision points.
2. **Mermaid flowcharts** for routing/branching logic (e.g., "which inbox → which agent → which action").
3. **Code-verified citations** with `file:///path#Lnnn` links to the actual source lines that support each claim. Do not describe system behavior without pointing to the code that implements it.
4. **Summary tables** for change impact ("What Changes vs. What Stays"), communication patterns, or comparison of alternatives.
5. **Concrete code snippets** for every proposed modification — show the actual function signatures, new helper functions, and prompt text changes.
6. **Phase-by-phase breakdown** with clear boundaries between config-only changes, code changes, and prompt changes.

**Why this matters:** This system has complex multi-agent pipelines where a wrong mental model leads to flawed design decisions. Visual artifacts (diagrams, tables) catch misunderstandings that paragraphs hide.

## Codex-Specific Addendum

> This file is symlinked from `AGENTS.md` so Codex / Antigravity / OpenAI agents read identical rules. The sections below apply when **Codex** specifically is the active agent (PR review, browser-based debugging). Claude Code can ignore them; Codex must follow them.

### Codex Review Guidelines

These guidelines apply when Codex reviews pull requests targeting `main` (the only deploy-firing branch post-2026-05-10) and `feature/latest2` (Kevin's working pseudo-trunk):

- Flag any code that logs, stores, or transmits PII or secrets without explicit redaction.
- Verify that every new or modified API route is wrapped by the appropriate authentication/authorization middleware.
- Flag blocking I/O (database calls, HTTP requests) that runs inside an async event loop without `await` or proper executor offloading.
- Verify that background tasks and service loops handle exceptions so they don't silently die.
- Flag Python code that imports secrets or API keys from environment variables directly instead of using the Infisical secret service (our canonical secrets provider — never `.env` files or `os.getenv` for secrets).
- Flag changes that touch `.github/workflows/deploy.yml` if the corresponding canonical docs in `docs/deployment/` were not updated in the same PR.
- Do not flag formatting-only issues (whitespace, line length) unless they break a linter gate.
- Treat typos in user-facing strings or documentation as P1.

### Codex Browser Debugging Rules

When working on frontend bugs, local web apps, or browser-based verification:

1. Use the browser MCP tools instead of guessing.
2. Start by navigating to the local app URL.
3. Reproduce the bug in the browser.
4. Inspect screenshots and page state.
5. Inspect failed network requests if relevant.
6. Only then edit code.
7. After edits, retest in the browser to confirm the fix.

Do not claim a UI bug is fixed unless it has been verified through the browser tools.

## Caveats
- _(Living section — add caveats as we discover them.)_
- Deployment is automated via GitHub Actions: a push to `main` (via merged PR) triggers the single production deploy workflow. The `develop` branch was retired 2026-05-10 — see [`docs/06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md`](docs/06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md). Do not use ad hoc scripts, `ssh`, `rsync`, or `git pull`. See [AI Coder Instructions](docs/deployment/ai_coder_instructions.md) for the full protocol.
- **v2 Phase 2 wiring is missing in `memory/HEARTBEAT.md` as of 2026-05-06.** Simone (the heartbeat-driven principal who owns Phase 2) IS deployed and running. Cody and Atlas exist as downstream principals. The skills `cody-scaffold-builder`, `cody-task-dispatcher`, `cody-implements-from-brief`, `cody-work-evaluator`, `cody-progress-monitor` are all on disk. **What's missing is the directive in `memory/HEARTBEAT.md` telling Simone to scan new CSI vault entities each cycle and decide whether to invoke `cody-scaffold-builder`.** That's why `/opt/ua_demos/` has only `_smoke` despite the skills being present — Simone is busy with the directives she has and was never told to look at vault entities. v2 design called this "Heartbeat poll integration" in PR 8 but the actual HEARTBEAT.md edit was never made. Fix is ~10 lines in `memory/HEARTBEAT.md`.
