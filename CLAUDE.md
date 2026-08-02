# CLAUDE.md

Quick working context for Claude (and other coding agents) in this repository.

> **Canonical documentation lives in [`project_docs/`](project_docs/README.md)** — the single index, rebuilt code-first (symbol-based citations, `code_paths` frontmatter, CI-audited via `scripts/doc_audit.py`). Editing rules: [`project_docs/CLAUDE.md`](project_docs/CLAUDE.md). The former `docs/` tree is archived (on disk, excluded from search via `.rgignore`) — do not read or link it as current. For daily work start with the [Agent Operating Playbook](project_docs/08_operations/01_agent_operating_playbook.md).

## Project Description

`universal_agent` is a Python agent runtime and orchestration project: agent execution and orchestration under `src/universal_agent/`, canonical docs under `project_docs/`, feature flags and scheduler controls via `.env`.

## Code-Verified Answers

When answering questions about how this system works — architecture, data flows, service interactions, agent pipelines, or any behavioral claim — **read the actual source code first**. Do not answer from memory or general knowledge.

1. **Read before you speak.** Open the relevant source before forming an answer.
2. **Cite what you find.** Reference `file.py::symbol` — never line numbers (they rot). If you can't point to code, say "I need to check the code."
3. **Never fabricate pipeline steps.** The multi-agent pipelines (email triage, heartbeat dispatch, daemon sessions, VP orchestration) have specific intermediaries, classifiers, and routing. Don't simplify or omit steps you haven't verified.
4. **Distinguish knowing from inferring.** State verified facts with confidence; flag extrapolation explicitly.
5. **When in doubt, investigate more.** A confident wrong answer about agent pipelines or session lifecycle leads to flawed design decisions downstream.

## LLM-Native Intelligence Design

When designing intelligence, briefing, ideation, curation, or pattern-detection features, prefer LLM reasoning over custom Pythonic pseudo-reasoning unless scale, latency, auditability, or determinism clearly require code.

1. **Code collects and preserves evidence** — raw facts, sources, timestamps, links, tags, state, ownership, retrieval metadata.
2. **Code gates and protects execution** — safety boundaries, budget/concurrency limits, dedup, auth, irreversible actions, promotion into Task Hub work.
3. **LLMs synthesize meaning** — when the corpus fits a briefing or retrieval context, let the LLM infer themes, neglected opportunities, recurring blockers, recommended actions.

Preferred briefing pattern:

`raw records -> durable knowledge blocks -> bounded retrieval context -> LLM synthesis -> gated action candidates`

Required briefing behavior: when recent knowledge blocks include surfaced ideas, repeated warnings, stalled work, or recurring observations, the briefing LLM should explicitly assess whether a pattern or opportunity is emerging, and propose any warranted action through existing gates rather than creating uncontrolled work.

## Runtime vs Development Environment Contract

**universal_agent RUNS on the VPS. The desktop is only Kevin's interactive cockpit.**

- **The VPS** (`uaonvps`, `/opt/universal_agent`, runtime user `ua`, always-on) is the **single runtime host**. Every deployed service, systemd timer/unit, cron, worker, scheduler, and database lives there. Anything continuous or scheduled is a `deployment/systemd/` unit shipped via the pipeline: merge to `main` → `deploy.yml` → `scripts/deploy/remote_deploy.sh` installs the units (`scripts/install_vps_*.sh`) and restarts the stack.
- **The desktop** (`mint-desktop`, user `kjdragan`) is the **interactive development cockpit — nothing more**. Kevin develops by running Claude Code there via `claudereal` (→ `scripts/claude_with_mcp_env.sh`). This is fully supported; **never require SSHing into the VPS to develop.**
- **Nothing operational ever runs on the desktop.** No `systemctl --user` UA timers/services, no cron, no long-running workers. SSHFS bridges files both ways, so host location never limits file access. If something must run continuously or on a schedule, build it as a `deployment/systemd/` unit for the VPS — **never** `systemctl --user enable` a `ua-*`/`universal-agent-*` unit or run a per-user installer (`scripts/install_*_timer.sh`, `scripts/install_*_user_service.sh`) on the desktop. A PreToolUse guard (`.claude/hooks/guard-no-timer-install.sh`, wired from the tracked `.claude/settings.json` so it is live in worktrees and fresh clones too) denies these mechanically; it self-disables on the VPS runtime host (where these installs are correct), and the canonical VPS root installers (`install_vps_*`) are exempt everywhere.

