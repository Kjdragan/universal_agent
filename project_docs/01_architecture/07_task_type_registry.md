---
title: Task Type & Mission System Registry
status: active
canonical: true
subsystem: task-type-registry
code_paths:
  - src/universal_agent/task_hub.py
  - src/universal_agent/vp/
  - src/universal_agent/durable/state.py
  - src/universal_agent/cron_service.py
  - src/universal_agent/heartbeat_service.py
  - src/universal_agent/services/proactive_convergence.py
  - src/universal_agent/services/hourly_intel_digest.py
  - src/universal_agent/services/atlas_direct_dispatch.py
  - src/universal_agent/systemd_migrated_jobs.py
last_verified: 2026-06-06
---

# Task Type & Mission System Registry

**Why this doc exists.** The platform runs many overlapping ways to create and execute a unit of
work — VP missions, Task Hub `source_kind`s, cron jobs, intelligence pipelines, dispatch paths. When
you go to improve one, it is easy to touch the *wrong* system, or to be misled by code for a system
that was already turned off. This registry is the **single map**: for every task type / mission
system it records **what it is, when to use it, its lifecycle status, and — for the dead ones — why
it was turned off and what replaced it.** It is the answer to "which one is canonical, and why is
*that* one still in the tree if it's dead?"

This is a **map**, not a re-implementation: each entry points to the canonical subsystem doc that
owns the detail (cited under "owner"). When behavior changes, update the owning doc and this row in
the same change.

## How to read status

| Status | Meaning | What to do |
|---|---|---|
| **canonical** | The current, correct way. Use this. | Build on it. |
| **active_secondary** | Real and running, but niche / supporting / behind a flag. | Use only for its specific purpose. |
| **deprecated** | Still present in the tree but **do not use** — kept as a disabled fallback / rollback lever, usually slated for deletion. | Do not extend; check the Decommission Register before reviving. |
| **removed** | Gone from code (or reduced to a stub/backfill-only reference). Listed so you know it *was* a thing and why it isn't anymore. | Don't resurrect without reading "why". |
| **unclear** | Referenced in docs/history but not anchored in current code. | Verify before relying on it. |

> Every entry below carries one of these statuses. Exact per-status totals are deliberately **not**
> tallied here — a hand-kept count rots on every row add/remove; read the status column instead. The
> Decommission Register (§6) is the consolidated "why was this turned off" record the rest of this
> doc cross-references.

---

## 1. VP missions & workers

A **VP mission** is the unit of delegated autonomous work: a producer queues a `vp_missions` row
(`vp/dispatcher.py::dispatch_mission` → `durable/state.py::queue_vp_mission`) carrying a free-form
`mission_type` string; a `VpWorkerLoop` claims it (`durable/state.py::claim_next_vp_mission`), runs
it via a client, and finalizes (`durable/state.py::finalize_vp_mission`). `mission_type` is **not an
enum** — it only drives priority-tier resolution and a couple of post-finalize hooks.

### The two VP workers (both canonical) — Atlas ≠ Cody

| | **Atlas** `vp.general.primary` | **Cody / CODIE** `vp.coder.primary` |
|---|---|---|
| Role | Generalist: research, intel-brief synthesis, convergence/ideation eval | Coder: builds runnable demos/code, **PR-as-output** |
| Inference | ZAI/GLM (`inference_mode='zai'`) — cheap autonomous synthesis | real Anthropic Max via OAuth (`inference_mode='anthropic'`) |
| Execution | `sdk` (in-process `ProcessTurnAdapter`) | **forced `cli`** (Claude Code subprocess) — /goal, Agent Teams, skills only work via CLI |
| Success looks like | an artifact (brief); no PR concept | a **PR** → `completed_with_pr` |
| `completed_without_pr` means | normal (no PR expected) | **likely a failure signal** (built nothing) |
| Entry | `vp/profiles.py::resolve_vp_profiles`, `vp/clients/claude_generalist_client.py` | `vp/profiles.py::resolve_vp_profiles`, `vp/clients/claude_cli_client.py::ClaudeCodeCLIClient`, `vp/coder_runtime.py::CoderVPRuntime` |

**Inference follows the VP, not the work**: a coding mission sent to Atlas still runs on ZAI; a
research mission sent to Cody still runs on Max. Owner: [`03_agents/01_vp_workers_and_delegation.md`](../03_agents/01_vp_workers_and_delegation.md).

