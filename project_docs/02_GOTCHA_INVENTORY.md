---
title: Gotcha & Operational-Fact Inventory
status: active
canonical: true
subsystem: meta-documentation
code_paths: []
last_verified: 2026-05-30
---

# Gotcha & Operational-Fact Inventory

> Harvested from the legacy corpus during Phase 1 (workflow `wf_c3afe272-598`). These are the
> **non-code-shaped** facts worth preserving — operational/environmental knowledge and design rationale
> that a pure code review would miss. Each Phase-2 reconstruction agent receives the items relevant to
> its doc and must apply the preservation rule from the refactor plan §4:
> - **operational** facts → judged for *current validity*; preserved if still true & important.
> - **rationale** facts → carried as asserted context (the *why*), marked not-code-verified.
> - `appears_still_valid: no/uncertain` items get extra scrutiny; genuinely-uncertain → flag to operator.

**Counts:** 61 operational, 11 rationale. (68 code-behavior gotchas omitted here — those are re-derived directly from code.) *Harvest was 59 operational; +2 added 2026-05-30 (CSI category vocabulary, OMC worktree-cancel).*

## Operational / environmental facts

### Still valid (preserve) — 55

- Dual Claude environments on VPS: ZAI-mapped (default, cheap GLM models) vs Anthropic-native (/opt/ua_demos/, real Claude Max plan OAuth). Mistaking one for the other is the #1 source of confusion.
  - *source:* `docs/README.md § Dual Claude Environments on VPS`
- Heartbeat does NOT own trusted email mission execution. That work routes through Task Hub and dedicated ToDo dispatcher. Heartbeat is responsible for health supervision and proactive checks only.
  - *source:* `docs/02_Subsystems/Heartbeat_Service.md § 1`
- Mission Control Operator Brief panel removed Phase 8 (2026-05-04): /api/v1/dashboard/situations endpoint marked deprecated for one release cycle (with warning log), then can be deleted. COS readout now uses tier-1 cards + tier-2 synthesis.
  - *source:* `docs/02_Subsystems/Mission_Control_Intelligence_System.md`
- Z.AI proxy is capacity-limited during Greater-China peak hours (UTC+8 daytime), which maps to US night under America/Chicago cron timezone — the inverse of 'run heavy batch overnight' intuition. Pick cron windows carefully.
  - *source:* `docs/README.md § 2026-05-08 Z.AI Peak-Time Scheduling Finding`
- CSI Database split-brain: resolve via _csi_default_db_path(), never assume the dev relic csi.db location. Always use canonical function to get live-state.
  - *source:* `docs/02_Subsystems/CSI_YouTube_Flow_2026-05-29.md § CSI_YouTube_Flow`
- Ghost.build MCP cleanup contract: Cody records DBs in manifest.json.ghost_databases and ghost_delete on success; operator weekly reconciles 'ghost list' to protect 100hr/mo free cap.
  - *source:* `docs/02_Subsystems/Ghost_Build_Cody_Demo_Postgres.md`
- Interactive claude defaults to Anthropic Max; zai function for GLM routing on VPS (2026-05-07 update to deployment explainer)
  - *source:* `/docs/03_Operations/19_Universal_Agent_VPS_App_API_Telegram_Deployment_Explainer_2026-02-11.md`
- GitHub Actions is now primary deployment path (develop→staging, main→production); references to vpsctl.sh and deploy_vps.sh should be read as legacy or break-glass tooling
  - *source:* `/docs/03_Operations/76_Sandbox_Permissioning_And_Exception_Profile_2026-02-23.md`
- AgentMail webhook transform exists but production email ingress uses WebSocket path; if webhooks reactivated, they need reply-extraction parity with WebSocket (2026-03-06 canonical review identified this gap)
  - *source:* `/docs/03_Operations/83_Webhook_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`
