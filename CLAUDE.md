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

- **The VPS user**: interactive Claude Code on the VPS runs as system user `ua` (not `kjdragan`). Reach the VPS with `ssh ua@uaonvps`. The `/home/kjdragan/...` paths below are an SSHFS mount point — `ua` can read those paths via the mount; there's no `kjdragan` user account on the VPS itself. Global `~/.claude/CLAUDE.md` for VPS interactive sessions therefore lives at `/home/ua/.claude/CLAUDE.md`.
- **The Path Guarantee**: The local desktop path `/home/kjdragan/...` is mounted onto the VPS at `/home/kjdragan/...`.
- **Capability Implication**: **Never** build custom "file fetcher" tools or syncing scripts to move files from the desktop to the VPS for agent tasks. Instead, simply refer to the absolute `/home/kjdragan/...` path directly. Standard OS operations (`cat`, Python `open()`, etc.) will seamlessly resolve over the SSHFS mount.
- **Architectural Tenet**: This demonstrates the core design philosophy of "expanding system capabilities at the OS level" rather than building complex, brittle agent workarounds.

## Self-Service Infisical Secret Access

You have machine-identity Infisical creds pre-loaded in your shell — use them to fetch secrets yourself instead of asking the operator. Sources: Kevin's desktop `~/.config/ua/infisical-machine-id`, VPS `/opt/universal_agent/.env`. Both export `INFISICAL_CLIENT_ID` / `INFISICAL_CLIENT_SECRET` / `INFISICAL_PROJECT_ID`. The interactive user-CLI session at `~/.infisical/infisical-config.json` is flaky for headless calls — **DO NOT rely on it; always use universal-auth (machine-identity)**.

**Inject every secret into a child process (preferred pattern):**
```bash
TOK=$(infisical login --method=universal-auth \
        --client-id="$INFISICAL_CLIENT_ID" \
        --client-secret="$INFISICAL_CLIENT_SECRET" --plain --silent)
INFISICAL_TOKEN="$TOK" infisical run \
    --projectId="$INFISICAL_PROJECT_ID" --env=development --silent -- \
    <your command>
```

**Single-key read:** swap `run … -- <cmd>` for `secrets get KEY_NAME --plain --silent` (token + projectId/env flags stay the same).

**Guardrails — NON-NEGOTIABLE:**
- **NEVER print secret VALUES to chat.** `infisical secrets` with `--plain` dumps `KEY=VALUE` pairs (not just keys). Filter with `awk -F= '{print $1}'` when you only want to enumerate names.
- Prefer `infisical run -- CMD` over fetching to shell variables — the secret stays inside the child process and never lands in your env or command history.
- Ask the operator before reading production secrets if the task could be done in dev.
- Never `infisical secrets set` / delete / rotate without explicit operator approval.
- For UA's own Python services, the canonical bootstrap is still `initialize_runtime_secrets()` — use the CLI only for ad-hoc diagnostics, script invocations, and one-off lookups.

Also note: bare `claude` on Kevin's desktop lazy-loads `HOSTINGER_API_TOKEN` (and only that) via `_ua_load_mcp_secrets` in `~/.bashrc`, cached at `~/.cache/ua/hostinger_token`. Replaced the eager `infisical secrets get` block on 2026-05-17 because it was hanging Antigravity terminals on an interactive arrow-key prompt that `--silent` didn't suppress. If MCP Hostinger stops resolving, the user can `rm ~/.cache/ua/hostinger_token` to force refresh on next `claude` launch.

## Key Commands
- Install deps: `uv sync`
- Run app: `uv run python -m src.universal_agent.main`
- Run tests: `uv run pytest`
- Lint/format (if configured): `uv run ruff check .` / `uv run ruff format .`

## VPS Autonomy (MUST READ)

You have direct SSH access to the VPS as `ua@uaonvps` and full SSHFS at `/home/kjdragan/...`. **Do not hand the operator commands you can run yourself.** Asking "should I run X?" or "please run X" when X is a non-destructive, fully-scriptable command burns operator time and is explicitly disallowed.

Default to running it. Stop and ask first only for: destructive operations (deletes, force-pushes, dropping data), operations that incur real cost the operator hasn't already authorized, anything that mutates external accounts (Google, GitHub, Stripe), or operations that touch production secrets in a way that could leak credentials into the transcript.

