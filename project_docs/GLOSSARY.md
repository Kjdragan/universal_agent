---
title: Glossary
status: active
canonical: true
subsystem: meta-documentation
code_paths: []
last_verified: 2026-05-29
---

# Glossary

Project-specific terms for `universal_agent`. Generic programming vocabulary is omitted.
Each definition is grounded in current code; `file::symbol` pointers reference the implementation
(no line numbers — they drift). Verified against the codebase on the date in the frontmatter.

| Term | Definition |
| --- | --- |
| **activity_state.db** | The canonical Task Hub database, resolved at runtime via `durable/db.py::get_activity_db_path`. NOT `task_hub.db` (a stale relic). Holds task rows, assignments, execution runs, and dispatch ledger. |
| **agent_router** | The Simone-first routing layer. The legacy `qualify_agent*` functions are decommissioned; today routing is just `services/agent_router.py::route_all_to_simone` — every claimed task is handed to Simone. |
| **Anthropic-native** | An execution profile that talks to real Anthropic Claude (Opus/Sonnet/Haiku) over OAuth (Max plan), as opposed to the ZAI/GLM proxy. Used by interactive coding and, since 2026-05-11 PM, by Cody missions by default. In demo workspaces the endpoint marker is `anthropic_native` (`services/demo_workspace.py::ENDPOINT_PROFILE_ANTHROPIC`). |
| **Antigravity Remote** | Kevin's desktop IDE (a VS Code fork) used over Remote-SSH; editor runs on desktop, workspace/terminal/Claude Code run on the VPS. Now a fallback to local `just dev`. |
| **artifacts dir** | Durable output location resolved by `artifacts.py::resolve_artifacts_dir` (default `<repo-root>/artifacts`, NOT `AGENT_RUN_WORKSPACES`). Diagnostics must read the resolver, not guess. |
| **ATLAS** | The VP General Agent (`vp.general.primary`). Handles deep research, analysis, content generation, proactive intelligence (`operator_signal` tier). Distinct from CODIE. Identity injected from `prompt_assets/ATLAS_SOUL.md`. |
| **attempt** | One execution try within a run; retries create additional attempts under the same run. |
| **background (tier)** | Lowest VP mission priority and the safe default when no tier is mapped (`vp/mission_priority.py::DEFAULT_TIER`). Opportunistic work. |
| **brain transplant** | Injecting global memory/identity files into a fresh session workspace at startup so the agent boots with continuity. |
| **CODIE** | The VP Coder Agent (`vp.coder.primary`). Handles code implementation, refactoring, doc maintenance, standalone builds. The email label for coder-targeted mail is `agent-codie` (note: NOT `agent-cody`). |
| **Cody** | The per-task CLI executor persona that runs downstream of Simone via Task Hub (`services/cody_dispatch.py` enqueues a `cody_demo_task`). Each task carries a `cody_mode` (`zai`/`anthropic`) resolved by `services/cody_mode.py::resolve_cody_mode`. Cody is the executor; CODIE is the VP coder lane identity. |
| **cody_mode** | Per-task field selecting Cody's inference backend: `"zai"` (GLM proxy) or `"anthropic"` (real Claude). Hardcoded fallback flipped from `zai`→`anthropic` on 2026-05-11 (`services/cody_mode.py::_HARDCODED_FALLBACK_MODE`). |
| **convergence** | Cross-channel semantic clustering of proactive signals into actionable matches with a 1–10 signal_strength (`services/proactive_convergence.py`). The core of the CSI insight engine; an LLM precision layer refines clusters. |
| **cron service** | System-job scheduler. Registration goes through `gateway_server::_register_system_cron_job` (handles catch-up, secrets, update-vs-create) — do not hand-roll cron registration. |
| **CSI** | Creator/Convergence Signal Intelligence — the pipeline that ingests external creator signals (YouTube RSS, X/@ClaudeDevs, HN), runs a vault intelligence pass, and detects convergence (`services/csi_intelligence_pass.py`, `csi_intelligence_persistence.py`). DB resolved via `_csi_default_db_path` (split-brain hazard). |
| **demo triage** | Ranking and gating of CSI demo candidates for build-worthiness (`services/csi_demo_triage.py`, `csi_demo_triage_ranker.py`, `csi_demo_triage_policy.py`). |
| **demo workspace** | A clean per-demo dir at `/opt/ua_demos/<id>/` provisioned by `services/demo_workspace.py::provision_demo_workspace` with a vanilla scaffold, giving Cody an isolated Anthropic-native environment. Endpoint profile written as a marker file. |
| **dispatch sweep** | The heartbeat-driven loop (`services/dispatch_service.py::dispatch_sweep`) that calls `task_hub.py::claim_next_dispatch_tasks(limit=N)` to atomically claim queued tasks and route them to Simone. |
| **dormancy** | The 6 AM–10 PM Houston (`America/Chicago`) active window for content-generation crons. Infrastructure-event handlers (deploy, auto-merge, CI watchdogs) run 24/7. Guard test pins active-hour schedules. |
| **durable execution** | Crash/restart survival via persisted state, a tool-call ledger, and checkpoints (`durable/state.py`, `durable/ledger.py`, `durable/checkpointing.py`, `durable/worker_pool.py`). |
| **endpoint profile** | A marker recorded in a demo workspace declaring which inference endpoint was exercised; `anthropic_native` is canonical (`services/demo_workspace.py::read_endpoint_profile`). Used as proof-of-real-run in verification. |
| **execution run** | A tracked execution of a task with forensics (open/close, exit classification) managed by `services/execution_run_service.py` and `services/worker_exit_classifier.py`. |
| **execution session** | The live provider/runtime process attached to an active attempt (`execution_session.py`). The correct scope for the word "session" when discussing runtime execution. |
| **external vault** | A canonical markdown wiki for outside sources within the LLM Wiki; raw sources immutable, the wiki is the maintained synthesis layer. `vault_kind="external"` in `wiki/core.py::resolve_vault_path`. |
| **factory delegation / factory role** | The headquarters-vs-worker runtime model. `runtime_role.py::FactoryRole` (HEADQUARTERS / LOCAL_WORKER) and `build_factory_runtime_policy` govern what a given machine is allowed to do; the factory heartbeat/registry tracks worker liveness. |
| **fail-closed (content-safety)** | ZAI error 1301 silently drops large/sensitive buckets rather than processing them. Policy: accept the drop (no retry/reroute), but log it so it is not silent. Sensitive convergences may not surface — an accepted tradeoff. |
| **FUP (Fair-Use Policy)** | A ZAI signal for account-level concurrency/usage-policy violation, distinct from a plain 429. Carries ban risk, so the rate limiter does NOT retry on FUP; a pipeline invariant fires severity=critical when detected. |
| **gateway** | The in-process communication core (`gateway.py::InProcessGateway`, `gateway_server.py`) mediating between channels (web, email, telegram, webhook) and the execution engine; owns session lifecycle, locking, and WebSocket streaming. |
| **Ghost.build** | An MCP-provisioned ephemeral Postgres service Cody uses for demos. Cleanup contract: Cody records DBs in `manifest.json.ghost_databases` and `ghost_delete`s on success to protect the 100hr/mo free cap. |
| **gotcha inventory** | `02_GOTCHA_INVENTORY.md` — preserved non-code-shaped facts harvested from the legacy corpus, classed as **operational** (judged for current validity) vs **rationale** (the why, carried as asserted context). 68 code-behavior gotchas are re-derived from code instead. |
| **gws** | The Google Workspace CLI backing Gmail send/label, Calendar sync, and the AgentMail 429→Gmail fallback. Creds materialized on the VPS from four base64 Infisical secrets; uses `file` keyring backend. OAuth "Testing" mode expires refresh tokens ~weekly. |
| **headquarters** | The factory role for the primary/control machine (`FactoryRole.HEADQUARTERS`), contrasted with `LOCAL_WORKER`. |
| **heartbeat** | The autonomic loop (`heartbeat_service.py`) that triggers periodic agent turns for health supervision and proactive checks, capped by `max_proactive_per_cycle`. It does NOT execute trusted-email missions — that routes through Task Hub. Directives live in `memory/HEARTBEAT.md`. |
| **idle dispatch** | The nudge mechanism (`services/idle_dispatch_loop.py`) that prompts a worker to claim work when otherwise idle. |
| **insight brief** | A synthesized proactive-intelligence work product produced from convergence/knowledge blocks, evaluated and authored for operator delivery; funneled toward gated Task Hub candidates. |
| **intel lane** | A "what to watch and where to put it" recipe in `config/intel_lanes.yaml`, validated by `services/intel_lanes.py::LaneConfig` (Pydantic). Each lane carries source handles, a `research_allowlist`, and a cron (the @ClaudeDevs lane runs `0 8,16,22` America/Chicago). |
| **internal vault** | A derived markdown wiki built from canonical memory/session/checkpoint/run evidence (`vault_kind="internal"`). Supplements recall without replacing runtime state. |
| **just dev** | The canonical local dev launcher on Kevin's desktop. Autonomous loops are OFF in dev unless opted in with `UA_DEV_<NAME>_FORCE_ON=1`. The VPS is production-only. |
| **lossless memory** | DAG-compressed conversation history persisted to SQLite (`lossless_memory/dag_condenser.py`, `db.py`, `history_adapter.py`) for full-fidelity recall. |
| **LLM Wiki / vault** | The wiki subsystem (`wiki/core.py`, `wiki/projection.py`, `wiki/llm.py`, `wiki/kb_registry.py`) maintaining internal and external markdown vaults via LLM extraction, projection, and a kb registry. `resolve_vault_path` honors `vault_kind` (legacy bug now fixed). |
| **maintenance (tier)** | VP mission priority for system housekeeping — curation, proactive wiki, upkeep (`vp/mission_priority.py`). |
| **MCP server** | The FastMCP tool server (`src/mcp_server.py`) exposing internal tools (file ops, research pipeline, wiki/kb bridges) to the agent; tool discovery via `tools/internal_registry.py`. |
| **mission priority tiers** | The four VP dispatch tiers in rank order: `operator_daily` → `operator_signal` → `maintenance` → `background` (`vp/mission_priority.py`). |
| **operator_daily** | Highest VP tier — work Kevin reads with morning/evening coffee (briefings, digests). |
| **operator_signal** | VP tier for Atlas-generated proactive intelligence surfaced to the operator. |
| **pipeline invariant** | An owner-declared post-condition asserting a pipeline's success actually produced correct output. Decorated functions under `services/invariants/` (`@invariant`) returning `None` (holds) or a finding dict; run every heartbeat by the Proactive Activity Watchdog. |
| **principal vs sub-agent** | Principals are top-level Claude Code orchestrators (Simone, Cody, Atlas) driven by heartbeats/Task Hub; sub-agents are helper definitions in `.claude/agents/<name>.md`. Listing `.claude/agents/` will not show the principals. |
| **Proactive Activity Watchdog** | A two-layer health framework run every Simone heartbeat: Layer 1 checks process liveness (cron registry, stale/parked tasks); Layer 2 runs pipeline invariants. Exposed at `GET /api/v1/ops/proactive_health` (`services/proactive_health.py`). |
| **proactive pipeline** | The `raw records → durable knowledge blocks → bounded retrieval context → LLM synthesis → gated action` flow (`services/proactive_*`, `supervisors/`). Some producer lanes (reflection, signal curation) are not fully wired to promotion helpers. |
| **residential proxy** | VPS-only outbound proxy for YouTube/transcript fetching (`youtube_ingest.py`): DataImpulse default, Webshare failover. The desktop transcript worker was decommissioned April 2026. |
| **research grounding** | A separate allowlist path (`services/research_grounding.py::is_allowed`) restricting open-web search to official sources. Distinct from the CSI URL judge; `research_allowlist` in `intel_lanes.yaml` gates only this path. |
| **route_all_to_simone** | The single active routing function (`services/agent_router.py`) — claimed tasks all go to Simone (Simone-first model). |
| **run** | The durable logical unit of work; may span multiple attempts and owns one run workspace. |
| **run workspace** | The durable filesystem evidence bundle for a run (checkpoints, transcripts, traces, artifacts), under `AGENT_RUN_WORKSPACES`. Resolution via `run_workspace.py`. |
| **Simone** | The primary orchestrator principal. Drives the heartbeat, claims Task Hub work, and dispatches downstream executors (Cody/VP workers). Directive file: `memory/HEARTBEAT.md`. Not a `.claude/agents/` entry. |
| **Simone-first** | The orchestration model where all routed work funnels to Simone rather than being auto-qualified to specialist agents. |
| **/ship** | The commit + push + open-PR-to-main + enable-auto-merge handoff. Should be run from a clean checkout (`~/dev/universal_agent`), not the polluted `/opt/universal_agent` production/scratch tree. |
| **SOUL.md** | A VP worker's identity file copied at mission start by `vp/worker_loop.py` from `prompt_assets/<VP>_SOUL.md` into the mission workspace; the agent setup pipeline loads it so the VP boots as itself, not Simone. |
| **SSHFS bridge** | A systemd SSHFS-over-Tailscale mount that makes the desktop's `/home/kjdragan/...` resolvable at the same path on the VPS. Refer to absolute paths directly; never build file-fetcher tools. |
| **stale release** | Recovery of orphaned in-progress tasks via `task_hub.py` (`UA_TASK_STALE_ENABLED` / `UA_TASK_STALE_MIN_AGE_MINUTES`). Do not write a per-task reaper. |
| **Tailscale hostnames** | The VPS has two names: raw OS `srv1360701` (Hostinger; used by `device_roles.json`/admin API) and MagicDNS `uaonvps` (used by SSH/preflight). Mixing them is a common gotcha. |
| **target-agent detection** | Email pre-triage that maps an inbound message to a VP via label (e.g. `agent-codie`) before task-bridge materialization (`services/email_task_bridge.py`, `email_tags.py`). |
| **Task Forge** | The meta-skill that turns human intent into reusable task-skills through a multi-phase pipeline (intent → scaffold → execute → quality gate → promote), with a v0→v3 skill maturity model. |
| **Task Hub** | The canonical durable task system and source of truth for proactive work (`task_hub.py`). Tasks flow through lifecycle states (open/in_progress/needs_review/completed/blocked/parked/delegated) with assignment, run forensics, and multi-channel delivery. DB is `activity_state.db`. |
| **Task Hub Observability Protocol** | The six-rule contract every async unit of work follows: identity, claim ledger, run history, subprocess identity, protocol-violation routing, standard recovery verbs (`ensure_cron_task_link`, `_open_run`, `classify_worker_exit`, `_close_run`, `park_task_for_protocol_violation`). |
| **ToDo dispatch** | The lane (`services/todo_dispatch_service.py`) that executes Simone's todo-derived/trusted-email work, separate from the heartbeat's health loop. |
| **trust_source** | A flag in URL judging that bypasses the LLM judge for official-handle lanes (`services/csi_url_judge.py`), trusting the source outright. |
| **URL judge** | The three-pass CSI URL enrichment: pre-filter → LLM judge (`resolve_opus`, not sonnet) → fetch (`services/csi_url_judge.py::enrich_urls`). One of three distinct "allowlists" in the codebase. |
| **URW (Universal Reasoning Workflow)** | The multi-phase orchestrator for long-running goals: decomposer, phase planner, evaluator, evaluation policy, plan persistence (`urw/decomposer.py`, `phase_planner.py`, `evaluator.py`, `orchestrator.py`). |
| **VP worker** | An external/agent worker process executing delegated work in a lane (`vp.coder.primary`=CODIE, `vp.general.primary`=ATLAS) with its own identity and mission constraints (`vp/worker_loop.py`, `vp/dispatcher.py`). Separates control plane from execution plane. |
| **worker-exit classification** | Mapping a worker subprocess exit into a lifecycle outcome (success/failure/protocol-violation), `services/worker_exit_classifier.py`. SIGTERM/143 during a deploy window is reclassified as a self-healing non-event. |
| **workspace guard** | The guardrail enforcing the workspace resolution boundary (`guardrails/workspace_guard.py`); 4-tier fallback for resolving where a run may write. |
| **ZAI proxy** | The cheap GLM inference backend (Anthropic-compatible endpoint) used by autonomous loops (Simone heartbeats, Atlas, dispatch sweep, intel crons). ZAI routing env vars are injected by `initialize_runtime_secrets()` at service start (never written to `.env`). Capacity-limited during Greater-China peak hours = US night. Contrast Anthropic-native. |