### Execution modes

| Mode | Status | What | Entry |
|---|---|---|---|
| `sdk` | canonical | Default. In-process SDK runtime. Cannot expose Agent Teams / skills / /goal. | `vp/worker_loop.py::VpWorkerLoop` (`_select_client_for_mission`) |
| `cli` | canonical | Spawns `claude --print`. Only mode with full Claude Code toolchain. Auto-forced when `cody_mode=='anthropic'`. | `vp/clients/claude_cli_client.py::ClaudeCodeCLIClient` |
| `dag` | active_secondary | Deterministic state-machine runner from `payload.dag_definition` instead of an LLM loop. | `vp/clients/dag_client.py::DagClient`, `services/dag_runner.py::DagRunner` |

### `mission_type` values (canonical unless noted)

| mission_type | Status | Purpose / tier | Entry |
|---|---|---|---|
| `briefing` / `morning_briefing` / `evening_briefing` | canonical | Operator-daily briefings; highest tier (`operator_daily`) | `scripts/briefings_agent.py` |
| `doc-maintenance` | canonical | Docs upkeep (`maintenance` tier); post-finalize push/PR hook | `vp/worker_loop.py::_post_mission_push_pr_merge` |
| `proactive_general` | canonical | Generic proactive Atlas mission from the direct-dispatch sweep | `services/atlas_direct_dispatch.py::dispatch_atlas_candidates_once` |
| `curation` / `proactive_wiki` | canonical | Memory/wiki housekeeping (`maintenance` tier) | `scripts/nightly_wiki_agent.py` |
| `freelance_scout` | active_secondary | Freelance-opportunity scouting; unmapped → `background` tier | `scripts/freelance_scout_agent.py` |
| `coding_task` / `general_task` / `research_task` | active_secondary | Redis cross-machine bridge mission_kinds → vp_id mapping | `delegation/redis_vp_bridge.py::MISSION_KIND_TO_VP` |
| `evaluate_and_author_intel_brief` | unclear | **Named in dispatch prompts but NOT a literal source `mission_type`.** The real pipeline is convergence_candidate → the `/evaluate-and-author-intel-brief` skill. Treat as a skill, not a mission_type. | `services/proactive_convergence.py::write_convergence_candidate` |

### Priority tiers (canonical)

`operator_daily` → `operator_signal` → `maintenance` → `background`, ordered by tolerable operator-notice
delay (replaced raw numeric priority). `vp/mission_priority.py::TIERS`, `::resolve_tier`, `::MISSION_TYPE_TIER`.

### VP mission lifecycle helpers (canonical)

- **Terminal disposition** — `completed_with_pr` vs `completed_without_pr`, stamped at finalize so the digest
  can tell a shipped PR from a legit no-op. `vp/worker_loop.py::VpWorkerLoop` (`_detect_pr_url`). **Read it,
  don't set it.**
- **Failure rescue verbs** — `retry` / `redispatch_fresh` / `escalate`, surfaced as a `vp_mission_failure`
  Task Hub item on failed/cancelled. `services/vp_failure_rescue.py`, `tools/vp_orchestration.py`.
