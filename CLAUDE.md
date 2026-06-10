# CLAUDE.md

This file provides quick working context for Claude (and other coding agents) in this repository.

> ## 📚 Canonical documentation lives in [`project_docs/`](project_docs/README.md)
> As of **2026-05-29** the documentation was rebuilt **code-first** (every doc reconstructed from
> source, symbol-based citations, `code_paths` frontmatter, CI-audited via `scripts/doc_audit.py`).
> **Start at [`project_docs/README.md`](project_docs/README.md)** — the single index. Editing rules
> are in [`project_docs/CLAUDE.md`](project_docs/CLAUDE.md). The former `docs/` tree is **archived**
> (kept on disk, excluded from default searches via `.rgignore`); do not read or link it as current.
> The pointers throughout this file have been repointed to their `project_docs/` equivalents.

> 👉 **Start here for daily work:** [`project_docs/README.md`](project_docs/README.md) is the doc index; the [Agent Operating Playbook](project_docs/08_operations/01_agent_operating_playbook.md) is the how-to-operate guide. Local dev + Claude execution environments: [`project_docs/06_platform/05_environments.md`](project_docs/06_platform/05_environments.md).

## Project Description
`universal_agent` is a Python-based agent runtime and orchestration project.

It includes:
- Agent execution and orchestration logic under `src/universal_agent/`
- Canonical documentation under `project_docs/`
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
2. **Cite what you find.** Reference specific files and symbols (`file.py::symbol`) that support your explanation — never line numbers (they rot). If you cannot point to actual code, say "I need to check the code" — do not guess.
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

## Runtime vs Development Environment Contract (READ FIRST)

**universal_agent RUNS on the VPS. The desktop is only Kevin's interactive cockpit.**

- **The VPS** (`uaonvps`, `/opt/universal_agent`, runtime user `ua`, always-on) is the **single runtime host**. Every deployed service, systemd timer/unit, cron, worker, scheduler, and database lives and runs there. Anything continuous or scheduled is a `deployment/systemd/` unit shipped via the pipeline: merge to `main` → `deploy.yml` → `scripts/deploy/remote_deploy.sh` auto-installs the units (the `scripts/install_vps_*.sh` installers) and restarts the stack.
- **The desktop** (`mint-desktop`, user `kjdragan`) is Kevin's **interactive development cockpit — nothing more**. He develops by running Claude Code on the desktop via `claudereal` (→ `scripts/claude_with_mcp_env.sh`, the Profile-1 "Interactive coding" launcher). This is fully supported; **never require SSHing into the VPS to develop.**
- **Nothing operational ever runs on the desktop.** No `systemctl --user` UA timers/services, no cron, no long-running workers. SSHFS bridges files both ways (below), so host location never limits file access — there is never a reason to run a UA service on the desktop. If you need something to run continuously or on a schedule, build it as a `deployment/systemd/` unit and deploy it to the VPS — **never** `systemctl --user enable` a `ua-*`/`universal-agent-*` unit or run a per-user installer (`scripts/install_*_timer.sh`, `scripts/install_*_user_service.sh`) on the desktop. A desktop-only PreToolUse guard (`.claude/hooks/guard-no-timer-install.sh`, wired from the gitignored `.claude/settings.local.json`) denies these mechanically; the canonical VPS root installers (`install_vps_*`) are exempt.
- Canonical environment reference (the three execution profiles, per-machine matrix): [`project_docs/06_platform/05_environments.md`](project_docs/06_platform/05_environments.md).

## Cross-Machine File Resolution (SSHFS)

The Universal Agent infrastructure includes a seamless, transparent file resolution bridge via SSHFS over Tailscale, so **host location never limits file access** — the desktop and VPS resolve each other's `/home/kjdragan/...` paths at the same absolute path. This is a file-access capability **only**; where work *runs* is governed by the contract above, not by what is reachable over the mount.

