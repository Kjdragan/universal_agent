# Universal Agent Documentation

Single source of truth for the rebuilt documentation. **This index is generated**
(`scripts/gen_doc_index.py`) from the doc manifest + each doc's frontmatter — it cannot
drift from disk. Editing rules live in [`CLAUDE.md`](CLAUDE.md) and are enforced by CI
(`scripts/doc_audit.py`).

> **Code is the source of truth.** Every doc is reconstructed from code, cites `file::symbol`
> (never line numbers), and carries `code_paths` frontmatter that drives PR-time drift checks.

## Meta

- [00_DOCUMENTATION_REFACTOR_PLAN.md](00_DOCUMENTATION_REFACTOR_PLAN.md) — How and why the docs were rebuilt (code-first).
- [01_TAXONOMY.md](01_TAXONOMY.md) — Category structure and the canonical doc set.
- [02_GOTCHA_INVENTORY.md](02_GOTCHA_INVENTORY.md) — Preserved operational/rationale facts not visible in code.
- [GLOSSARY.md](GLOSSARY.md) — Project-specific terminology.
- [CLAUDE.md](CLAUDE.md) — Documentation governance (rules) — lazy-loaded when editing docs.

## 01_architecture

_System-level design & cross-cutting models_

- **[System Architecture Overview](01_architecture/01_system_overview.md)** — Top-level topology: principals (Simone/Cody/Atlas), gateway, what-talks-to-what, process model. _(verified 2026-06-11)_
- **[Task Lifecycle End-to-End](01_architecture/02_task_lifecycle_end_to_end.md)** — Trace one unit of work ingress→queue→claim→execute→finalize→deliver across channels. _(verified 2026-06-10)_
- **[Database Architecture](01_architecture/03_database_architecture.md)** — DB inventory, schema, segregation boundaries (activity_state.db is canonical Task Hub DB, NOT task_hub.db), pruning. _(verified 2026-06-05)_
- **[Model Choice & Resolution](01_architecture/04_model_choice_and_resolution.md)** — resolve_opus/sonnet/haiku, ZAI proxy vs Anthropic-native routing, the three execution profiles, inference health governance. _(verified 2026-06-11)_
- **[Hook System Architecture](01_architecture/05_hook_system.md)** — Hook lifecycle, permissions, subagent architecture as wired in code. _(verified 2026-06-12)_
- **[Event Streaming & Tracing](01_architecture/06_event_streaming_and_tracing.md)** — AgentEvent emission, event stream protocol, transcript/trace building, Logfire links. _(verified 2026-06-10)_
- **[Task Type & Mission System Registry](01_architecture/07_task_type_registry.md)** — Canonical catalog of every task type / mission system with lifecycle status (canonical/active_secondary/deprecated/removed) _(verified 2026-06-09)_

## 02_execution_core

_Gateway, sessions, execution engine, task hub, dispatch, durable, URW, workspaces_

- **[Gateway, Sessions & Execution](02_execution_core/01_gateway_sessions_execution.md)** — Gateway protocol, InProcessGateway, ProcessTurnAdapter, session lifecycle/locking, WebSocket streaming, timeouts. _(verified 2026-06-10)_
- **[Task Hub & Dispatch](02_execution_core/02_task_hub.md)** — Data model, dispatch queue build+ranking, atomic claiming, stale release, execution runs, action verbs, worker-exit classification, observability protocol. _(verified 2026-06-06)_
- **[Durable Execution](02_execution_core/03_durable_execution.md)** — Durable state, tool-call ledger, worker pool, tool classification, checkpointing. _(verified 2026-06-10)_
- **[URW Orchestration](02_execution_core/04_urw_orchestration.md)** — Multi-phase task orchestration: decomposer, phase planner, evaluator, evaluation policy, state/artifacts. _(verified 2026-06-11)_
- **[Workspaces & Artifacts](02_execution_core/05_workspaces_and_artifacts.md)** — Workspace resolution (4-tier fallback), artifacts dir resolution, run workspaces, guardrails, remote sync. _(verified 2026-06-12)_
- **[SDK Lifecycle Hooks & Guardrails](02_execution_core/06_sdk_lifecycle_hooks_and_guardrails.md)** — PreToolUse/PostToolUse guardrail engine: tool gating (DISALLOWED_TOOLS), workspace write guard, heartbeat write allowlist, code-mutation actor resolution, subagent detection, TaskStop rejection, tool-call event emission. _(verified 2026-06-11)_