- ZAI routing keys added 2026-05-07: ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN, ANTHROPIC_DEFAULT_HAIKU_MODEL, ANTHROPIC_DEFAULT_SONNET_MODEL, ANTHROPIC_DEFAULT_OPUS_MODEL loaded from Infisical, never written to .env on disk
  - *source:* `/docs/03_Operations/85_Infisical_Secrets_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`
- Residential proxy: Desktop transcript worker decommissioned April 2026; all transcript fetching now runs on VPS via youtube_ingest.py with residential proxy (DataImpulse default, Webshare failover)
  - *source:* `/docs/03_Operations/86_Residential_Proxy_Architecture_And_Usage_Policy_Source_Of_Truth_2026-03-06.md`
- Tailscale: VPS has two different hostnames — raw OS: srv1360701 (Hostinger), Tailscale MagicDNS: uaonvps; device_roles.json uses srv1360701 (admin API indexes by raw hostname); SSH/preflight scripts use uaonvps
  - *source:* `/docs/03_Operations/87_Tailscale_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`
- Tailscale: Kevin's primary development workflow as of 2026-05-07 is Antigravity Remote-SSH from mint-desktop to ua@uaonvps, opening /home/ua/dev/universal_agent as interactive workspace; Tailscale is hard dependency for daily development
  - *source:* `/docs/03_Operations/87_Tailscale_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`
- SSHFS cross-machine file resolution (added 2026-04-23): VPS systemd mount unit mounts workstation /home/kjdragan at same path via SSHFS over Tailscale using id_ed25519 key; enables transparent file reference by local paths
  - *source:* `/docs/03_Operations/87_Tailscale_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`
- ACL troubleshooting runbook added 2026-04-20; key finding: many Tailscale SSH issues are transient (session re-auth, tag sync delays, node key rotation) and self-resolve; always verify current state before investing in code remediation
  - *source:* `/docs/03_Operations/87_Tailscale_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`
- Telegram: polling-based bot, not webhook-based; legacy webhook registration helpers (start_telegram_bot.sh, scripts/register_webhook.py) exist but should be treated as stale relative to current main bot code in bot/main.py
  - *source:* `/docs/03_Operations/91_Telegram_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`
- Cleanup P0 item: Remove dev-secret fallback for dashboard session signing; currently falls back to hardcoded ua-dashboard-dev-secret when no explicit secret configured (real security gap if reached in hardened deployment)
  - *source:* `/docs/03_Operations/93_Prioritized_Cleanup_Plan_From_Canonical_Review_2026-03-06.md`
- Cleanup P0 item: Telegram env variable naming drift — code reads TELEGRAM_ALLOWED_USER_IDS but .env.sample documents ALLOWED_USER_IDS (without prefix); silent misconfiguration risk
  - *source:* `/docs/03_Operations/93_Prioritized_Cleanup_Plan_From_Canonical_Review_2026-03-06.md`
- Architectural concern: Telegram is the only UI channel bypassing gateway session model; uses Telegram-specific session-id scheme (tg_<user_id>), checkpoint-reinjection, workspace convention; diverges from gateway session model
  - *source:* `/docs/03_Operations/94_Architectural_Integration_Review_From_Canonical_Review_2026-03-06.md`
- Architectural concern: Three separate trust surfaces (dashboard auth via cookie+HMAC, ops auth via UA_OPS_TOKEN, CSI ingest auth via HMAC signature) do not share common abstraction; dashboard proxy injects ops tokens on behalf of authenticated users
  - *source:* `/docs/03_Operations/94_Architectural_Integration_Review_From_Canonical_Review_2026-03-06.md`
- Architectural concern: Factory heartbeat and CSI delivery health are parallel liveness models that don't inform each other; if gateway goes down, both fail but neither tells the other
  - *source:* `/docs/03_Operations/94_Architectural_Integration_Review_From_Canonical_Review_2026-03-06.md`