## Cross-Machine File Resolution (SSHFS)

A transparent file-resolution bridge over Tailscale means **host location never limits file access** — desktop and VPS resolve each other's `/home/kjdragan/...` paths at the same absolute path. This is file access **only**; where work *runs* is governed by the contract above.

- **Runtime user on the VPS** is system user `ua` (not `kjdragan`) — reach it with `ssh ua@uaonvps`. There is no `kjdragan` account on the VPS; `/home/kjdragan/...` is an SSHFS mount `ua` reads through. Global `~/.claude/CLAUDE.md` for VPS-side sessions lives at `/home/ua/.claude/CLAUDE.md`.
- **The path guarantee:** desktop `/home/kjdragan/...` is mounted on the VPS at the same path; the VPS is mounted on the desktop at `/home/kjdragan/mnt/vps`.
- **Never** build custom file-fetcher or syncing tools to move files between them — refer to the absolute path directly and standard OS operations resolve it.

## Tailnet HTML Scratchpad

The mechanism this repo owns (`scripts/publish_scratch.sh`, `src/universal_agent/services/scratch_publish.py`) is documented canonically in [`project_docs/06_platform/06_networking_tailscale_proxy_sshfs.md`](project_docs/06_platform/06_networking_tailscale_proxy_sshfs.md) § 1.6; agents use the **`publish-to-scratchpad` skill**. Read one of those before publishing.

- **REQUIRED after every interactive (desktop) publish:** commit the `scratch_archive/` entry (`git add scratch_archive/`, branch → PR). The archiver writes the durable copy but does not commit, and this is the step most often skipped. `scratch_archive/**` is `paths-ignore`d in `deploy.yml`, so it never restarts prod. Commit only your own entry.

## Secrets, Infisical & gws/Gmail auth

- Full contract: [`project_docs/06_platform/01_secrets_and_infisical.md`](project_docs/06_platform/01_secrets_and_infisical.md). TL;DR: agents have machine-id creds pre-loaded — fetch secrets yourself with `infisical run` (universal-auth, never the interactive CLI session); **never print secret values**; never `set`/delete/rotate without operator approval; UA Python services use `initialize_runtime_secrets()`, not the CLI.
- **gws (Google Workspace CLI) auth on the VPS** — the ~weekly OAuth re-auth runbook and headless verification: [`project_docs/05_channels/01_email_agentmail.md`](project_docs/05_channels/01_email_agentmail.md) § "gws CLI auth on the VPS".

## VPS Autonomy

You have direct SSH access as `ua@uaonvps`, full SSHFS at `/home/kjdragan/...`, and **passwordless sudo** on the VPS (`/etc/sudoers.d/ua-nopasswd`) — so restarting services, editing and reloading nginx, and managing `tailscale serve` all work non-interactively over SSH.

**Do not hand the operator commands you can run yourself.** Asking "should I run X?" for a non-destructive, scriptable command burns operator time and is disallowed. Diagnostic reads, log tails, manual cron triggers, status checks, restarting `universal-agent-gateway` after your own change: just do them and narrate the result.

Stop and ask first only for: destructive operations (deletes, force-pushes, dropping data), operations incurring real unauthorized cost, anything mutating external accounts (Google, GitHub, Stripe), or operations that could leak production secrets into the transcript.

## Git Workflow