## 03_agents

_VP workers, Simone orchestration, heartbeat, cron, idle dispatch, agent college_

- **[VP Workers & Delegation](03_agents/01_vp_workers_and_delegation.md)** — CODIE/ATLAS lanes, mission dispatch/queueing, priority tiers (operator_daily/operator_signal/maintenance/background), profiles, execution clients, worker loop, redis bridge, factory heartbeat/registry, cody-mode routing, goal loop, failure rescue. _(verified 2026-06-11)_
- **[Simone-First Orchestration](03_agents/02_simone_first_orchestration.md)** — Simone-first routing model, agent router (note: qualify_agent* are decommissioned). _(verified 2026-06-11)_
- **[Heartbeat Service](03_agents/03_heartbeat_service.md)** — Heartbeat loop, max_proactive_per_cycle, findings schema/contract, auto-triage→Simone. _(verified 2026-06-07)_
- **[Cron & Scheduling](03_agents/04_cron_and_scheduling.md)** — Cron registration, deploy-window detection (suppress restart noise), catch-up, system cron jobs. _(verified 2026-06-10)_
- **[Idle Dispatch & Goal Loop](03_agents/05_idle_dispatch_and_goal_loop.md)** — Idle dispatch nudge mechanism, goal loop & completion attestation, failure-mode classification & rescue verbs. _(verified 2026-06-09)_
- **[Agent College](03_agents/06_agent_college.md)** — Failure-analysis learning loop (if present/active in code; dispose if dead). _(verified 2026-06-11)_

## 04_intelligence

_CSI, intel lanes, research, wiki, memory, proactive pipeline, mission control, discord intel_

- **[CSI Architecture](04_intelligence/01_csi_architecture.md)** — CSI overall: convergence pipeline, vault intelligence pass/persistence, db split-brain (_csi_default_db_path), content-safety fail-closed gotcha. _(verified 2026-06-11)_
- **[URL Judging & Research Grounding](04_intelligence/02_url_judging_and_research_grounding.md)** — Three-pass URL enrichment (pre-filter→LLM judge (resolve_opus)→fetch), trust_source bypass, research_grounding allowlist (separate path). _(verified 2026-06-08)_
- **[Intel Lanes Configuration](04_intelligence/03_intel_lanes_config.md)** — Lane config schema, cron schedule (3x daily 08/16/22 CT), research_allowlist semantics. _(verified 2026-06-11)_
- **[ClaudeDevs X Intelligence](04_intelligence/04_claudedevs_x_intel.md)** — @ClaudeDevs polling lane, packet outputs, vault-as-canonical-product. _(verified 2026-06-08)_
- **[YouTube CSI Flow](04_intelligence/05_youtube_csi_flow.md)** — YouTube feeds topology, dual-pipeline (UA-native playlist watcher vs CSI RSS feed — separate DBs), residential proxy ingestion. _(verified 2026-06-10)_
- **[Demo Triage](04_intelligence/06_demo_triage.md)** — Demo candidate store, ranking, triage policy. _(verified 2026-06-11)_
- **[LLM Wiki System](04_intelligence/07_llm_wiki.md)** — Vault management, internal sync/projection, query, LLM extraction, kb registry. _(verified 2026-06-11)_
- **[Memory System](04_intelligence/08_memory_system.md)** — Tiered memory, memory store/index, vector backends (Chroma/Lance), orchestrator, feature flags. _(verified 2026-06-03)_
- **[Lossless Memory](04_intelligence/09_lossless_memory.md)** — DAG compression & SQLite history. _(verified 2026-06-04)_
- **[Proactive Pipeline](04_intelligence/10_proactive_pipeline.md)** — raw→knowledge blocks→bounded retrieval→LLM synthesis→gated action. _(verified 2026-06-11)_
- **[Mission Control Intelligence](04_intelligence/11_mission_control_intelligence.md)** — Operator intelligence surface, supervisor snapshots (note: Operator Brief panel removed Phase 8). _(verified 2026-06-11)_
- **[Discord Intelligence](04_intelligence/12_discord_intelligence.md)** — Discord message pipeline, triage, calendar sync (gws materialization). _(verified 2026-06-12)_
- **[Insight Pipeline Build Plan (Phases 0.5/4/5/6)](04_intelligence/13_insight_pipeline_build_plan.md)** — Living build/status plan: close the brief->digest email gap, digest dedup+template, feedback/index verify, gated legacy deletion. _(verified 2026-06-04)_
- **[Intelligence Model Tiering by Process](04_intelligence/14_model_tiering_by_process.md)** — Per-process registry of which GLM tier each inference call uses and why (air/turbo/flagship), the decision rubric, and the 2026-06-10 429-burst remediation. _(verified 2026-06-12)_
- **["ADR: YouTube Brief / Tutorial / Demo Pipeline Redesign"](04_intelligence/15_demo_tutorial_pipeline_adr.md)** — Brief→Tutorial→Demo ladder; demo = runnable mini-app of the video's capability (native stack or Claude Agent SDK); gated auto-build (~10/day) _(verified 2026-06-11)_