- **Runtime user on the VPS**: the deployed stack and the autonomous agents that spawn the `claude` CLI run as system user `ua` (not `kjdragan`). Reach the VPS with `ssh ua@uaonvps`. There is no `kjdragan` user account on the VPS; the `/home/kjdragan/...` paths are an SSHFS mount that `ua` reads through. Global `~/.claude/CLAUDE.md` for any VPS-side `claude` session lives at `/home/ua/.claude/CLAUDE.md`. (Claude Code is installed on both machines for different reasons — the desktop for Kevin's *interactive* `claudereal` use, the VPS because the autonomous agents spawn the `claude` CLI as `ua`; not redundant.)
- **The Path Guarantee**: The local desktop path `/home/kjdragan/...` is mounted onto the VPS at `/home/kjdragan/...`, and the VPS is mounted on the desktop at `/home/kjdragan/mnt/vps`.
- **Capability Implication**: **Never** build custom "file fetcher" tools or syncing scripts to move files from the desktop to the VPS for agent tasks. Instead, simply refer to the absolute `/home/kjdragan/...` path directly. Standard OS operations (`cat`, Python `open()`, etc.) will seamlessly resolve over the SSHFS mount.
- **Architectural Tenet**: This demonstrates the core design philosophy of "expanding system capabilities at the OS level" rather than building complex, brittle agent workarounds.

## Tailnet HTML Scratchpad — how to hand the operator a rendered report

Kevin runs Claude Code terminal-only. Markdown shows as raw text, and HTML/PDF email attachments get their links + anchors stripped. **When you produce a report, analysis, diff review, or anything that benefits from real HTML rendering (styling, Mermaid/SVG diagrams, working in-page anchors), publish it to the tailnet HTML scratchpad and hand over the link** instead of pasting markdown or attaching a file.

One command does it — `scripts/publish_scratch.sh` auto-detects whether it runs on the VPS (writes directly) or anywhere else on the tailnet (copies over `ssh ua@uaonvps`), generates an unguessable slug, and prints the URL:

```bash
scripts/publish_scratch.sh report.html                       # random slug
scripts/publish_scratch.sh report.html my-analysis           # readable slug -> /scratch/my-analysis/report.html
URL=$(scripts/publish_scratch.sh report.html)                # capture URL (stdout = URL only)
scripts/publish_scratch.sh --init    # one-time/idempotent setup of the /scratch mapping
scripts/publish_scratch.sh --status  # verify mappings (must still show / -> :3000)
```

- **URL shape:** `https://uaonvps.taildcc090.ts.net/scratch/<slug>/<file>.html` — auto-HTTPS, **tailnet-only** (private to Kevin's own devices; never public). Tailnet membership is the auth boundary, not the slug.
- **Mechanism:** `tailscale serve` path-mount of `/home/ua/ua_scratch` (daemon-managed, reboot-safe; survives `/opt/universal_agent` deploys). Don't disturb the other serve mappings.
- **Workflow:** write your HTML anywhere, run the script, paste the printed URL back to Kevin. That's the whole loop for "spin up a report and give me the link."
- **Two reusable front doors (one mechanism — both wrap `publish_scratch.sh`):** for **agentic** work, invoke the **`publish-to-scratchpad` skill** (it triggers whenever you're about to paste markdown or attach a report — publish + hand over the link instead). For **deterministic Python pipelines** (cron/services, which can't invoke a skill), call `from universal_agent.services.scratch_publish import publish_html_to_scratch` — returns the URL, or `None` so you can fall back to attaching the file. The YouTube daily digest uses this helper for **link-first** delivery (PDF only as a fallback).
- Full reference (mechanism, failure signatures): `project_docs/06_platform/06_networking_tailscale_proxy_sshfs.md` § 1.6. The `visual-explainer` skill is a good way to generate the HTML itself.

## Secrets, Infisical & gws/Gmail auth

- **Self-service secret access** (machine-identity CLI pattern, guardrails, the desktop Hostinger lazy-load) and the full Infisical contract: [`project_docs/06_platform/01_secrets_and_infisical.md`](project_docs/06_platform/01_secrets_and_infisical.md). TL;DR: agents have machine-id creds pre-loaded — fetch secrets yourself with `infisical run` (universal-auth, never the interactive CLI session); **never print secret values**; never `set`/delete/rotate without operator approval; UA Python services use `initialize_runtime_secrets()`, not the CLI.
- **gws (Google Workspace CLI) auth on the VPS** — the ~weekly OAuth re-auth runbook (Testing-mode 7-day token expiry), how the 4 base64 Infisical secrets reach the VPS, and headless verification: [`project_docs/05_channels/01_email_agentmail.md`](project_docs/05_channels/01_email_agentmail.md) § "gws CLI auth on the VPS".

## Key Commands
- Install deps: `uv sync`
- Run app: `uv run python -m src.universal_agent.main`
- Run tests: `uv run pytest`
- Lint/format (if configured): `uv run ruff check .` / `uv run ruff format .`

## VPS Autonomy (MUST READ)

You have direct SSH access to the VPS as `ua@uaonvps` and full SSHFS at `/home/kjdragan/...`. **Do not hand the operator commands you can run yourself.** Asking "should I run X?" or "please run X" when X is a non-destructive, fully-scriptable command burns operator time and is explicitly disallowed.

Default to running it. Stop and ask first only for: destructive operations (deletes, force-pushes, dropping data), operations that incur real cost the operator hasn't already authorized, anything that mutates external accounts (Google, GitHub, Stripe), or operations that touch production secrets in a way that could leak credentials into the transcript.

Diagnostic reads, log tails, manual cron triggers, status checks, restarting the `universal-agent-gateway` service after a change you just made, and similar operational steps: just do them, narrate the result.

### VPS sudo + the Tailnet HTML Scratchpad (added 2026-06-01)

**`ua` has passwordless sudo on the VPS.** As of 2026-06-01, `/etc/sudoers.d/ua-nopasswd` grants `ua ALL=(ALL) NOPASSWD: ALL`. So `sudo` works non-interactively over `ssh ua@uaonvps` — restart services (`sudo systemctl restart universal-agent-gateway`), edit + reload nginx (`sudo nginx -t && sudo systemctl reload nginx`), manage `tailscale serve`, etc., **all autonomously**. Any older note in this file saying "you don't have passwordless sudo as `ua`" / "ask Kevin to `sudo`" is **obsolete** — do it yourself. (Standing guardrails still hold: confirm before destructive, outward-facing, or secret-leaking operations.)

**The Tailnet HTML Scratchpad — how to show the terminal-only operator rendered HTML.** Kevin runs Claude Code terminal-only (no IDE), and email/PDF viewers don't honor clickable links — so the reliable way to surface a rendered page (digest reports, diffs, architecture diagrams, visual-explainer pages, any HTML artifact) is to publish it to the VPS scratchpad and hand him a link:

- **Serve:** `tailscale serve --bg --set-path /scratch /home/ua/ua_scratch` (already configured; served directly by the Tailscale daemon, so it's reboot-safe with no extra process to babysit). `--set-path` / directory serves need root → use `sudo`; plain port-proxy serves work under the operator grant without sudo.
- **Host:** `uaonvps.taildcc090.ts.net` — the VPS's Tailscale MagicDNS name, auto-HTTPS. All of Kevin's devices (desktop, phone, tablet) are on this tailnet, so the links open everywhere he reads mail. Tailnet membership **is** the auth — these URLs are private to his devices and never exposed to the public internet (no capability token needed, though an unguessable subdir is good hygiene).
- **Publish:** write the HTML to an unguessable token subdir — `/home/ua/ua_scratch/<token>/<name>.html` — then give the URL `https://uaonvps.taildcc090.ts.net/scratch/<token>/<name>.html`. The scratch dir lives in `ua`'s home, so it survives `/opt/universal_agent` deploys.
- **Don't disturb** the other `tailscale serve` mappings (`/`→:3000 dashboard, `:8443`→:8002, etc.). Only ever add/modify the `/scratch` path. Verify with `tailscale serve status` after any change.

## Git Workflow (MUST READ)
- Read [`project_docs/06_platform/04_deployment_and_cicd.md`](project_docs/06_platform/04_deployment_and_cicd.md) before your first commit. It defines branch discipline, commit conventions, `/ship` handoff, and the deploy pipeline.
- **Branch model:** any branch → PR → `main` → Deploy. `develop` retired 2026-05-10. See [`project_docs/06_platform/04_deployment_and_cicd.md`](project_docs/06_platform/04_deployment_and_cicd.md).
- TL;DR: branch from a **freshly-fetched `origin/main`** — `git fetch origin && git checkout -b <name> origin/main`, **never** a bare `git checkout -b` off local `main` (this desktop checkout's local `main` drifts behind origin as PRs merge, and a stale base can revert origin's newer commits when your PR squash-merges). Per-task — Claude Code work uses `claude/<task>`, Codie work uses `codie/<task>`, operator work uses `kevin/<task>` or `feature/<task>`, push, run `/ship` (or `gh pr create --base main`). `pr-auto-merge.yml` auto-enables auto-merge for all non-draft PRs EXCEPT `codie/*`, `kevin/*`, and `feature/*` (which need manual review); CI runs; squash-merge fires `.github/workflows/deploy.yml`. `feature/latest2` retired 2026-05-13 — `main` is the home base. **Never push directly to `main`.** The `EnterWorktree` helper creates branches named `worktree-claude+<task>` rather than `claude/<task>`; these are functionally equivalent under the auto-merge allowlist (they don't match `codie/*`, `kevin/*`, or `feature/*` so they auto-merge). `EnterWorktree`'s default `fresh` base is `origin/main` — but still `git fetch` first so that ref is current.
- PRs are gated by [`.github/workflows/pr-validate.yml`](.github/workflows/pr-validate.yml) — `py_compile` on every changed `.py`, `ruff check`, `pytest tests/unit`, and a `.py.bak`/`.swp`/`.orig` tripwire. **PR-Validate is the only pre-deploy gate.** Don't merge red.
- `deploy.yml` has `paths-ignore` for docs (`docs/`, `**.md`, `reports/`, `state/`, `artifacts/`, `memory/**`) so docs-only commits don't restart production. Mixed code+docs commits still deploy.
- Auto-merge uses `AUTO_MERGE_PAT` (not `GITHUB_TOKEN` — that suppresses downstream workflow events). `deploy.yml` has a `concurrency: { group: deploy-production, cancel-in-progress: false }` guard (added 2026-05-11) — simultaneous merges queue serially rather than racing on `/opt/universal_agent/.git/index.lock`. Full rationale: [`project_docs/06_platform/04_deployment_and_cicd.md`](project_docs/06_platform/04_deployment_and_cicd.md).

## Claude Execution Environments (MUST READ before touching anything Claude-related)
UA runs **THREE Claude execution profiles** across the VPS and Kevin's desktop. Mistaking one for another is the #1 source of confusion in the system.

1. **Kevin's interactive coding** (Antigravity terminal, Antigravity IDE side panel, plain `claude` from any shell) → **Anthropic Max plan** (real Opus/Sonnet/Haiku via OAuth). Default everywhere after the inversion plan ships. Kevin's `~/.bashrc` aliases `claude` → `scripts/claude_with_mcp_env.sh`, which bootstraps Infisical secrets so `.mcp.json` `${VAR}` placeholders resolve, strips `ANTHROPIC_*` so OAuth wins, and auto-injects `--dangerously-skip-permissions` for interactive sessions (skipped for management subcommands like `claude agents`). The new **`claude agents`** subcommand (v2.1.139+, released 2026-05-11) opens "Agent View" — a session-roster manager for parallel sessions; not a different coding interface. The flag set on `claude agents` is intentionally tiny (`--setting-sources` + `-h`) so the wrapper passes it through clean.
2. **UA autonomous principals running in-process** (Simone heartbeats, Atlas, the dispatch sweep, ClaudeDevs intel cron, etc.) → **ZAI proxy / GLM models** (cheap inference). ZAI vars are injected at service-start by `initialize_runtime_secrets()` reading Infisical, NOT via user-global `~/.claude/settings.json`. These are heartbeat-driven, run continuously inside the UA daemon, and have no per-task model switch — their `ANTHROPIC_*` env routes to ZAI by design.
3. **Cody — per-task CLI subprocesses (BOTH demo workspaces AND in-environment work)** → **Anthropic Max plan by default** as of 2026-05-11 PM (see `services/cody_mode.py::_HARDCODED_FALLBACK_MODE` — flipped from `"zai"` to `"anthropic"`). Every Cody task carries a `cody_mode` field resolved in this priority: (a) per-task override on `task_hub_items.cody_mode`, (b) DB setting `cody_default_mode` (flippable from the dashboard tile), (c) `UA_CODY_DEFAULT_MODE` env var, (d) hardcoded `"anthropic"`. When the resolved mode is `"anthropic"`, `vp/clients/claude_cli_client.py::_build_cli_env` strips every `ANTHROPIC_*` var from the spawned subprocess so OAuth wins, and force-enables `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`. This applies to **all** Cody work — not just `/opt/ua_demos/<id>/` workspaces. Demo workspaces add a second layer of defense via a vanilla `.claude/settings.json` in `templates/ua_demos_scaffold/`.

**Common past mistakes (don't repeat):**
- ❌ "Cody normally runs on ZAI" — wrong since 2026-05-11 PM. Cody defaults to Anthropic Max for every task unless explicitly overridden. The flip was an operator decision; reverting requires either a per-task `cody_mode="zai"`, the dashboard tile, or `UA_CODY_DEFAULT_MODE=zai`.
- ❌ "Anthropic Max is only for `/opt/ua_demos/` workspaces." Wrong. `_build_cli_env` scrubs `ANTHROPIC_*` for any Cody mission with `cody_mode="anthropic"` regardless of workspace location.
- ❌ "Typing `claude agent` (singular) opens a new agent UI." The real command is `claude agents` (plural). `claude agent` is parsed as a prompt argument and does nothing special.

**Canonical reference (read this FIRST before touching any Claude env, settings.json, or Anthropic-related code):** [`project_docs/06_platform/05_environments.md`](project_docs/06_platform/05_environments.md) — the three execution profiles, per-machine matrix, local dev, demo execution, model routing. Model resolution internals: [`project_docs/01_architecture/04_model_choice_and_resolution.md`](project_docs/01_architecture/04_model_choice_and_resolution.md).

## Working Rules
- **Local dev happens on Kevin's desktop, not the VPS.** Spin up the stack with `just dev` from `/home/kjdragan/lrepos/universal_agent/`. Autonomous loops (heartbeat, cron, dispatch sweep, AgentMail polling, etc.) are OFF in dev by default — set `UA_DEV_<NAME>_FORCE_ON=1` in `.env` to opt a specific loop in for testing. The VPS is production-only. Canonical runbook: [`project_docs/06_platform/05_environments.md`](project_docs/06_platform/05_environments.md). Doc index: [`project_docs/README.md`](project_docs/README.md).
- Keep changes small and targeted.
- Do not commit secrets, credentials, or local state files.
- Prefer root-cause fixes over temporary workarounds.
- Update docs when behavior or operations change.

## Operating Hours / Dormancy Default

**Active window: 6:00 AM – 10:00 PM Houston time.** **Dormant: 10:00 PM – 6:00 AM.**

Default applies to **content-generation** work only — cron jobs / polling loops / scheduled GHA workflows that burn quota to produce intelligence nobody reads until morning. Use `default_timezone="America/Chicago"` (or `TZ=America/Chicago`) so DST handles itself; GitHub Actions schedules are UTC-only — express in UTC and accept the 1h DST drift.

Dormancy does **NOT** apply to infrastructure-event handlers — deploy workflows, auto-merge, CI/PR failure handling, error alerting. Those run 24/7 because a merge can land or a CI run can fail at any wall-clock time, and silently broken production until 6 AM is unacceptable. Event-driven GHA workflows (triggered by `push`/`pull_request`/`workflow_run`) are not subject to dormancy mechanically either.

**Adding a new cron:** classify by schedule shape. **Interval** crons (`*/N`, hourly ranges) respect dormancy — window into 6-21, or run 24/7 via `DOCUMENTED_EXCEPTIONS` or the per-job `UA_<JOB>_24_7` opt-out. **Fixed-time** crons (a single/few discrete times) run as scheduled — dormancy doesn't apply. Full rules + the per-cron settings exhibit: [`project_docs/08_operations/03_dormancy_and_operating_hours.md`](project_docs/08_operations/03_dormancy_and_operating_hours.md). Guard test: `tests/unit/test_cron_dormancy_defaults.py` — enforces the window on interval crons; fixed-time crons get only an FYI.

## Pre-Implementation Reading — DO NOT SKIP

**The rule:** before you propose new logic for any of the verbs below, grep for the verb in the canonical service module. If a function already exists, compose with it. If you can't tell whether something exists, you have NOT done your reading and you should NOT propose a change yet.

Postmortem context (a near-miss and a real one) and the full anti-patterns catalog: [`project_docs/08_operations/02_production_verification_rules.md`](project_docs/08_operations/02_production_verification_rules.md).

| If you're about to propose | Read first |
|---|---|
| Task claiming, routing, atomic dispatch, concurrency cap, queue rebuild, dedup | `services/dispatch_service.py` + `task_hub.py`. Heartbeats call `dispatch_sweep` → `claim_next_dispatch_tasks(limit=N)`; every claimed task auto-routes to Simone via `route_all_to_simone`. |
| Stale / orphaned in-progress task recovery | `task_hub.py` — `UA_TASK_STALE_ENABLED` / `UA_TASK_STALE_MIN_AGE_MINUTES`. Don't write a per-task reaper. |
| Cron registration (system jobs) | `gateway_server._register_system_cron_job` — handles catch-up, secrets, update-vs-create. Do not hand-roll. |
| **A new cron / scheduled task / webhook handler / demo workspace consumer / any async unit of work** | **[`project_docs/02_execution_core/02_task_hub.md`](project_docs/02_execution_core/02_task_hub.md)** — six-rule observability protocol (identity / claim ledger / run history / subprocess identity / protocol-violation routing / standard recovery verbs). Use `ensure_cron_task_link` + `_open_run` + `classify_worker_exit` + `_close_run` + `park_task_for_protocol_violation`. Default `skip_task_hub_link=False`; only opt out for pure no-state event handlers. |
| Artifact path resolution | `artifacts.py:resolve_artifacts_dir` — default `<repo-root>/artifacts`, NOT `AGENT_RUN_WORKSPACES`. Read this before any `find` or `ls` diagnostic. |
| URL fetching for CSI / linked-source enrichment | `services/csi_url_judge.enrich_urls` — three passes (pre-filter → LLM judge → fetch). `trust_source=True` bypasses the judge for official-handle lanes. |
| Research grounding (open-web search restricted to official sources) | `services/research_grounding.is_allowed` — separate code path from the URL judge. `research_allowlist` in `intel_lanes.yaml` only gates this path, NOT tweet-link fetching. |
| Skill invocation by a principal | The skill's `SKILL.md` is canonical. Don't re-document it in `HEARTBEAT.md`. |
| Storing/loading any application secret | [`project_docs/06_platform/01_secrets_and_infisical.md`](project_docs/06_platform/01_secrets_and_infisical.md) — Infisical is the single source of truth. Call `initialize_runtime_secrets()` at startup; never read secrets from `.env`/`os.getenv` except for Infisical bootstrap creds. |
| Adding/touching `.mcp.json` (esp. `env.*`) | [`project_docs/06_platform/01_secrets_and_infisical.md`](project_docs/06_platform/01_secrets_and_infisical.md) § "MCP Server Credentials". **Every value MUST be a `${VAR}` placeholder, never a literal token.** Resolution via `scripts/claude_with_mcp_env.sh`. `infisical run` CLI is the WRONG primitive — no headless auth context on the VPS. |
| Web-fetch / search tool selection (autonomous vs interactive vs demo) | [`project_docs/06_platform/05_environments.md`](project_docs/06_platform/05_environments.md) § "Tool Surface by Execution Mode". Autonomous → ZAI MCPs (`webReader`, `webSearchPrime`, `zai-mcp-server` vision). Interactive/demo → Claude built-ins (`WebFetch`, `WebSearch`). Calling ZAI MCPs from interactive/demo burns ZAI quota for an Anthropic-side use. |

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

For the **Ship-then-Verify cadence (Rules A–D)** — `/api/v1/version` SHA check, backend-logic vs. UI-rendering verification paths, full ship-then-verify sequence, deploy-restart guarantee — see [`project_docs/08_operations/02_production_verification_rules.md`](project_docs/08_operations/02_production_verification_rules.md). If your work touches gateway endpoints, DB queries, scoring logic, or service-layer code AND you want end-to-end browser confirmation, read that doc first.

If a rule above isn't satisfiable for a given PR, say so explicitly in the commit message and the SHIP_HANDOFF, with a specific operator step to close the gap. Acceptable failure mode: "shipped the code change but Phase 2 wiring still needs a Simone agent file deployed — see Followup #1." Unacceptable failure mode: silence.

## Documentation Maintenance — MANDATORY

Canonical docs live in **`project_docs/`** (the legacy `docs/` tree is archived). The full editing
contract — taxonomy, required frontmatter, the **symbol-reference citation convention (never line
numbers)**, create-vs-update rule, and CI enforcement — lives in
[`project_docs/CLAUDE.md`](project_docs/CLAUDE.md) and lazy-loads when you work under `project_docs/`.

The non-negotiables that apply to **every** PR:

1. **Code is the source of truth.** A doc describes what the code does *now*; if they disagree, the doc is wrong.
2. **Doc updates ship in the same PR as the behavior change** — not a follow-up ticket. A code PR without the matching canonical-doc update is incomplete.
3. **Update the canonical doc, don't spawn a parallel one.** One canonical doc per subsystem; check `project_docs/README.md` (the single index) first.
4. **Cite with symbols (`file.py::symbol`), never line numbers.** A new subsystem doc also gets a `README.md` index entry in the same change. CI (`scripts/doc_audit.py`) enforces frontmatter, symbol-ref resolution, and the no-line-number rule.
5. **Discover the docs you must touch — by reverse-lookup, not memory.** When you add or change a feature, *find* the canonical docs that own the code you touched via their `code_paths:` frontmatter, and update each in **this** PR. This is the front-line step — do it while you're making the change. The nightly accuracy sweep (`scripts/doc_accuracy_sweep.py`) is a **backstop to catch what slips through, not the mechanism** — never defer a doc update to it. Build the candidate list, then confirm each against the doc's `code_paths` globs:
   ```bash
   git diff --name-only origin/main...HEAD | while read f; do
     grep -rl -e "$f" -e "$(dirname "$f")" project_docs --include='*.md'
   done | sort -u
   ```
   Every doc that owns changed behavior gets updated now — or gets a one-line "unaffected because…" in the PR description. "I'll document it later" / "the sweep will flag it" is the unacceptable failure mode.

## Implementation Plan Quality Standards

Plans MUST include Mermaid diagrams (sequence + flowchart for multi-component flows), symbol-based code citations (`file.py::symbol` — never line numbers), summary tables, concrete code snippets for every proposed modification (not pseudocode), and phase-by-phase boundaries between config/code/prompt changes. Visual artifacts catch the misunderstandings that paragraphs hide.

## Codex-Specific Rules

Codex / OpenAI / Antigravity agents: see [`AGENTS.md`](AGENTS.md) for PR-review and browser-debugging rules. Claude Code can ignore.

## Caveats
- _(Living section — add caveats as we discover them.)_
- Deployment is automated via GitHub Actions: a push to `main` (via merged PR) triggers the single production deploy workflow. `develop` retired 2026-05-10. Do not use ad hoc scripts, `ssh`, `rsync`, or `git pull`. See [`project_docs/06_platform/04_deployment_and_cicd.md`](project_docs/06_platform/04_deployment_and_cicd.md) for the full protocol.