- Read [`project_docs/06_platform/04_deployment_and_cicd.md`](project_docs/06_platform/04_deployment_and_cicd.md) before your first commit — branch discipline, commit conventions, `/ship` handoff, deploy pipeline.
- **Branch model:** any branch → PR → `main` → deploy. `main` is the only home base; **never push directly to it.**
- **Always branch from a freshly-fetched `origin/main`**: `git fetch origin && git checkout -b <name> origin/main`. Never a bare `git checkout -b` off local `main` — this desktop checkout drifts behind origin, and a stale base can revert newer commits when your PR squash-merges. (`EnterWorktree` creates `worktree-claude+<task>` branches with a `fresh` base of `origin/main`; still `git fetch` first.)
- **Branch naming:** Claude Code work `claude/<task>`, Codie work `codie/<task>`, operator work `kevin/<task>` or `feature/<task>`. `pr-auto-merge.yml` auto-enables auto-merge for all non-draft PRs EXCEPT `codie/*`, `kevin/*`, and `feature/*`, which need manual review.
- PRs are gated by [`.github/workflows/pr-validate.yml`](.github/workflows/pr-validate.yml) — `py_compile` on changed `.py`, `ruff check`, `pytest tests/unit`, and a `.py.bak`/`.swp`/`.orig` tripwire. **PR-Validate is the only pre-deploy gate. Don't merge red.**
- `deploy.yml` has `paths-ignore` for docs (`docs/`, `**.md`, `reports/`, `state/`, `artifacts/`, `memory/**`) so docs-only commits don't restart production; mixed code+docs commits still deploy. Deployment is fully automated — never use ad hoc `ssh`, `rsync`, or `git pull` to deploy.
- Auto-merge uses `AUTO_MERGE_PAT`, not `GITHUB_TOKEN` (which suppresses downstream workflow events). `deploy.yml` carries `concurrency: { group: deploy-production, cancel-in-progress: false }` so simultaneous merges queue serially instead of racing on the git index lock.

## Claude Execution Environments

UA runs **three Claude execution profiles**. Mistaking one for another is the #1 source of confusion in the system. Canonical reference — read before touching any Claude env, `settings.json`, or Anthropic-related code: [`project_docs/06_platform/05_environments.md`](project_docs/06_platform/05_environments.md); model resolution internals: [`project_docs/01_architecture/04_model_choice_and_resolution.md`](project_docs/01_architecture/04_model_choice_and_resolution.md).

1. **Kevin's interactive coding** (Antigravity terminal, IDE side panel, plain `claude`) → **Anthropic Max plan** (Opus/Sonnet/Haiku via OAuth). `claude` is aliased to `scripts/claude_with_mcp_env.sh`, which bootstraps Infisical secrets so `.mcp.json` `${VAR}` placeholders resolve, strips `ANTHROPIC_*` so OAuth wins, and auto-injects `--dangerously-skip-permissions` for interactive sessions (skipped for management subcommands like `claude agents`).
2. **UA autonomous principals running in-process** (Simone heartbeats, Atlas, dispatch sweep, ClaudeDevs intel cron) → **ZAI proxy / GLM models**. ZAI vars are injected at service start by `initialize_runtime_secrets()` reading Infisical, NOT via user-global `~/.claude/settings.json`. Heartbeat-driven, continuous, no per-task model switch — their `ANTHROPIC_*` env routes to ZAI by design.
3. **Cody / all VPs — per-task CLI subprocesses (both demo workspaces and in-environment work)** → **ZAI (GLM) by default** (`services/cody_mode.py::_HARDCODED_FALLBACK_MODE = "zai"`; `vp/profiles.py` `VpProfile.inference_mode = "zai"` for CODIE/ATLAS/HOMER), because Anthropic API-bills the Claude-Code-via-Max SDK path. Every Cody task carries a `cody_mode` resolved by `cody_mode.py::resolve_cody_mode` in priority: (a) per-task override on `task_hub_items.cody_mode`, (b) per-VP DB setting, (c) global DB setting `cody_default_mode` (dashboard-flippable), (d) `UA_CODY_DEFAULT_MODE`, (e) `VpProfile.inference_mode`, (f) hardcoded `"zai"`. When a task is explicitly opted into `"anthropic"`, `vp/clients/claude_cli_client.py::_build_cli_env` strips every `ANTHROPIC_*` var from the subprocess so OAuth wins and force-enables `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`. That opt-in applies to **all** Cody work, not just `/opt/ua_demos/<id>/` workspaces; demo workspaces needing Anthropic add a second layer via a vanilla `.claude/settings.json` in `src/universal_agent/templates/ua_demos_scaffold/`.