## 05_channels

_Email/AgentMail, webhooks, telegram, discord ops, web-ui communication_

- **[Email / AgentMail](05_channels/01_email_agentmail.md)** — WebSocket ingress, pre-triage security/quarantine, target-agent detection (label is agent-codie NOT agent-cody), task-bridge materialization, tags, Gmail 429 fallback, trusted-inbox queue. _(verified 2026-06-04)_
- **[Webhook Architecture](05_channels/02_webhooks.md)** — Webhook handlers & ops. _(verified 2026-06-07)_
- **[Telegram Channel](05_channels/03_telegram.md)** — Polling bot (not webhook), tg_<user_id> session scheme, allowlist (TELEGRAM_ALLOWED_USER_IDS — naming-drift gotcha), gateway-bypass architectural note. _(verified 2026-06-08)_
- **[Discord Operations](05_channels/04_discord_ops.md)** — Discord bot operations & usage (operator-facing). _(verified 2026-06-10)_
- **[Web UI Communication](05_channels/05_web_ui_communication.md)** — Chat panel ingress/tracking, activity log layer, task hub dashboard contract, AG-UI streaming, dashboard auth surface. _(verified 2026-06-08)_

## 06_platform

_Secrets/Infisical, runtime bootstrap, identity/auth, deployment/CI, environments, networking_

- **[Secrets & Infisical](06_platform/01_secrets_and_infisical.md)** — Infisical as SSOT, initialize_runtime_secrets, bootstrap-identity-key immutability (overwrite=True but identity preserved), env rendering, dev mirrors prod. _(verified 2026-06-11)_
- **[Runtime Bootstrap & Profiles](06_platform/02_runtime_bootstrap_and_profiles.md)** — Runtime stage resolution {development,staging,local,production}, deployment profiles, factory role policy, machine identity. _(verified 2026-06-09)_
- **[Identity & Auth](06_platform/03_identity_and_auth.md)** — Identity registry/resolver, email recipient resolution, ops auth (JWT + legacy token), dashboard auth (cookie+HMAC), three trust surfaces. _(verified 2026-06-09)_
- **[Deployment & CI/CD](06_platform/04_deployment_and_cicd.md)** — Branch model (any→PR→main→deploy; develop retired; feature/latest2 retired), pr-validate gates, auto-merge allowlist + PAT, concurrency guard, healthcheck gates, paths-ignore, crashloop abort. _(verified 2026-06-11)_
- **[Execution Environments](06_platform/05_environments.md)** — Three Claude execution profiles (interactive Max / autonomous ZAI / Cody Anthropic-default-since-2026-05-11), local dev (just dev), demo execution, model routing. _(verified 2026-06-10)_
- **["Networking: Tailscale, Residential Proxy, SSHFS"](06_platform/06_networking_tailscale_proxy_sshfs.md)** — Tailscale (uaonvps MagicDNS vs srv1360701 raw hostname), residential proxy (DataImpulse default/Webshare failover, VPS-only), SSHFS cross-machine mount. _(verified 2026-06-03)_
- **[Claude Max OAuth Credentials (CLAUDE_CODE_OAUTH_TOKEN)](06_platform/07_claude_max_oauth_credentials.md)** — CLAUDE_CODE_OAUTH_TOKEN in Infisical is the SSOT for Cody-on-Anthropic / demo builds; refresh runbook + gotchas. _(verified 2026-06-11)_
- **["ADR: Scheduling Substrate Redesign (deploy-resilient timers + read-only Mission Control)"](06_platform/08_scheduling_substrate_adr.md)** — Deploy-resilient scheduling substrate — two-axis substrate policy + per-job target table (31 crons), Mission Control sweeper extraction to its own service, deterministic proactive-health systemd timer + delivery contract, consolidations (reports/AM-products/mailer/DB), deploy-window-aware bounded backfill. _(verified 2026-06-10)_
- **["Agent Runbook: Reaching the Production VPS (read live state without fighting SSH)"](06_platform/09_agent_vps_access_runbook.md)** — Agent runbook for reaching the prod VPS read-only over the tailnet gateway API (no SSH) _(verified 2026-06-09)_
- **["ZAI Rate Limiter & Inference Governance"](06_platform/10_zai_rate_limiter.md)** — Canonical doc for ZAI inference governance: the ZAIRateLimiter concurrency/backoff/FUP control, the zai_observability httpx events hook, the zai_inference_health watchdog, and the half-adoption reality (most opus callers bypass the limiter). _(verified 2026-06-11)_
- **["ADR: Autonomous doc-drift issue triage & fix — three delivery options"](06_platform/11_autonomous_doc_triage_options_adr.md)** — Options for an autonomous Opus agent that triages/fixes the nightly doc-drift issues: (a) _(verified 2026-06-10)_
- **["ADR: Deploy-Restart Resilience for the Gateway (cut the churn, protect in-process work)"](06_platform/12_deploy_restart_resilience_adr.md)** — ADR: cut deploy-restart churn + protect in-process work across gateway restarts (coalesce queued deploys, drain/handoff in-flight turns). _(verified 2026-06-12)_