Diagnostic reads, log tails, manual cron triggers, status checks, restarting `universal-agent.service` after a change you just made, and similar operational steps: just do them, narrate the result.

## Git Workflow (MUST READ)
- Read [`docs/deployment/ai_coder_instructions.md`](docs/deployment/ai_coder_instructions.md) before your first commit. It defines branch discipline, commit conventions, `/ship` handoff, and the **Agent-Type → Workflow Matrix**.
- **Branch model:** any branch → PR → `main` → Deploy. `develop` retired 2026-05-10. See [`04_Branching_And_Release_Workflow`](docs/06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md).
- TL;DR: branch from `main` (per-task — Claude Code work uses `claude/<task>`, Codie work uses `codie/<task>`, operator work uses `kevin/<task>` or `feature/<task>`), push, run `/ship` (or `gh pr create --base main`). `pr-auto-merge.yml` auto-enables auto-merge for all non-draft PRs EXCEPT `codie/*`, `kevin/*`, and `feature/*` (which need manual review); CI runs; squash-merge fires `.github/workflows/deploy.yml`. `feature/latest2` retired 2026-05-13 — `main` is the home base. **Never push directly to `main`.**
- PRs are gated by [`.github/workflows/pr-validate.yml`](.github/workflows/pr-validate.yml) — `py_compile` on every changed `.py`, `ruff check`, `pytest tests/unit`, and a `.py.bak`/`.swp`/`.orig` tripwire. **PR-Validate is the only pre-deploy gate.** Don't merge red.
- `deploy.yml` has `paths-ignore` for docs (`docs/`, `**.md`, `reports/`, `state/`, `artifacts/`) so docs-only commits don't restart production. Mixed code+docs commits still deploy.
- Auto-merge uses `AUTO_MERGE_PAT` (not `GITHUB_TOKEN` — that suppresses downstream workflow events). `deploy.yml` lacks a `concurrency:` guard — simultaneous merges can race on `/opt/universal_agent/.git/index.lock`. Recommended guard: `concurrency: { group: deploy-production, cancel-in-progress: false }`. Full rationale: [`docs/deployment/ci_cd_pipeline.md`](docs/deployment/ci_cd_pipeline.md).

## Claude Execution Environments (MUST READ before touching anything Claude-related)
UA runs **THREE Claude execution profiles** across the VPS and Kevin's desktop. Mistaking one for another is the #1 source of confusion in the system.

1. **Kevin's interactive coding** (Antigravity terminal, Antigravity IDE side panel, plain `claude` from any shell) → **Anthropic Max plan** (real Opus/Sonnet/Haiku via OAuth). Default everywhere after the inversion plan ships. Kevin's `~/.bashrc` aliases `claude` → `scripts/claude_with_mcp_env.sh`, which bootstraps Infisical secrets so `.mcp.json` `${VAR}` placeholders resolve, strips `ANTHROPIC_*` so OAuth wins, and auto-injects `--dangerously-skip-permissions` for interactive sessions (skipped for management subcommands like `claude agents`). The new **`claude agents`** subcommand (v2.1.139+, released 2026-05-11) opens "Agent View" — a session-roster manager for parallel sessions; not a different coding interface. The flag set on `claude agents` is intentionally tiny (`--setting-sources` + `-h`) so the wrapper passes it through clean.
2. **UA autonomous principals running in-process** (Simone heartbeats, Atlas, the dispatch sweep, ClaudeDevs intel cron, etc.) → **ZAI proxy / GLM models** (cheap inference). ZAI vars are injected at service-start by `initialize_runtime_secrets()` reading Infisical, NOT via user-global `~/.claude/settings.json`. These are heartbeat-driven, run continuously inside the UA daemon, and have no per-task model switch — their `ANTHROPIC_*` env routes to ZAI by design.
3. **Cody — per-task CLI subprocesses (BOTH demo workspaces AND in-environment work)** → **Anthropic Max plan by default** as of 2026-05-11 PM (see `src/universal_agent/services/cody_mode.py:34-37` — `_HARDCODED_FALLBACK_MODE` flipped from `"zai"` to `"anthropic"`). Every Cody task carries a `cody_mode` field resolved in this priority: (a) per-task override on `task_hub_items.cody_mode`, (b) DB setting `cody_default_mode` (flippable from the dashboard tile), (c) `UA_CODY_DEFAULT_MODE` env var, (d) hardcoded `"anthropic"`. When the resolved mode is `"anthropic"`, `vp/clients/claude_cli_client.py:_build_cli_env` strips every `ANTHROPIC_*` var from the spawned subprocess so OAuth wins, and force-enables `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`. This applies to **all** Cody work — not just `/opt/ua_demos/<id>/` workspaces. Demo workspaces add a second layer of defense via a vanilla `.claude/settings.json` in `templates/ua_demos_scaffold/`.