**Common mistakes:**
- ❌ "Cody defaults to Anthropic Max." Wrong — Cody and all VPs default to **ZAI**. Anthropic Max is the **opt-in** (per-task `cody_mode="anthropic"`, per-VP, dashboard tile, or `UA_CODY_DEFAULT_MODE=anthropic`).
- ❌ "Anthropic Max is the default for `/opt/ua_demos/` workspaces." Wrong — those default to ZAI too.
- ❌ "`claude agent` (singular) opens a new agent UI." The command is `claude agents` (plural); the singular is parsed as a prompt argument.

## Working Rules

- **Local dev happens on Kevin's desktop, not the VPS.** Spin up the stack with `just dev` from `/home/kjdragan/lrepos/universal_agent/`. Autonomous loops (heartbeat, cron, dispatch sweep, AgentMail polling) are OFF in dev by default — set `UA_DEV_<NAME>_FORCE_ON=1` in `.env` to opt a loop in for testing. The VPS is production-only.
- Keep changes small and targeted; prefer root-cause fixes over workarounds.
- Do not commit secrets, credentials, or local state files.
- Update docs when behavior or operations change.

## Operating Hours / Dormancy Default

**Active window: 6:00 AM – 10:00 PM Houston time. Dormant: 10:00 PM – 6:00 AM.**

Applies to **content-generation** work only — cron jobs, polling loops, and scheduled GHA workflows that burn quota producing intelligence nobody reads until morning. Use `default_timezone="America/Chicago"` (or `TZ=America/Chicago`) so DST handles itself; GitHub Actions schedules are UTC-only — express in UTC and accept the 1h DST drift.

Dormancy does **NOT** apply to infrastructure-event handlers — deploy workflows, auto-merge, CI/PR failure handling, error alerting. Those run 24/7 because a merge or CI failure can land at any hour and silently broken production until 6 AM is unacceptable. Event-driven GHA workflows (`push`/`pull_request`/`workflow_run`) are not subject to dormancy mechanically either.

**Adding a new cron:** classify by schedule shape. **Interval** crons (`*/N`, hourly ranges) respect dormancy — window into 6-21, or run 24/7 via `DOCUMENTED_EXCEPTIONS` or the per-job `UA_<JOB>_24_7` opt-out. **Fixed-time** crons (a single/few discrete times) run as scheduled. Full rules: [`project_docs/08_operations/03_dormancy_and_operating_hours.md`](project_docs/08_operations/03_dormancy_and_operating_hours.md). Guard test: `tests/unit/test_cron_dormancy_defaults.py`.

## Pre-Implementation Reading — DO NOT SKIP

**The rule:** before proposing new logic for any verb below, grep for it in the canonical service module. If a function exists, compose with it. If you can't tell whether something exists, you have NOT done your reading and should NOT propose a change yet.

Postmortem context and the full anti-patterns catalog: [`project_docs/08_operations/02_production_verification_rules.md`](project_docs/08_operations/02_production_verification_rules.md).