- Architectural concern: Run workspace ownership enforced by convention (API-level) not filesystem; no per-workspace access control — any process with access to AGENT_RUN_WORKSPACES can read another workspace's files
  - *source:* `/docs/03_Operations/94_Architectural_Integration_Review_From_Canonical_Review_2026-03-06.md`
- Heartbeat auto-triage: non-OK heartbeats now dispatched to Simone for investigation with structured findings contract (heartbeat_findings_latest.json); Simone owns remediation decision with assumption she can fix most bounded coding issues autonomously
  - *source:* `/docs/03_Operations/95_Heartbeat_Issue_Mediation_And_Auto_Triage_2026-03-12.md`
- Runtime cron store at AGENT_RUN_WORKSPACES/cron_jobs.json differs from schedule_nightly_wiki.py default (workspaces/); nightly_wiki and 3x daily proactive reports not registered in live runtime cron store
  - *source:* `docs/03_Operations/115_Proactive_Automation_Current_State_Audit_2026-04-18.md § 2`
- Two YouTube watching systems exist with separate databases: UA-native playlist watcher (state in youtube_playlist_watcher_state.json) and CSI RSS channel feed (state in csi.db); resetting one does NOT reset the other
  - *source:* `docs/04_CSI/CSI_Master_Architecture.md § 8`
- Proactive health endpoint uses fixed cron job csi_convergence_sync registered at gateway startup when UA_CSI_CONVERGENCE_CRON_ENABLED=1; runs every 30 minutes by default
  - *source:* `docs/04_CSI/CSI_Convergence_Intelligence_Pipeline.md § 3`
- Lifecycle miss during deploy window (SIGTERM/143 exit) should be reclassified as warning severity with dashboard-only routing, not email/telegram, to suppress deploy-restart noise; failure record and assignment reopen unchanged
  - *source:* `docs/03_Operations/106_TaskStop_Guardrails_And_Task_Hub_Execution_Hardening_2026-03-31.md § 8.1`
- Deploy-time service restart is already part of .github/workflows/deploy.yml (runs systemctl restart after rsync); gateway picks up Python changes by construction, verified via GET /api/v1/version SHA check
  - *source:* `docs/03_Operations/130_Production_Verification_Rules.md § Rule D`
- Infisical's `development` environment often mirrors `production` for parity; Phase D (2026-05-11) defends by ignoring truthy `UA_*_ENABLED` in dev and requiring explicit `UA_DEV_<NAME>_FORCE_ON=1` opt-in.
  - *source:* `docs/06_Deployment_And_Environments/12_Local_Dev_Environment.md`
- Production deploy workflow writes bootstrap `.env` from scratch on every deploy (deterministic key set); stale historical lines cannot survive lane migration.
  - *source:* `docs/deployment/ci_cd_pipeline.md`
- `pr-auto-merge.yml` uses fine-grained PAT (`AUTO_MERGE_PAT`) instead of GITHUB_TOKEN because GITHUB_TOKEN's resulting push events are suppressed and don't trigger `deploy.yml`; PAT-driven push fires deploy normally.
  - *source:* `docs/deployment/ci_cd_pipeline.md`
- Deploy workflow has concurrency guard (`deploy-production` group, `cancel-in-progress: false`) as of 2026-05-11 PM; simultaneous merges queue instead of racing on `.git/index.lock`.
  - *source:* `docs/deployment/ci_cd_pipeline.md`
- MCP credential placeholders in `.mcp.json` MUST use `${VAR}` syntax; literal token (Hostinger token, Jan-May 2026) sat in git for 78 days; claude_with_mcp_env.sh launcher resolves placeholders via initialize_runtime_secrets().
  - *source:* `docs/deployment/secrets_and_environments.md`
- Next.js webui `.env.local` is created fresh on every deploy (from Infisical via render_service_env_from_infisical.py); Anthropic SDK has no Infisical integration so deploy-time rendering is the required pattern.
  - *source:* `docs/deployment/secrets_and_environments.md`