**Common past mistakes (don't repeat):**
- ❌ "Cody normally runs on ZAI" — wrong since 2026-05-11 PM. Cody defaults to Anthropic Max for every task unless explicitly overridden. The flip was an operator decision; reverting requires either a per-task `cody_mode="zai"`, the dashboard tile, or `UA_CODY_DEFAULT_MODE=zai`.
- ❌ "Anthropic Max is only for `/opt/ua_demos/` workspaces." Wrong. `_build_cli_env` scrubs `ANTHROPIC_*` for any Cody mission with `cody_mode="anthropic"` regardless of workspace location.
- ❌ "Typing `claude agent` (singular) opens a new agent UI." The real command is `claude agents` (plural). `claude agent` is parsed as a prompt argument and does nothing special.

**Canonical reference (read this FIRST before touching any Claude env, settings.json, or Anthropic-related code):** [`docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md`](docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md) — the inversion plan, per-machine matrix, `zai()` shell function, Antigravity Remote-SSH workflow, acid tests, rollback.

Companion docs:
- [`docs/06_Deployment_And_Environments/09_Demo_Execution_Environments.md`](docs/06_Deployment_And_Environments/09_Demo_Execution_Environments.md) — demo path mechanics, decision tree, CLI-vs-SDK auth wrinkle.
- [`docs/operations/demo_workspace_provisioning.md`](docs/operations/demo_workspace_provisioning.md) — one-time setup runbook for `/opt/ua_demos/`.

## ClaudeDevs Intelligence v2 — Active Implementation Plan
The ClaudeDevs X intel pipeline is undergoing a v2 rebuild. Two living docs track it:

- **Design (what we're building):** [`docs/proactive_signals/claudedevs_intel_v2_design.md`](docs/proactive_signals/claudedevs_intel_v2_design.md) — the original 13-PR design with vault-as-canonical-product, append-dominant Memex maintenance, Phase 0–5 pipeline, Simone↔Cody orchestration.
- **Plan (what's left):** [`docs/proactive_signals/claudedevs_intel_v2_remaining_work.md`](docs/proactive_signals/claudedevs_intel_v2_remaining_work.md) — reconciled execution catalog. Cross-references original design § 16 PRs to the actual reconciled PRs (some shipped scaffolding only; their wiring is tracked separately). Lists what's shipped vs what's left across four phases. Updated after every ship — read this first when picking up the work.
- **Next phase (deferred until CSI v2 is live-verified):** [`docs/proactive_signals/youtube_demo_unification_plan.md`](docs/proactive_signals/youtube_demo_unification_plan.md) — captures the deferred initiative to flow YouTube tutorial "runnable demo" creation through the same CSI demo workspace pattern. Trigger condition: ≥5 organic CSI tier-3 end-to-end runs first. Operator agreed 2026-05-16 to defer until CSI v2 is live-verified post-incident.

## Working Rules
- **Local dev happens on Kevin's desktop, not the VPS.** Spin up the stack with `just dev` from `/home/kjdragan/lrepos/universal_agent/`. Autonomous loops (heartbeat, cron, dispatch sweep, AgentMail polling, etc.) are OFF in dev by default — set `UA_DEV_<NAME>_FORCE_ON=1` in `.env` to opt a specific loop in for testing. The VPS is production-only. Canonical runbook: [`docs/06_Deployment_And_Environments/12_Local_Dev_Environment.md`](docs/06_Deployment_And_Environments/12_Local_Dev_Environment.md). One-page operator index: [`docs/WORKFLOW.md`](docs/WORKFLOW.md). VPS-as-dev via Antigravity Remote-SSH ([Doc 11](docs/06_Deployment_And_Environments/11_Daily_Dev_Workflow.md)) is the fallback for when desktop isn't available.
- Keep changes small and targeted.
- Do not commit secrets, credentials, or local state files.
- Prefer root-cause fixes over temporary workarounds.
- Update docs when behavior or operations change.

## Operating Hours / Dormancy Default

**Active window: 6:00 AM – 9:00 PM Houston time.** **Dormant: 9:00 PM – 6:00 AM.**

Default applies to **content-generation** work only — cron jobs / polling loops / scheduled GHA workflows that burn quota to produce intelligence nobody reads until morning. Use `default_timezone="America/Chicago"` (or `TZ=America/Chicago`) so DST handles itself; GitHub Actions schedules are UTC-only — express in UTC and accept the 1h DST drift.

Dormancy does **NOT** apply to infrastructure-event handlers — deploy workflows, auto-merge, CI/PR failure handling, error alerting. Those run 24/7 because a merge can land or a CI run can fail at any wall-clock time, and silently broken production until 6 AM is unacceptable. Event-driven GHA workflows (triggered by `push`/`pull_request`/`workflow_run`) are not subject to dormancy mechanically either.

**Adding a new cron:** classify it. Content-generation → respect dormancy. Infrastructure-event → 24/7, add to `DOCUMENTED_EXCEPTIONS` citing Exception #3 (latency-sensitive incident response). Full scope rules and currently-registered exceptions: [`docs/operations/operating_hours_dormancy.md`](docs/operations/operating_hours_dormancy.md). Guard test: `tests/unit/test_cron_dormancy_defaults.py` — pins active-hour schedules and asserts new crons fall inside the active window unless listed as exceptions.

## Pre-Implementation Reading — DO NOT SKIP

**The rule:** before you propose new logic for any of the verbs below, grep for the verb in the canonical service module. If a function already exists, compose with it. If you can't tell whether something exists, you have NOT done your reading and you should NOT propose a change yet.

Postmortem context (a near-miss and a real one) and the full anti-patterns catalog: [`docs/03_Operations/130_Production_Verification_Rules.md`](docs/03_Operations/130_Production_Verification_Rules.md).

| If you're about to propose | Read first |
|---|---|
| Task claiming, routing, atomic dispatch, concurrency cap, queue rebuild, dedup | `services/dispatch_service.py` + `task_hub.py`. Heartbeats call `dispatch_sweep` → `claim_next_dispatch_tasks(limit=N)`; every claimed task auto-routes to Simone via `route_all_to_simone`. |
| Stale / orphaned in-progress task recovery | `task_hub.py` — `UA_TASK_STALE_ENABLED` / `UA_TASK_STALE_MIN_AGE_MINUTES`. Don't write a per-task reaper. |
| Cron registration (system jobs) | `gateway_server._register_system_cron_job` — handles catch-up, secrets, update-vs-create. Do not hand-roll. |
| **A new cron / scheduled task / webhook handler / demo workspace consumer / any async unit of work** | **[`docs/03_Operations/129_Task_Hub_Observability_Protocol.md`](docs/03_Operations/129_Task_Hub_Observability_Protocol.md)** — six-rule protocol (identity / claim ledger / run history / subprocess identity / protocol-violation routing / standard recovery verbs). Use `ensure_cron_task_link` + `_open_run` + `classify_worker_exit` + `_close_run` + `park_task_for_protocol_violation`. Default `skip_task_hub_link=False`; only opt out for pure no-state event handlers. |
| Artifact path resolution | `artifacts.py:resolve_artifacts_dir` — default `<repo-root>/artifacts`, NOT `AGENT_RUN_WORKSPACES`. Read this before any `find` or `ls` diagnostic. |
| URL fetching for CSI / linked-source enrichment | `services/csi_url_judge.enrich_urls` — three passes (pre-filter → LLM judge → fetch). `trust_source=True` bypasses the judge for official-handle lanes. |
| Research grounding (open-web search restricted to official sources) | `services/research_grounding.is_allowed` — separate code path from the URL judge. `research_allowlist` in `intel_lanes.yaml` only gates this path, NOT tweet-link fetching. |
| Skill invocation by a principal | The skill's `SKILL.md` is canonical. Don't re-document it in `HEARTBEAT.md`. |
| Storing/loading any application secret | `docs/deployment/secrets_and_environments.md` — Infisical is the single source of truth. Call `initialize_runtime_secrets()` at startup; never read secrets from `.env`/`os.getenv` except for Infisical bootstrap creds. |
| Adding/touching `.mcp.json` (esp. `env.*`) | `docs/deployment/secrets_and_environments.md` § "MCP Server Credentials". **Every value MUST be a `${VAR}` placeholder, never a literal token.** Resolution via `scripts/claude_with_mcp_env.sh`. `infisical run` CLI is the WRONG primitive — no headless auth context on the VPS. |
| Web-fetch / search tool selection (autonomous vs interactive vs demo) | [`10_Interactive_Coding_Environment.md`](docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md) § "Tool Surface by Execution Mode". Autonomous → ZAI MCPs (`webReader`, `webSearchPrime`, `zai-mcp-server` vision). Interactive/demo → Claude built-ins (`WebFetch`, `WebSearch`). Calling ZAI MCPs from interactive/demo burns ZAI quota for an Anthropic-side use. |

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

**Note on principals vs. sub-agents.** UA has top-level Claude Code principals (Simone, Cody, Atlas — full orchestrator instances driven by heartbeats and dispatching their own sub-agents) and helper sub-agents (the entries in `.claude/agents/<name>.md` like `csi-supervisor`, `factory-supervisor`, `evaluation-judge`). They are different. Listing `.claude/agents/` will not show Simone or Cody — that does not mean they're missing. Simone's directive file is `memory/HEARTBEAT.md`. Cody runs as her downstream task executor via Task Hub. Diagnose presence of a principal by checking heartbeat sessions / daemon registration, not by `ls .claude/agents/`.

**These rules apply to every PR and every "phase complete" claim:**

1. **Skill deployed ≠ skill invoked.** A skill file in `.claude/skills/<name>/` does nothing on its own. Before declaring a phase complete, prove that *some invoker* in production is pointing at the skill by name:
   - **Sub-agent invocation:** `grep -l <skill-name> /opt/universal_agent/.claude/agents/*.md` — must return at least one sub-agent definition.
   - **Principal heartbeat invocation (Simone, Cody, Atlas):** `grep -n <skill-name> /opt/universal_agent/memory/HEARTBEAT.md` — must return at least one directive.
   - **Task Hub-mediated invocation:** some producer enqueues a task type whose handler invokes the skill, AND the consumer principal's directives tell it to claim that task type. Both ends required.

   At least one check must pass. If none do, the skill is dead code regardless of how many tests exercise it directly.

2. **Phase complete = real artifact on real disk.** Not complete until a representative real-world artifact exists at the expected VPS path. Examples: a `cody_demo_task` row in Task Hub created by a non-test run; a `/opt/ua_demos/<id>/manifest.json` with `endpoint_hit=anthropic_native`; a vault entity page authored by a non-mocked Simone run. "Mechanical end-to-end loop synthesized in-memory" is NOT verification.

3. **Diagnostic commands must read the canonical resolver, not your guess.** Path resolution lives in code (e.g. `artifacts.py:resolve_artifacts_dir`). Read the resolver function before scripting a `find` or `ls`. Do not invent fallback paths.

4. **No conflation of code paths under similar names.** "URL allowlist" exists in three different files for three different purposes (research grounding, csi_url_judge pre-filter, csi_url_judge LLM judge). Before you say "the allowlist blocks X," follow the call chain from the actual call site. Use grep on the call site, not on the term.

5. **Prove your claim before stating it.** When asserting how the system behaves ("Task Hub queueing is per-handle"), open the function that does the gating and read the body. Function names lie; bodies don't. If you don't have time to read the body, say "I think X but haven't confirmed" — never assert.

6. **End-of-PR production smoke is mandatory for any PR that touches a phase boundary** (work whose value depends on a downstream agent picking up its output). The shakedown log format is fine; "smoke deferred to operator" must not become permanent. If smoke can't run from the dev box, schedule it on the VPS within 24h of merge AND record the result back in the PR thread.

7. **Sandbox honesty.** When working from a sandbox that can't SSH the VPS, say so up front. Don't loop the operator through 5 incremental commands when one consolidated command would do. Don't claim "I checked" when you can't.

8. **Branch-versus-deploy honesty.** A commit on a feature branch is not deployed. A commit merged to `main` is not deployed if the GitHub Actions deploy hasn't completed. Never say "the fix is shipped" until the deploy workflow is green AND the live VPS state confirms the change took effect (Rule A: `/api/v1/version` SHA check).

For the **Ship-then-Verify cadence (Rules A–D)** — `/api/v1/version` SHA check, backend-logic vs. UI-rendering verification paths, full ship-then-verify sequence, deploy-restart guarantee — see [`docs/03_Operations/130_Production_Verification_Rules.md`](docs/03_Operations/130_Production_Verification_Rules.md). If your work touches gateway endpoints, DB queries, scoring logic, or service-layer code AND you want end-to-end browser confirmation, read that doc first.

If a rule above isn't satisfiable for a given PR, say so explicitly in the commit message and the SHIP_HANDOFF, with a specific operator step to close the gap. Acceptable failure mode: "shipped the code change but Phase 2 wiring still needs a Simone agent file deployed — see Followup #1." Unacceptable failure mode: silence.

## Documentation Maintenance — MANDATORY

All documentation for this project MUST reside exclusively within the `docs/` directory. Creating any other documentation directories (such as `OFFICIAL_PROJECT_DOCUMENTATION/`) is **strictly prohibited**.

**Documentation updates are part of the implementation itself — not optional follow-up work.** When you make code changes that affect system behavior, architecture, routing, protocols, or configuration, the doc update is a deliverable of the same work unit. Architecture drift caused by undocumented changes is worse than a minor redundant doc update.

**Mandatory steps, in order:**

1. **Identify affected docs BEFORE coding.** Consult `docs/README.md` AND `docs/Documentation_Status.md` to find which existing documents cover the area you're changing. Read them so you understand the documented contract before you touch the code.
2. **Update over create.** If a doc already exists for your topic, update the existing file rather than creating a new one. Do not create a peripheral parallel doc.
3. **Update the canonical source-of-truth doc first.** Email routing → `82_Email_Architecture`. VP delegation → `03_VP_Workers_And_Delegation`. Task Hub → `107_Task_Hub_Master_Reference`. Always the canonical, not a peripheral reference.
4. **Update during implementation, not after.** Treat doc updates as a deliverable of the same PR, not a separate ticket. A PR that ships code without the corresponding canonical-doc update is incomplete.
5. **Include visual artifacts.** Mermaid diagrams, routing tables, and code-verified citations (`file:///path#Lnnn`) — not just prose paragraphs.
6. **Update BOTH indexes.** Any new doc MUST be linked from `docs/README.md` AND `docs/Documentation_Status.md`. Updates to existing docs should bump the "last updated" timestamp in `Documentation_Status.md`.
7. **No unindexed files.** No document exists in `docs/` without being linked from one of the two index files. If you find an unindexed file, either index it or delete it.
8. **When in doubt, update.** If you are unsure whether a change is "significant enough" to warrant a doc update, it is.

**Adherence check (apply to your own PR before requesting review):**

- [ ] Did I read the canonical doc(s) for the area I touched, before coding?
- [ ] Did I update the canonical doc in the same PR as the code change?
- [ ] If I created a new doc, did I add links to BOTH `docs/README.md` and `docs/Documentation_Status.md`?
- [ ] Did I bump the `Last updated` timestamp on every doc I edited?
- [ ] Does my doc update include diagrams / tables / code citations, not just prose?

A PR that ships code while failing any of these is shipping architecture drift.

## Implementation Plan Quality Standards

Plans MUST include Mermaid diagrams (sequence + flowchart for multi-component flows), code-verified citations (`file:///path#Lnnn`), summary tables, concrete code snippets for every proposed modification (not pseudocode), and phase-by-phase boundaries between config/code/prompt changes. Visual artifacts catch the misunderstandings that paragraphs hide. Full standards: [`docs/03_Operations/131_Implementation_Plan_Quality_Standards.md`](docs/03_Operations/131_Implementation_Plan_Quality_Standards.md).

## Codex-Specific Rules

Codex / OpenAI / Antigravity agents: see [`AGENTS.md`](AGENTS.md) for PR-review and browser-debugging rules. Claude Code can ignore.

## Caveats
- _(Living section — add caveats as we discover them.)_
- Deployment is automated via GitHub Actions: a push to `main` (via merged PR) triggers the single production deploy workflow. `develop` retired 2026-05-10 — see [`04_Branching_And_Release_Workflow`](docs/06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md). Do not use ad hoc scripts, `ssh`, `rsync`, or `git pull`. See [AI Coder Instructions](docs/deployment/ai_coder_instructions.md) for the full protocol.