| If you're about to propose | Read first |
|---|---|
| Task claiming, routing, atomic dispatch, concurrency cap, queue rebuild, dedup | `services/dispatch_service.py` + `task_hub.py`. Heartbeats call `dispatch_sweep` → `claim_next_dispatch_tasks(limit=N)`; every claimed task auto-routes to Simone via `route_all_to_simone`. |
| Stale / orphaned in-progress task recovery | `task_hub.py` — `UA_TASK_STALE_ENABLED` / `UA_TASK_STALE_MIN_AGE_MINUTES`. Don't write a per-task reaper. |
| Adding/changing an agent-execution timeout (turn kill, mission reap, no-progress) | Use the idle / no-progress watchdog — **never a hard wall-clock cap** (one killed live Simone work turns). `timeout_policy.py::LivenessWatchdog` is the ONE shared policy, used by `execution_engine.py::ProcessTurnAdapter.execute`, `vp/clients/base.py::consume_adapter_events_with_idle_timeout`, and `vp/clients/claude_cli_client.py::_monitor_cli_output`. See [`project_docs/02_execution_core/01_gateway_sessions_execution.md`](project_docs/02_execution_core/01_gateway_sessions_execution.md) § "Liveness / no-progress timeout". |
| Cron registration (system jobs) | `gateway_server._register_system_cron_job` — handles catch-up, secrets, update-vs-create. Do not hand-roll. |
| **A new cron / scheduled task / webhook handler / demo workspace consumer / any async unit of work** | **[`project_docs/02_execution_core/02_task_hub.md`](project_docs/02_execution_core/02_task_hub.md)** — six-rule observability protocol (identity / claim ledger / run history / subprocess identity / protocol-violation routing / standard recovery verbs). Use `ensure_cron_task_link` + `_open_run` + `classify_worker_exit` + `_close_run` + `park_task_for_protocol_violation`. Default `skip_task_hub_link=False`; only opt out for pure no-state event handlers. |
| **Spawning a subprocess that itself spawns children (`uv run …`, the `claude` CLI, any wrapper) with a timeout** | **Never bare `subprocess.run(argv, timeout=N)`** — on timeout CPython kills ONLY the direct child; grandchildren orphan to PID 1 but **stay in the systemd unit's cgroup** (membership is inherited at fork and does not change on re-parenting), still charged against `MemoryMax` and outliving the main process. That single defect caused a `result=oom-kill` and a `result=timeout` on the nuggets lane. Use `Popen(..., start_new_session=True)` + `os.killpg(SIGTERM→SIGKILL)`; copy `services/proactive_demo_nuggets.py::_kill_build_process_group`. |
| Adding a new VPS systemd unit / installer | The installer list in `scripts/deploy/remote_deploy.sh` is **enumerated, not auto-discovered** — a new `scripts/install_vps_*.sh` that isn't wired in there never runs, and the missing unit looks exactly like "the timer isn't firing". |
| Instrumenting a `Type=oneshot` unit's resource use | Its cgroup is **destroyed on exit**, so `memory.events` / `memory.pressure` are unreadable post-hoc and `MemoryPeak` is a *clamp* reading when the job sits on its limit. Capture with `ExecStopPost=` (runs inside the still-live cgroup, and on abnormal exits too) — see `scripts/nuggets_cgroup_postmortem.sh`. |
| Artifact path resolution | `artifacts.py:resolve_artifacts_dir` — default `<repo-root>/artifacts`, NOT `AGENT_RUN_WORKSPACES`. Read this before any `find` or `ls` diagnostic. |
| URL fetching for CSI / linked-source enrichment | `services/csi_url_judge.enrich_urls` — three passes (pre-filter → LLM judge → fetch). `trust_source=True` bypasses the judge for official-handle lanes. |
| Research grounding (open-web search restricted to official sources) | `services/research_grounding.is_allowed` — separate path from the URL judge. `research_allowlist` in `intel_lanes.yaml` gates only this path, NOT tweet-link fetching. |
| Skill invocation by a principal | The skill's `SKILL.md` is canonical. Don't re-document it in `HEARTBEAT.md`. |
| Storing/loading any application secret | [`project_docs/06_platform/01_secrets_and_infisical.md`](project_docs/06_platform/01_secrets_and_infisical.md) — Infisical is the single source of truth. Call `initialize_runtime_secrets()` at startup; never read secrets from `.env`/`os.getenv` except for Infisical bootstrap creds. |
| Adding/touching `.mcp.json` (esp. `env.*`) | **Every value MUST be a `${VAR}` placeholder, never a literal token.** Resolution via `scripts/claude_with_mcp_env.sh`. `infisical run` CLI is the WRONG primitive — no headless auth context on the VPS. |
| Web-fetch / search tool selection (autonomous vs interactive vs demo) | Autonomous → ZAI MCPs (`webReader`, `webSearchPrime`, `zai-mcp-server` vision). Interactive/demo → Claude built-ins (`WebFetch`, `WebSearch`). Calling ZAI MCPs from interactive/demo burns ZAI quota for an Anthropic-side use. |
| Using/evaluating **GLM-5.2** (ZAI opus-tier candidate) or any `thinking` param on a ZAI model | **GLM-5.1 had no thinking; GLM-5.2 defaults thinking ON** (`thinking:{type:enabled\|disabled\|auto}`). Wire id is bare `glm-5.2` (`[1m]` 400s); it mis-buckets to sonnet so pass `model_tier="opus"`; thinking is 10-24× the tokens so use `{type:disabled}` for cheap crons and always set `budget_tokens` when on. Eval harness: `python -m universal_agent.scripts.glm52_probe`. |

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