- Gateway resource limits: MemoryMax 8G, MemoryHigh 6G, TasksMax 500, OOMPolicy continue (child kills OK, gateway survives). Prevents runaway memory + fork bombs; lives in both template + VPS override.
  - *source:* `docs/deployment/architecture_overview.md`
- `feature/latest2` was the 'pseudo-trunk' from 2026-05-11 through 2026-05-14; retired PR #273 (deleted locally + on origin). Now every PR branches from `main`, lands on `main`, deploys. Session Baseline Cleanup auto-lands new sessions on fresh `main`.
  - *source:* `docs/deployment/ai_coder_instructions.md`
- VPS deploy expected times: cold npm build ~20-25 min, normal deploy (no package.json change) ~10-15 min, with npm reinstall ~15-20 min. Workflow timeout-minutes: 35.
  - *source:* `docs/deployment/ci_cd_pipeline.md`
- HTTP healthcheck gates on deploy: gateway (96 attempts × 5s = 8 min timeout), API, webui. Crashloop abort (2026-05-28): if service restarts ≥5 times while polling, exit immediately instead of timeout.
  - *source:* `docs/deployment/ci_cd_pipeline.md`
- Production .venv corruption (2026-03-12 incident): stale symlink to inaccessible interpreter path prevents `uv sync` even when run as correct user; fix is deploy's selective `.venv` removal + rebuild.
  - *source:* `docs/06_Deployment_And_Environments/06_Production_Deploy_Incident_2026-03-12.md`
- `/proc/<pid>/environ` shows exec-time env only, not runtime os.environ mutations; unreliable for verifying Infisical injection in long-running processes; use in-process verify or end-to-end endpoint behavior.
  - *source:* `docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md`
- Autonomous mission worktree pattern (Tier 2): syntax-check + unit-test BEFORE committing (not relying on CI); use reusable blocks (vp/worktree_utils.py, autonomous_mission_executor.py); never edit /opt/universal_agent/src directly.
  - *source:* `docs/deployment/ai_coder_instructions.md`
- CLAUDE_CODE_OAUTH_TOKEN in Infisical is the canonical Cody-on-Anthropic credential; `/home/ua/.claude/.credentials.json` on VPS is orphan state from old interactive session; nothing in production reads it.
  - *source:* `docs/06_Deployment_And_Environments/13_Claude_Max_OAuth_Credentials.md`
- Simone heartbeat executes autonomously and can run unconstrained in production checkouts. The codie/docstring-cleanup-task-hub branch was deployed without PR review, introduced a SyntaxError mid-flight in durable/state.py, crashed the 08:00 CDT CSI cron, and was only recovered by stopping the gateway, parking the task with careful SQL (not just 'cancel' which gets resurrected by orphan-reconciler), resetting to origin/main, and manual verification fire.
  - *source:* `docs/operations/2026-05-07_codie_rogue_branch_recovery.md`
- The canonical Task Hub DB is NOT /opt/universal_agent/AGENT_RUN_WORKSPACES/task_hub.db (stale, most recent mtime 2026-05-01) but /opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db, resolved at runtime via durable/db.py:get_activity_db_path(). Prior handoff docs named the wrong path.
  - *source:* `docs/operations/2026-05-07_codie_rogue_branch_recovery.md § Dead end 2`