## 07_tools

_MCP server, tools/bridges, SDK integration, skills_

- **[MCP Server & Tools](07_tools/01_mcp_server_and_tools.md)** — FastMCP server, tool registry/discovery, bridge architecture, research pipeline tools, workspace resolution in tools, circuit breakers. _(verified 2026-06-12)_
- **[SDK Integration](07_tools/02_sdk_integration.md)** — Claude Agent SDK integration helpers, session history adapter, runtime info, task events, harness planning phase. _(verified 2026-06-09)_
- **[Skills System](07_tools/03_skills_system.md)** — Skills architecture, invocation by principals, dependency/gated-binary setup. _(verified 2026-06-12)_
- **[HyperFrames Video Generation (Studio + Pipeline)](07_tools/04_hyperframes_video_generation.md)** — Studio + pipeline (external project in Cody_Code_Generations/): beat-sheet pipeline, Agent SDK app, learning loop, widget strategy, UA adoption status (design-only). _(verified 2026-06-11)_

## 08_operations

_Operating playbook, verification rules, dormancy, VPS recovery, incident patterns_

- **[Agent Operating Playbook](08_operations/01_agent_operating_playbook.md)** — How agents should operate (operator-facing playbook). _(verified 2026-06-10)_
- **[Production Verification Rules](08_operations/02_production_verification_rules.md)** — Ship-then-verify cadence, /api/v1/version SHA check, branch-vs-deploy honesty, backend vs UI verification paths. _(verified 2026-06-10)_
- **[Dormancy & Operating Hours](08_operations/03_dormancy_and_operating_hours.md)** — 6AM-10PM Houston active window, content-gen vs infra-event distinction, documented exceptions, guard test. _(verified 2026-06-10)_
- **[VPS Recovery & Security](08_operations/04_vps_recovery_and_security.md)** — Watchdog/timers, service recovery, host hardening, daily ops. _(verified 2026-06-04)_
- **[Incident Response Patterns](08_operations/05_incident_response_patterns.md)** — Recurring incident classes + recovery: rogue autonomous branch, .venv corruption, gateway wedge, event-loop starvation. _(verified 2026-06-11)_
- **[Self-Improving CLAUDE.md Stop Hook](08_operations/06_self_improving_claude_md_hook.md)** — Desktop-local Stop hook that reflects on each session and drafts CLAUDE.md improvement proposals for review. _(verified 2026-06-03)_

---

_61/61 canonical docs present. Legacy point-in-time reports are archived (search-excluded) — see `00_DOCUMENTATION_REFACTOR_PLAN.md` §5._