**Principals vs. sub-agents.** UA has top-level Claude Code principals (Simone, Cody, Atlas — full orchestrator instances driven by heartbeats, dispatching their own sub-agents) and helper sub-agents (entries in `.claude/agents/<name>.md` like `csi-supervisor`, `factory-supervisor`, `evaluation-judge`). Listing `.claude/agents/` will not show Simone or Cody — that does not mean they're missing. Simone's directive file is `memory/HEARTBEAT.md`; Cody runs as her downstream task executor via Task Hub. Diagnose a principal's presence by checking heartbeat sessions / daemon registration, not by `ls .claude/agents/`.

**These rules apply to every PR and every "phase complete" claim:**

1. **Skill deployed ≠ skill invoked.** A skill file in `.claude/skills/<name>/` does nothing on its own. Before declaring a phase complete, prove some invoker in production points at it by name:
   - **Sub-agent:** `grep -l <skill-name> /opt/universal_agent/.claude/agents/*.md` — must return at least one definition.
   - **Principal heartbeat (Simone, Cody, Atlas):** `grep -n <skill-name> /opt/universal_agent/memory/HEARTBEAT.md` — must return at least one directive.
   - **Task Hub-mediated:** a producer enqueues a task type whose handler invokes the skill, AND the consumer principal's directives tell it to claim that task type. Both ends required.

   At least one check must pass, or the skill is dead code regardless of how many tests exercise it directly.

2. **Phase complete = real artifact on real disk.** Not complete until a representative real-world artifact exists at the expected VPS path — a `cody_demo_task` row created by a non-test run, a `/opt/ua_demos/<id>/manifest.json` with `endpoint_hit=anthropic_native`, a vault entity page authored by a non-mocked Simone run. "Mechanical end-to-end loop synthesized in-memory" is NOT verification.

3. **Diagnostic commands must read the canonical resolver, not your guess.** Path resolution lives in code (e.g. `artifacts.py:resolve_artifacts_dir`). Read the resolver before scripting a `find` or `ls`. Do not invent fallback paths.

4. **No conflation of code paths under similar names.** "URL allowlist" exists in three files for three purposes (research grounding, csi_url_judge pre-filter, csi_url_judge LLM judge). Before saying "the allowlist blocks X," follow the call chain from the actual call site.