- The 'develop' branch was retired 2026-05-10 (PR #181). The old chain was feature/latest2 → develop → main; it's now any feature branch → PR → main → deploy. /ship no longer targets develop. Multiple PRs (#193-197) were misdirected to feature/latest2 and never reached main; they were recovered via cherry-pick recovery PR #198.
  - *source:* `docs/operations/2026-05-11_autonomous_pr_and_deploy_flow_briefing.md § 4, docs/reports/hermes_continued.md § 7`
- deploy.yml has paths-ignore filter (docs/**, **.md, reports/**, state/**, artifacts/**, memory/**) that prevents docs-only or state-only commits to main from triggering a deploy. Mixed code+docs commits still trigger deploy — the safe default.
  - *source:* `docs/operations/2026-05-11_autonomous_pr_and_deploy_flow_briefing.md § 5`
- Z.AI (the LLM proxy used by all UA autonomous loops) has customer base concentrated in Greater China. Peak demand is Beijing business hours (16:00–22:00 CST), which overlaps with US Central night (00:00–10:00 CDT = 05:00–15:00 UTC). Running heavy cron jobs overnight US time hits Z.AI capacity limits. 9 of 12 system crons currently fire during China peak.
  - *source:* `docs/operations/2026-05-08_zai_peak_time_scheduling.md § 1–2`
- Hostinger API token (value starting 'ei5J...') was committed to .mcp.json:33 from 2026-02-19 to 2026-05-08 (78 days) in commits visible to anyone who cloned the repo. The literal remains in git history on branches feature/latest2, develop (pre-retirement), and all 119 refs containing those commits. Revocation at Hostinger is the complete fix; history rewriting is optional but operationally expensive (requires all collaborators to re-clone, CI re-run, VPS reset).
  - *source:* `docs/operations/2026-05-08_hostinger_token_remediation.md § 1–2`
- /opt/universal_agent is both production target (deploy.yml syncs to it) AND agent scratch directory (autonomous missions run in-place, Claude sessions write .claude/ artifacts, MCP servers write state). No automatic cleanup. Pollution accumulates: untracked .py.bak, test stubs, scratch markdown, .claude/session_work_products/, crashed-agent half-outputs. /ship auto-commit assumes mostly-clean working tree; on /opt/ua/ it captures gigabytes of junk. Habit fix: run /ship from ~/dev/universal_agent (clean checkout) instead.
  - *source:* `docs/operations/2026-05-09_ship_pollution_and_phase1_followups.md § Issue 1`
- pr-auto-merge.yml auto-enables auto-merge on PRs from `claude/*` branches to `main`. Prior `develop` retirement, this automation did NOT exist; operators had to manually enable auto-merge or use /ship. Now, any `claude/*` PR gets auto-merge enabled automatically. Non-claude branches require manual enable (the safe default for operator-driven PRs).
  - *source:* `docs/operations/2026-05-11_autonomous_pr_and_deploy_flow_briefing.md § 3`
- Anthropic-native Fallback for emergencies: /opt/ua_demos/ environment runs on Anthropic native API (not Z.AI proxy), so it is immune to Z.AI peak-time throttling. Can be used as an emergency override if a phase-boundary backfill absolutely must run during China peak. Cost tradeoff needed before enabling.
  - *source:* `docs/operations/2026-05-08_zai_peak_time_scheduling.md § 6 Open Q 5`
- ZAI content-safety (error 1301) silently drops large/sensitive buckets (fail-closed). Phase 0 verification (2026-05-29) saw 29-video bucket dropped on YouTube convergence run. Phase 2 (resilience) decision: accept the drop (no retry/reroute), keep fail-closed, just ensure the drop is logged not silent. Political/conflict convergences that trip guardrail will not surface — accepted tradeoff.
  - *source:* `docs/proactive_signals/insight_pipeline_completion_spec_2026-05-29.md § 2, § 12 (Decisions C)`
- Hermes Phase A.1/A.2/B.1 were initially PR'd to feature/latest2 (PRs #193-197) under the old branch model assumption. They never reached main. Recovery required opening a single PR (#198) targeting main directly, cherry-picking the 3 code commits, dropping the wrong-direction auto-promote workflow, and merging. feature/latest2 is now stale. All new feature work branches off origin/main directly.
  - *source:* `docs/reports/hermes_continued.md § 7`
- CSI `rss_event_analysis.category` is a **single-token** enum the live classifier emits (`ai_coding`, `ai_models`, `ai_news_and_business`, `ai_business`, `ai_applications`, `software_engineering`, `technology`, `geopolitics`, `conflict`, `economics`, `cooking`, `personal_health`, `noise`, `other_signal`, `longform_interviews`, `from`), NOT the compound taxonomy (`geopolitics_and_conflict`, `ai_coding_and_agents`) a handoff/prompt may assert. The ideation relevance gate's first version (PR #592) used the compound tokens, matched almost nothing, and silently leaked ~290 geopolitics/conflict/economics rows into the ideation corpus; PR #594 corrected it. The values are classifier-defined (CSI Ingester, a separate deploy unit), not enforced by a UA-code enum, so they can drift. **Lesson:** verify any category-based gate with `SELECT category, COUNT(*) FROM rss_event_analysis GROUP BY category` against the live `csi.db` before trusting a doc/prompt/handoff's claimed vocabulary. `technology` is intentionally kept despite being mixed (real dev content + occasional politics) — coarse category gating can't split it.
  - *source:* PR #594; `proactive_convergence.py::_DEFAULT_RELEVANCE_DENYLIST`; live `csi.db` 2026-05-30; `04_intelligence/01_csi_architecture.md § 3.1`
- OMC (oh-my-claudecode) ralph / persistent-mode state started **inside a git worktree** lives in the *worktree's* `.omc/state/sessions/<id>/`, but the `state_clear` MCP tool and `/oh-my-claudecode:cancel` always resolve the *main repo's* `.omc` (their `resolveOmcStateRoot`/`workingDirectory` does not follow into the worktree). A ralph loop started in a worktree therefore **cannot be cancelled by the normal command** and spins to the 2-hour staleness timeout. Recovery: remove the worktree's `ralph-state.json` + linked `ultrawork-state.json` directly (and write a `cancel-signal-state.json`). Do NOT `cancel --force` when other sessions are active — it wipes their state too.
  - *source:* this session 2026-05-30 (debugged `persistent-mode.mjs` Stop hook + `scripts/lib/state-root.mjs::resolveOmcStateRoot`)

### Uncertain (verify before preserving) — 5

- Proactive Pipeline architecture (doc) describes target state but several producer lanes are not fully wired end-to-end as of 2026-04-18: reflection mode and signal curation don't call promotion helpers to create Task Hub work.
  - *source:* `docs/02_Subsystems/Proactive_Pipeline.md § WARNING`
- Memory system: core_blocks in agent_core.db are stale (13+ days old in Feb 2026); 0 rows in processed_traces; memory/index.json doesn't exist on VPS; ProcessTurnAdapter.close() has NO memory flush
  - *source:* `/docs/03_Operations/30_Memory_System_Architecture_And_Health_2026-02-13.md`
- gws auth on VPS (headless) via Infisical: four base64-encoded secrets (GWS_CREDENTIALS_ENC_B64, GWS_TOKEN_CACHE_B64, GWS_ENCRYPTION_KEY_B64, GWS_CLIENT_SECRET_JSON_B64) materialized to ~/.config/gws/
  - *source:* `/docs/03_Operations/82_Email_Architecture_And_AgentMail_Source_Of_Truth_2026-03-06.md`
- UNRESOLVED: OAuth app in Testing mode expires refresh tokens ~7 days; must publish to Production for durable fix (mentioned in 2026-03-06 email doc, unresolved as of 2026-05-28)
  - *source:* `/docs/03_Operations/82_Email_Architecture_And_AgentMail_Source_Of_Truth_2026-03-06.md`
- Archon's claude subprocess will hit Anthropic Max budget (not Z.ai) unless wrapped with Infisical secrets or env vars explicitly exported; 2026-05-07 Phase B inversion removed ANTHROPIC_* from ~/.claude/settings.json
  - *source:* `docs/03_Operations/124_Archon_Integration_And_Operations_Guide_2026-04-25.md § 2`

### Likely stale (preserve only if re-confirmed) — 1

- CSI legacy firehose: detect_and_queue_convergence → insight_detection is deprecated (698 cancelled / 1 completed / 30 parked, 0.14% completion). Cleanup is tracked as PR E. Still reachable from two hand-trigger endpoints until PR E lands.
  - *source:* `docs/02_Subsystems/CSI_YouTube_Flow_2026-05-29.md`

## Design rationale (the WHY)

- Database is absolute source of truth for database paradigms, schema structure, segregation boundaries, activity notification lifecycle, and pruning logic. Multiple sources of scattered DB guidance are subordinate.
  - *source:* `docs/01_Architecture/Database_Architecture.md (referenced in README)`
- VP Goal Integration PRD is DRAFT, awaiting operator approval. Describes universal self-briefing (BRIEF.md), completion attestation (COMPLETION.md), and Simone-mediated failure rescue with three new tools. NOT YET IMPLEMENTED.
  - *source:* `docs/01_Architecture/12_VP_Goal_Integration_And_Failure_Rescue_PRD.md`
- Archon Comparison (2026-04-25) evaluates Git Worktree isolation emulation vs UA; async YAML-driven coding service integration. Decision status and integration strategy still current.
  - *source:* `docs/01_Architecture/11_Archon_Comparison_And_Integration_Strategy_2026-04-25.md`
- VP General interim path was implemented; full SessionContext refactor remains future milestone; decision deferred since 2026-03-02
  - *source:* `/docs/03_Operations/16_Concurrency_Conflict_Root_Cause_And_VP_General_Interim_Path_2026-03-02.md`
- Google Workspace integration: Strategy C hybrid direct+Composio design was prototyped in src/universal_agent/services/google_workspace/ but NEVER deployed; gws CLI released 2026-03-06 obsoletes it
  - *source:* `/docs/03_Operations/80_Google_Workspace_Integration_Retrospective_Memo_2026-03-06.md`
- Factory delegation: hybrid model uses Redis Streams for transport and SQLite for local execution queue; this separation is intentional (Redis is NOT the state machine)
  - *source:* `/docs/03_Operations/88_Factory_Delegation_Heartbeat_And_Registry_Source_Of_Truth_2026-03-06.md`
- Heartbeat auto-triage policy: all non-OK findings auto-dispatch Simone; Simone uses memory and reasoning to decide autonomous remediation; operator review required for destructive, security, or approval-bound fixes; known safe fixes bypass email and become Task Hub items
  - *source:* `/docs/03_Operations/95_Heartbeat_Issue_Mediation_And_Auto_Triage_2026-03-12.md`
- Proactive health watchdog shipped with empty crons[] for four PRs (2026-04-15 through 2026-05-06) because gateway handler never called CronService.list_jobs(); identical class of bug repeated in PR #376->PR #392 (wrong DB path used twice)
  - *source:* `docs/03_Operations/133_Agent_Operating_Playbook.md § 1.1-1.2`
- Backend logic verification should use direct Python invocation (PYTHONPATH=src uv run python -c ...) before production smoke; browser verification is only meaningful AFTER deploy, not before
  - *source:* `docs/03_Operations/130_Production_Verification_Rules.md § Rule B`
- Stale task policy (Task Hub retry exhaustion, anti-starvation gates) exists; orphaned in_progress tasks should be reset by rehydrate verbs (Hermes Phase B.1: rehydrate/re_evaluate/redirect_to/request_revision), not by writing manual directives
  - *source:* `docs/03_Operations/130_Production_Verification_Rules.md § Anti-pattern 5`
- The dormancy window (6 AM–10 PM Houston) is a cost/quota-conservation policy, not a work freeze. Detection work can run overnight (Dormancy Phase 0 re-ran overnight 2026-05-29), but digest *delivery* must respect operator reading hours. The distinction between 'work frozen' and 'output delivery delayed' is critical.
  - *source:* `docs/proactive_signals/insight_pipeline_completion_spec_2026-05-29.md § Assumptions`