- **Lease-liveness reconciliation guard** — prevents the gateway-startup reconciler from false-orphaning a
  healthy VP mission (which previously caused duplicate runs). Agent-agnostic (Atlas + Cody).
  `task_hub.py::_vp_mission_lease_live` (consumed in `task_hub.py::reconcile_task_lifecycle`). _(added 2026-06-06, PR #771)_
- **`flush_vp_mission_backlog`** (active_secondary) — one-shot operator tool to clear a runaway queue.
  `scripts/flush_vp_mission_backlog.py`.
- **VP `/goal` self-brief + completion attestation** (active_secondary, Cody-only, flag-gated) — BRIEF.md /
  ACCEPTANCE.md / COMPLETION.md loop. `services/self_briefing.py::vp_goal_enabled`. Owner:
  [`03_agents/05_idle_dispatch_and_goal_loop.md`](../03_agents/05_idle_dispatch_and_goal_loop.md).

---

## 2. Dispatch & routing paths

| Path | Status | Use for | Entry |
|---|---|---|---|
| **VP dispatcher** | canonical | Getting work TO a VP worker (Atlas/Cody). Idempotent by `sha1(vp_id:idempotency_key)`. | `vp/dispatcher.py::dispatch_mission`, `durable/state.py::claim_next_vp_mission` |
| **Task Hub daemon dispatch sweep** | canonical | Simone-side claiming of general Task Hub work. **Excludes `vp_mission` source kinds** (they go via the VP dispatcher) — this exclusion is why VP mirror rows never get dispatch handles. | `task_hub.py::claim_next_dispatch_tasks`, `services/dispatch_service.py::dispatch_sweep` |
| **`claim_task_for_agent`** | canonical | Targeted companion for interactive/explicit intake into the same durable lifecycle. | `task_hub.py::claim_task_for_agent` |
| **Simone-first routing** | canonical | Every claimed task is routed to Simone, who decides delegation. | `services/dispatch_service.py::_enrich_with_routing`, `services/agent_router.py::route_all_to_simone` |
| **atlas_direct_dispatch** | active_secondary | Independent ~60s sweep dispatching `preferred_vp='vp.general.primary'` tasks without Simone's heartbeat throttle (Hermes Phase C). | `services/atlas_direct_dispatch.py::dispatch_atlas_candidates_once` |
| `qualify_agent*` keyword router | **removed** | (see §6) superseded by Simone-first routing | — |

---

## 3. Task Hub `source_kind`s (the ingress taxonomy)

The Task Hub (`activity_state.db`, **not** `task_hub.db`) is the spine; each `source_kind` is effectively
a task type. Owner: [`02_execution_core/02_task_hub.md`](../02_execution_core/02_task_hub.md).

| source_kind | Status | What creates it | Entry |
|---|---|---|---|
| `email` | canonical | Trusted inbound AgentMail → one task/thread | `services/email_task_bridge.py::EmailTaskBridge` |
| `simone_chat` | canonical | Each interactive Simone chat session | `services/simone_chat_tasks.py::record_first_operator_message` |
| `convergence_candidate` | canonical | Proactive convergence/ideation → Atlas intel brief | `services/proactive_convergence.py::write_convergence_candidate` |
| `proactive_signal` | canonical | Signal curator promotes signal cards | `services/signal_curator.py::promote_cards_to_tasks` |
| `csi` | canonical | CSI-ingested signals (routing sub-machine) | `task_hub.py::upsert_csi_item` |
| `cron_run` | canonical | Cron tick ↔ Task Hub link (observability). **Post-#773 board behavior:** idle perpetual `cron:<job>` rows are hidden from the Kanban (`gateway_server.py::_is_idle_cron_board_row`); a cron earns a card only while running/failed, and each finished run lands as its own card in the Completed lane via `task_hub.py::list_completed_cron_runs` (backed by the per-run `task_hub_runs` audit table). | `services/cron_task_hub_link.py::ensure_cron_task_link` |
| `vp_mission` | canonical | **Visibility-only** Kanban mirror of a VP mission (`agent_ready=False`) | `tools/vp_orchestration.py` (`_vp_dispatch_mission_impl`) |
| `vp_mission_failure` | canonical | VP failure → rescue surfacing to Simone | `services/vp_failure_rescue.py` |
| `approval` | canonical | Operator-approval-gated tasks | `services/dispatch_service.py::dispatch_on_approval` |
| `mission_control_card_dispatch` | canonical | Operator dispatches a Mission Control card to Cody | `gateway_server.py::dashboard_mission_control_dispatch_to_codie` |
| `dashboard_quick_add` / `system_command` | canonical | Operator quick-add / directives (e.g. `schedule_task`) | `task_hub.py::_is_system_schedule_task` |
| `calendar` | active_secondary | Upcoming Google Calendar events → tasks | `services/calendar_task_bridge.py::CalendarTaskBridge` |
| `cody_demo_task` | active_secondary | Cody picks up a scaffolded demo workspace | `services/cody_dispatch.py::dispatch_cody_demo_task` |
| `cody_scaffold_request` / `claude_code_update` / `claude_code_kb_update` | active_secondary | ClaudeDevs X-intel artifacts; `cody_scaffold_request` is the canonical demo-scaffold request | `services/claude_code_intel.py` |
| `reflection` | active_secondary | Idle-only autonomous ideation | `services/reflection_engine.py::build_reflection_context` |
| `proactive_codie` / `codie_pr` | active_secondary | Proactive code-cleanup & PR-review tasks for Cody | `services/proactive_codie.py` |
| `tutorial_build` | active_secondary | Transcript-derived tutorial-build tasks | `services/proactive_tutorial_builds.py::queue_tutorial_build_task` |
| `proactive_outcome` | active_secondary | Post-mortem for failed/blocked proactive tasks | `services/proactive_auto_investigator.py` |
| `chat_panel` / `heartbeat_remediation` / `proactive_feedback_continuation` | active_secondary | Chat-panel tasks / heartbeat self-remediation / feedback continuations | `task_hub.py::create_proactive_feedback_continuation` |
| `mission_envelope` / `mission_phase` | active_secondary | Multi-task grouping (flag-gated `task_hub_missions`) | `task_hub.py::create_mission_envelope` |
| `csi_recommendation` | active_secondary | CSI recommendation cards (parking/approval flow) | `gateway_server.py` (csi_recommendation handling) |
| `claude_code_demo_task` | **deprecated** | (see §6) legacy direct demo → `cody_scaffold_request` | `services/claude_code_intel.py` |
| `convergence_detection` / `insight_detection` | **removed** | (see §6) legacy per-signature firehose | backfill-only in `task_hub.py::PROACTIVE_HISTORY_SOURCE_KINDS` |
| `proactive_health` | **removed** | (see §6) zombie needs-review rows | — |

---

## 4. Scheduled / autonomous drivers

Two substrates run recurring work. Owner: [`03_agents/04_cron_and_scheduling.md`](../03_agents/04_cron_and_scheduling.md)
and the scheduling-substrate ADR ([`06_platform/08_scheduling_substrate_adr.md`](../06_platform/08_scheduling_substrate_adr.md)).

| Driver | Status | What | Entry |
|---|---|---|---|
| **systemd timers (S5 migration)** | canonical | Deploy-independent per-job `OnCalendar` timers on the VPS — the canonical substrate for migrated jobs. **The migrated set is not enumerated here** (it drifts): the machine source of truth is the `SYSTEMD_MIGRATED_SYSTEM_JOBS` frozenset; the per-job target policy + rationale is ADR §Decision 1. On migration the in-process cron is **force-disabled so the timer is the sole firer** (no double-fire); env escape hatch `UA_SYSTEMD_TIMER_MIGRATION_DISABLED=1` reverts all migrated jobs to in-process. | `systemd_migrated_jobs.py::SYSTEMD_MIGRATED_SYSTEM_JOBS`, `systemd_migrated_jobs.py::is_migrated_to_systemd`, `deployment/systemd/*.timer` |
| **in-process gateway `CronService`** | active_secondary | Embedded async scheduler for operator/dynamic crons + the system jobs **not** migrated (the live `/api/v1/cron/jobs` complement of the frozenset — see the split note below). | `cron_service.py::CronService` |
| **Mission Control sweeper service** | canonical | The observational Chief-of-Staff sweeper, extracted from the gateway lifespan into its own deploy-isolated systemd process (S5 Phase B). | `services/mission_control_sweeper_main.py` |
| **proactive-health timer** | canonical | Deterministic, LLM-free health-probe runner on a systemd oneshot timer (S5 Phase C); writes a durable `proactive_health_snapshots` row + emails a digest; heartbeat reads the snapshot. | `services/proactive_health_timer_main.py` |
| **VP worker services** | canonical | Atlas & Cody `VpWorkerLoop`s run as **systemd template instances** `universal-agent-vp-worker@vp.general.primary` / `@vp.coder.primary`, not threads inside the gateway. | `vp/worker_main.py`, `deployment/systemd/universal-agent-vp-worker@.service` |
| **`systemd_migrated_jobs` registry + migration-aware COS** | canonical | Leaf module = source of truth for which crons moved to timers; the Chief-of-Staff readout consults it to drop stale `cron:<job>` Task-Hub relics (#766). | `systemd_migrated_jobs.py::is_migrated_to_systemd`, `services/mission_control_chief_of_staff.py` |
| **Heartbeat service** | canonical | Simone's recurring proactive-findings loop (findings → email + Mission Control, **not** Task Hub rows). | `heartbeat_service.py::HeartbeatService` |
| **Idle dispatch loop** | canonical | Wakes an idle agent the instant work appears (decoupled from heartbeat cadence). | `services/idle_dispatch_loop.py::idle_dispatch_loop` |
| **Goal loop / completion attestation** | active_secondary | Gate on COMPLETION.md before a mission may finalize `completed` (Cody-only). | `services/self_briefing.py::check_completion_attestation` |
| **VPS infra timers** | canonical | watchdog / OOM-alert / uv-cache-prune host timers. | `deployment/systemd/universal-agent-service-watchdog.timer` |
| **`hourly_intel_digest`** | canonical | **The** delivery path for VP-authored intel briefs (see §5); migrated (batch 3) — runs as a systemd timer, gateway cron registered-but-`enabled=False`. | `scripts/hourly_intel_digest_cron.py::run_once` |
| `hourly_insight_email` cron | **removed** | (see §6) gateway cron registration **fully removed** (no `_ensure_*` fn, absent from the startup block) — superseded by `hourly_intel_digest`. Only dead modules remain. | `scripts/hourly_insight_email.py` |

**Getting the current migrated-vs-in-process split** — *do not hand-maintain a job list here.* Such a list drifts within a day; re-enumerating it is exactly how the prior scheduling snapshots went stale (see the ADR's own staleness notes). To get the live split:

- **Migrated → systemd timers:** the `SYSTEMD_MIGRATED_SYSTEM_JOBS` frozenset in `systemd_migrated_jobs.py` is the machine source of truth; `is_migrated_to_systemd(job)` is the predicate every surface uses. A migrated job appears in the gateway cron list with `enabled=False` — that is the **correct migrated state** (the timer is the sole firer), not a fault.
- **Still in-process:** whatever is enabled in live `/api/v1/cron/jobs` and **not** in that frozenset, plus operator/dynamic crons. Some jobs stay in-process *by design* — e.g. `paper_to_podcast_daily` is a daily *prompt* that needs the agent runtime/skills/MCP, so it structurally cannot be a pure timer.
- **Why a given job is on a timer vs left in-process:** ADR §Decision 1 (substrate policy + per-job target table).

> **"Looks-off-but-intentional" anchor:** a migrated job's gateway cron shows `enabled=False` (e.g. `hourly_intel_digest`) — that is the **correct migrated state** (the systemd timer is the sole firer); do **not** "fix" it by re-enabling the gateway cron. Cross-reference `is_migrated_to_systemd(job)` to tell migrated-and-running from genuinely-off. (Contrast: `hourly_insight_email` is *fully removed*, not migrated — see §6.)

---

## 5. Intelligence pipelines (the producers)

These generate the work Atlas/Cody execute. Owners: [`04_intelligence/01_csi_architecture.md`](../04_intelligence/01_csi_architecture.md),
[`04_intelligence/10_proactive_pipeline.md`](../04_intelligence/10_proactive_pipeline.md),
[`04_intelligence/06_demo_triage.md`](../04_intelligence/06_demo_triage.md),
[`04_intelligence/05_youtube_csi_flow.md`](../04_intelligence/05_youtube_csi_flow.md).

| Pipeline | Status | What | Entry |
|---|---|---|---|
| **Convergence detection (Track A)** | canonical | SQL recall + bounded LLM precision: same story across ≥N channels in 72h | `services/proactive_convergence.py::sync_topic_signatures_from_csi` |
| **Ideation sweep (Track B)** | canonical | Cross-cutting non-obvious synthesis over the signature corpus | `services/proactive_convergence.py::track_b_ideation_synthesis` |
| **Convergence → intel brief → digest** | canonical | `write_convergence_candidate` → triage → `convergence_candidate` task → `/evaluate-and-author-intel-brief` → `proactive_artifacts` (verdict=ship) → `hourly_intel_digest`. **This is the canonical intel delivery path.** | `services/hourly_intel_digest.py::select_candidates_for_current_hour`, `::compose_send_payload` |
| **ClaudeDevs X intel lane** | canonical | X polling → tier classify → URL ground → packet → demo-triage | `services/claude_code_intel.py::run_sync` |
| **CSI Vault intelligence pass** | canonical | Per-packet LLM VaultDelta extraction (Memex) | `services/csi_intelligence_pass.py::analyze_action` |
| **Demo Triage** | canonical | Candidate store + ranker + pending→approve gate for tier-3 actions | `services/csi_demo_triage.py::approve_candidate` |
| **Signal curator** | canonical | Promotes proactive signal cards to tasks | `services/signal_curator.py::should_run_curation` |
| **Proactive task builder + gates** | canonical | The single chokepoint where proactive services create tasks (preference + budget gates) | `services/proactive_task_builder.py::queue_proactive_task` |
| **Intel auto-promoter** | canonical | Overnight score-gated automation of demo-triage approval | `services/intel_auto_promoter.py::promote_top_candidates` |
| **YouTube Daily Digest (Pipeline A)** | canonical | Native playlist watcher → transcript → digest email | `scripts/youtube_daily_digest.py::process_daily_digest` |
| **YouTube CSI channel-RSS (Pipeline B)** | canonical | RSS events → proactive cards + convergence source | `proactive_signals.py::generate_youtube_cards` |
| **CSI URL three-pass judge** | active_secondary | Shared URL enrichment (pre-filter → LLM judge → fetch) | `services/csi_url_judge.py::enrich_urls` |
| **Reflection engine** | active_secondary | Idle-only ideation prompts | `services/reflection_engine.py::is_reflection_enabled` |
| **Relevance gate** | active_secondary | SQL denylist excluding non-domain categories at ingest | `services/proactive_convergence.py::sync_topic_signatures_from_csi` |
| **Proactive reporting surfaces** | active_secondary | 3×/day intelligence reports + AM artifact digest + advisor | `services/proactive_intelligence_report.py::compose_intelligence_report` |
| **CSI Ingester (external service)** | active_secondary | Source registry + RSS/Threads/HN adapters + semantic enricher | `CSI_Ingester/development/csi_ingester/app.py` |
| **Discord Intelligence** | active_secondary | Passive monitor daemon + C&C bot | `discord_intelligence/daemon.py` |
| **Intel lanes config** | active_secondary | `intel_lanes.yaml` loader (add a lane in YAML, not code) | `services/intel_lanes.py` |
| **URW HarnessOrchestrator / URWOrchestrator** | active_secondary | Multi-phase long-running task orchestration (live + classic engines). Owner: [`02_execution_core/04_urw_orchestration.md`](../02_execution_core/04_urw_orchestration.md) | `urw/harness_orchestrator.py`, `urw/orchestrator.py` |
| Legacy CSI auto-queue (`queue_follow_up_tasks`) | **deprecated** | (see §6) bypassed operator review → demo-triage gate | `services/claude_code_intel.py::queue_follow_up_tasks` |
| Legacy per-signature convergence firehose | **removed** | (see §6) 0.14% completion noise | — |
| **Health invariant probes** | canonical | ~21 deterministic, LLM-free invariant probes across 9 modules feeding the proactive-health snapshot + Mission Control; the cron probes skip systemd-migrated jobs so they don't false-alert. | `services/invariants/` (`cron_staleness.py`, `csi_source_liveness.py`, `mission_control_sweeper_liveness.py`, …) |
| Legacy regex vault entity extractor | **removed** | (see §6) ~50% junk → LLM-native pass | — |

---

## 6. Decommission Register — what was turned off, why, and what replaced it

The consolidated "why is this dead / why was it turned off" record. **Before reviving or being confused
by any of this code, read the row.**

| System (removed/deprecated) | Replaced by | Why turned off | When |
|---|---|---|---|
| **`qualify_agent()` / `qualify_agent_llm()`** keyword pre-router | Simone-first routing (`agent_router.route_all_to_simone`) | Simone now owns delegation; the deterministic keyword router became dead code | ~2026-05-26 (PR #461) |
| **Legacy per-signature convergence/insight firehose** (`detect_and_queue_convergence`, `create_insight_brief_task`) | `convergence_candidate` path (`write_convergence_candidate` + inline triage) | 0.14% completion (~698 cancelled / 1 completed) — a no-precision-gate firehose that buried the VP queue | 2026-05 (PR #568) |
| `convergence_detection` / `insight_detection` **source_kinds** | `convergence_candidate` | Tied to the removed firehose; survive only in backfill source-kind lists | 2026-05 (#568) |
| **`proactive_convergence_events` table** + its probe | `convergence_candidates` table | Old 24/7 cadence table became a permanent false-RED for the watchdog after the move to hourly | frozen 2026-05-28; table dropped (#710) |
| **`proactive_health` Task Hub parking** | critical email + live `/ops/proactive_health` endpoint + Mission Control System Health panel | Produced zombie needs-review rows, severity mislabels, board pollution, resurrection-on-trash | 2026-06-03 |
| **Operator Brief panel** / `/dashboard/situations` | Mission Control Chief-of-Staff readout (`mission_control_chief_of_staff.py`) | Synthesizing from stale situations re-staled the brief; replaced by cascade-gated COS synthesis | Phase 8 (~2026-05-04) |
| **Todoist integration** (external task manager) | Task Hub (`task_hub.py` / `activity_state.db`) | Consolidated all task management onto the durable in-process hub | 2026-03-26 |
| **`youtube_playlist_watcher`** (CSI playlist poller) | YouTube daily digest + `youtube_channel_rss` adapter | Daily digest became the canonical YouTube trigger; poller redundant (orphan timer unit remains) | retired PR #438; dropped from liveness 2026-06-03 |
| **Legacy regex vault entity extractor** | CSI Vault intelligence pass (LLM-native VaultDelta) | Code-side pattern matching produced ~50% junk; "code never decides what is meaningful" | superseded ~2026-06-01 |
| **`develop` / `feature/latest2` branches** + legacy AgentBridge session path | main-only branching; InProcessGateway / ProcessTurnAdapter | Simplified to main-only; AgentBridge replaced by in-process execution | `develop` retired 2026-05-10 |
| **`hourly_insight_email` cron** | `hourly_intel_digest` cron (+ `/hourly-intel-digest` skill) | Per-insight emails superseded by the batched convergence-brief digest. Gateway cron registration **fully removed** (no `_ensure_*` fn; absent from the startup block) — *not* a disabled-but-registered rollback lever. Only dead modules (`scripts/hourly_insight_email.py`, `services/hourly_insight_email.py`) remain as deletion candidates. | registration removed (post-#534); modules pending deletion |
| **`claude_code_demo_task`** source_kind | `cody_scaffold_request` | Replaced by the scaffold-request canonical path; emergency fallback only | — |
| **Legacy CSI auto-queue** (`queue_follow_up_tasks` straight-to-Task-Hub) | Demo Triage pending→approve gate | Unconditional auto-queue flooded the hub with low-signal historical candidates | 2026-05 |
| **Legacy tutorial feed** | `tutorial_build` proactive task | Replaced by the explicit tutorial-build pipeline | 2026-03-26 |
| **URW `GitCheckpointer`** | SQLite `URWStateManager` + `.urw/` file mirror | Real git checkpointing caused nested-repo conflicts; replaced with deterministic SQLite/file state (current code is a no-op stub) | git init removed (~2026-05-31) |
| **`read_research_files`** MCP tool *(deprecated, NOT removed)* | `finalize_research` → `refined_corpus.md` | Superseded by the pre-extracted corpus, but **still a registered, callable `@mcp.tool()`** kept for backwards compatibility (`mcp_server.py::read_research_files`) — its own docstring marks it DEPRECATED. Listed here as deprecated, not gone from code. | deprecated (live) |
| **Agent College** (self-improvement loop) | none active (dormant) — skills authored under `.claude/skills/` | Belongs to the old Railway/Telegram deploy; never wired into the production gateway runtime | vestigial |
| **VPS-as-Dev fallback workflow** _(unclear)_ | Antigravity Remote-SSH to `ua@uaonvps` | Listed obsolete in the doc taxonomy; no code anchor | doc-asserted only |
| **Reddit** — CSI `reddit_discovery` source + `reddit_top_posts` internal tool (`reddit_bridge.py`) + `reddit-intel` skill + Composio `REDDIT_*` surface | none — de-scoped (no replacement) | De-scoped from the project. CSI ingestion went dark 2026-05-12 and the ingestion lane was killed in #707; this change removes the remaining tool, skill, prompt/agent wiring, UI source, and doc references so nothing references Reddit or treats it as broken functionality. No code residue remains (the tool/skill/icon are deleted, not stubbed). | ingestion dark 2026-05-12; CSI lane removed #707; fully removed 2026-06-06 |

---

## Cross-references

- Task lifecycle (one unit of work, ingress→deliver): [`01_architecture/02_task_lifecycle_end_to_end.md`](02_task_lifecycle_end_to_end.md)
- Task Hub mechanics (queue, claim, reconcile): [`02_execution_core/02_task_hub.md`](../02_execution_core/02_task_hub.md)
- VP workers (Atlas/Cody detail): [`03_agents/01_vp_workers_and_delegation.md`](../03_agents/01_vp_workers_and_delegation.md)
- Proactive pipeline (producers): [`04_intelligence/10_proactive_pipeline.md`](../04_intelligence/10_proactive_pipeline.md)
- Operational gotchas: [`02_GOTCHA_INVENTORY.md`](../02_GOTCHA_INVENTORY.md)