5. **Prove your claim before stating it.** When asserting how the system behaves, open the function that does the gating and read the body. Function names lie; bodies don't. If you don't have time, say "I think X but haven't confirmed" — never assert.

6. **End-of-PR production smoke is mandatory for any PR touching a phase boundary** (work whose value depends on a downstream agent picking up its output). "Smoke deferred to operator" must not become permanent. If smoke can't run from the dev box, schedule it on the VPS within 24h of merge AND record the result in the PR thread.

7. **Sandbox honesty.** When working from a sandbox that can't SSH the VPS, say so up front. Don't loop the operator through 5 incremental commands when one consolidated command would do. Don't claim "I checked" when you can't.

8. **Branch-versus-deploy honesty.** A commit on a feature branch is not deployed. A commit merged to `main` is not deployed until the GitHub Actions deploy completes. Never say "the fix is shipped" until the deploy workflow is green AND live VPS state confirms it (Rule A: `/api/v1/version` SHA check).

For the **Ship-then-Verify cadence (Rules A–D)** — `/api/v1/version` SHA check, backend-logic vs. UI-rendering verification paths, deploy-restart guarantee — see [`project_docs/08_operations/02_production_verification_rules.md`](project_docs/08_operations/02_production_verification_rules.md). Read it first if your work touches gateway endpoints, DB queries, scoring logic, or service-layer code and you want end-to-end browser confirmation.

If a rule isn't satisfiable for a given PR, say so explicitly in the commit message and the SHIP_HANDOFF, with a specific operator step to close the gap. Acceptable: "shipped the code change but Phase 2 wiring still needs a Simone agent file deployed — see Followup #1." Unacceptable: silence.

## Documentation Maintenance

Canonical docs live in `project_docs/`. The full editing contract — taxonomy, required frontmatter, the symbol-reference citation convention, create-vs-update rule, CI enforcement — lives in [`project_docs/CLAUDE.md`](project_docs/CLAUDE.md) and lazy-loads when you work under `project_docs/`.

The non-negotiables for **every** PR:

1. **Code is the source of truth.** A doc describes what the code does *now*; if they disagree, the doc is wrong.
2. **Doc updates ship in the same PR as the behavior change** — not a follow-up ticket. A code PR without the matching canonical-doc update is incomplete.
3. **Update the canonical doc, don't spawn a parallel one.** One canonical doc per subsystem; check the index first.
4. **Cite with symbols (`file.py::symbol`), never line numbers.** A new subsystem doc also gets a `README.md` index entry in the same change. CI (`scripts/doc_audit.py`) enforces frontmatter, symbol-ref resolution, and the no-line-number rule.
5. **Discover the docs you must touch by reverse-lookup, not memory.** Find the canonical docs owning the code you changed via their `code_paths:` frontmatter and update each in **this** PR. The nightly sweep (`scripts/doc_accuracy_sweep.py`) is a backstop to catch what slips through, not the mechanism — never defer to it.
   ```bash
   git diff --name-only origin/main...HEAD | while read f; do
     grep -rl -e "$f" -e "$(dirname "$f")" project_docs --include='*.md'
   done | sort -u
   ```
   Every doc owning changed behavior gets updated now — or gets a one-line "unaffected because…" in the PR description.

## Implementation Plan Quality Standards

Plans MUST include Mermaid diagrams (sequence + flowchart for multi-component flows), symbol-based code citations, summary tables, concrete code snippets for every proposed modification (not pseudocode), and phase-by-phase boundaries between config/code/prompt changes. Visual artifacts catch the misunderstandings that paragraphs hide.

## Codex-Specific Rules

Codex / OpenAI / Antigravity agents: see [`AGENTS.md`](AGENTS.md) for PR-review and browser-debugging rules. Claude Code can ignore.

## Caveats

- _(Living section — add caveats as we discover them.)_
