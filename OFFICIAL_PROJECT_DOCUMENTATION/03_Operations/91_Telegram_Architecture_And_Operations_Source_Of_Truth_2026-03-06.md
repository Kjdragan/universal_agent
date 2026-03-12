# 91. Telegram Architecture and Operations Source of Truth (2026-03-06)

## Deployment Status Note

The current application deployment contract for this repository is GitHub Actions, not manual VPS deploy scripts.

- Push or merge to `develop` to deploy to staging.
- Push or merge to `main` to deploy to production.
- Treat references here to `scripts/deploy_vps.sh` or `scripts/vpsctl.sh` as legacy or break-glass operational tooling only.
- See `AGENTS.md` and `docs/deployment/ci_cd_pipeline.md` for the canonical deployment path.

## Purpose

This document is the canonical source of truth for current Telegram usage in Universal Agent.

It covers both:
- the **interactive Telegram bot** used as a user-facing channel into the agent runtime
- Telegram as an **outbound notification and digest delivery surface** for CSI-related scripts and auxiliary tooling

It also distinguishes the **current implemented path** from older webhook-era helper material that still exists in the repository.

## Executive Summary

Telegram is currently used in three distinct ways:

1. **Interactive Telegram bot UI**
   - implemented in `src/universal_agent/bot/`
   - currently runs in **long-polling mode**, not webhook mode
   - can talk to the agent through either `ExternalGateway` or `InProcessGateway`

2. **Outbound Telegram notification/digest delivery**
   - used by CSI scripts to send RSS, Reddit, and tutorial digests or updates
   - supports per-stream chat and thread/topic routing

3. **Auxiliary Telegram tooling**
   - an MCP Telegram helper exists for send/debug operations
   - legacy webhook registration helpers still exist for local/dev or older flows

The most important current-state conclusion is:
- **the primary Telegram runtime today is the polling bot in `src/universal_agent/bot/main.py`**
- **webhook-oriented Telegram scripts and notes exist, but they are not the main current production runtime path**

## Current Production Architecture

## 1. Interactive Telegram Bot Runtime

Primary implementation:
- `src/universal_agent/bot/main.py`
- `src/universal_agent/bot/agent_adapter.py`
- `src/universal_agent/bot/task_manager.py`
- `src/universal_agent/bot/core/runner.py`
- `src/universal_agent/bot/core/middleware_impl.py`
- `src/universal_agent/bot/plugins/commands.py`
- `src/universal_agent/bot/normalization/formatting.py`

Current runtime entrypoint:
- `uv run python -m universal_agent.bot.main`

Current runtime mode:
- **long polling** using `python-telegram-bot`
- the bot calls `app.updater.start_polling()`
- production evidence docs also describe polling behavior via `getMe`, `deleteWebhook`, and `getUpdates`

### High-Level Bot Flow

1. Telegram update arrives via polling
2. update is fed into `UpdateRunner`
3. updates are processed sequentially per chat id
4. middleware chain performs logging, auth, session lookup, onboarding, and command routing
5. task manager queues agent work
6. `AgentAdapter` executes the request via gateway path
7. completion/error text is sent back to Telegram with bounded retry

## 2. Per-Chat Ordering and Task Execution Model

Primary implementation:
- `src/universal_agent/bot/core/runner.py`
- `src/universal_agent/bot/task_manager.py`

Current update handling model:
- `UpdateRunner` creates one queue per Telegram chat id
- each chat gets a dedicated worker to preserve sequential processing order

Current task handling model:
- `TaskManager` maintains a queue of agent tasks
- concurrency is bounded by `MAX_CONCURRENT_TASKS`
- each user may have only one active pending/running task at a time
- duplicate `/agent` spam from the same user is rejected until the active task completes

Current task states include:
- `pending`
- `running`
- `completed`
- `error`
- `canceled`

### Current Cancellation Behavior

Telegram currently supports:
- `/cancel [task_id]`

But the implementation distinction is important:
- pending tasks can be canceled
- already-running tasks cannot yet be force-canceled by the Telegram task manager itself

## 3. Auth and Allowlist Model

Primary implementation:
- `src/universal_agent/bot/config.py`
- `src/universal_agent/bot/core/middleware_impl.py`

Current auth model is simple and env-driven.

Primary env control:
- `TELEGRAM_ALLOWED_USER_IDS`

Behavior:
- if the allowlist is empty, Telegram auth middleware allows all users
- if the allowlist is populated, only listed Telegram user ids are accepted
- unauthorized users receive `⛔ Unauthorized access.`

This is a pragmatic allowlist, not a full tenant or role model.

## 4. Session Semantics

Primary implementation:
- `src/universal_agent/bot/agent_adapter.py`
- `src/universal_agent/bot/plugins/commands.py`
- `tests/bot/test_telegram_gateway.py`

Telegram session behavior is intentionally hybrid.

### Current Session ID Strategy

Continuation-oriented Telegram session id:
- `tg_<user_id>`

Workspace root for Telegram user lane:
- `AGENT_RUN_WORKSPACES/tg_<user_id>`

When creating a fresh gateway session, the adapter uses:
- `user_id="telegram_<user_id>"`
- `workspace_dir=<AGENT_RUN_WORKSPACES/tg_<user_id>>`
- `session_id="tg_<user_id>_<short_uuid>"` — unique per query for traceability
- session metadata includes `source: "telegram"` and `telegram_user_id: "<user_id>"`

The workspace is rooted at the stable `tg_<user_id>/` key for checkpoint continuity, while each session gets a unique ID for dashboard visibility and audit.

### Fresh vs Continue

Current command semantics:
- `/new` -> disable continuation mode for that user
- `/continue` -> enable continuation mode for that user

Actual runtime behavior:
- default path is **fresh-session-per-request plus checkpoint reinjection**
- when continuation mode is enabled, adapter first tries `resume_session("tg_<user_id>")` using the workspace key
- if resume fails, it falls back to fresh session creation

### Checkpoint Reinjection

If a prior Telegram workspace already exists, the adapter attempts to:
- load the latest session checkpoint
- convert it to markdown context
- inject it into the next fresh request inside `<prior_session_context>`

This is a key part of current Telegram semantics:
- continuity is often preserved by **checkpoint reinjection**, not only by resuming the exact same live session

## 5. Gateway Integration Model

Primary implementation:
- `src/universal_agent/bot/agent_adapter.py`

Telegram bot can currently operate in two execution modes.

### External Gateway Mode

Used when:
- `UA_GATEWAY_URL` is set

Behavior:
- bot connects to `ExternalGateway`
- gateway health check is attempted
- Telegram acts as a client of the main gateway runtime

### In-Process Gateway Mode

Used when:
- `UA_GATEWAY_URL` is not set
- `UA_TELEGRAM_ALLOW_INPROCESS=1`

Behavior:
- bot starts `InProcessGateway`
- local/dev execution can proceed without a separately running external gateway

This is practical for development, but it is not the same architecture as a centralized always-on gateway service.

## 6. Heartbeat Integration for Telegram

Primary implementation:
- `src/universal_agent/bot/agent_adapter.py`
- `src/universal_agent/bot/heartbeat_adapter.py`

Telegram has an integration path with the heartbeat system when running in the in-process path and when heartbeat is enabled.

Current behavior:
- a `send_message_callback` is injected into the adapter
- `HeartbeatService` can start with a Telegram-specific connection adapter
- resumed or newly created sessions can be registered for heartbeat tracking
- proactive bot sends go through the provided callback

This is a real current integration, but it is more tightly associated with the in-process path than with the external-gateway client model.

## 7. Telegram Command Surface

Primary implementation:
- `src/universal_agent/bot/plugins/commands.py`

Current supported user-facing commands:
- `/status`
- `/continue`
- `/new`
- `/cancel [task_id]`
- `/agent <prompt>`
- implicit non-command text -> treated as agent prompt

### Behavior Summary

- `/status` shows recent tasks and current session mode
- `/continue` enables continuation mode
- `/new` returns to fresh-session behavior
- `/cancel` cancels a pending task or reports inability to cancel a running one
- `/agent` queues work
- plain non-command text also queues work as an implicit agent request

## 8. Telegram Response Formatting and Delivery

Primary implementation:
- `src/universal_agent/bot/main.py`
- `src/universal_agent/bot/normalization/formatting.py`
- `tests/unit/test_telegram_formatter.py`

Current completion formatting behavior:
- includes execution time if present
- includes tool count if present
- includes code-execution marker if present
- includes Logfire trace link when `trace_id` exists
- escapes text for Telegram Markdown V2
- truncates long output to Telegram-friendly size

Current outbound send behavior:
- `_send_with_retry()` in `bot/main.py` delegates to the shared `telegram_send_async()` utility
- the shared utility (`src/universal_agent/services/telegram_send.py`) provides unified retry policy across all Telegram senders
- both async and sync variants are available for different contexts (gateway vs scripts)
- failures are logged with structured context (chat_id, attempt, error type)
- final exhaustion raises runtime error in the bot context

The shared send utility replaces four previously separate send mechanisms:
1. `bot/main.py` `_send_with_retry()` — now delegates to shared utility
2. CSI scripts `_send_telegram_message()` — can adopt shared utility (separate process)
3. `mcp_server_telegram.py` — now uses shared utility
4. `services/tutorial_telegram_notifier.py` — now uses shared utility

This is an important current hardening layer for Telegram reliability.

## 9. VPS Runtime and Service Posture

Primary operational references:
- `AGENTS.md`
- `docs/deployment/ci_cd_pipeline.md`
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/19_Universal_Agent_VPS_App_API_Telegram_Deployment_Explainer_2026-02-11.md`
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/24_VPS_Service_Recovery_System_Runbook_2026-02-12.md`
- `scripts/vps_service_watchdog.sh`
- `scripts/vpsctl.sh` for break-glass diagnostics only

Current documented VPS posture:
- Telegram runs as `universal-agent-telegram`
- it is treated as one of the core long-running services on VPS
- normal deploy/restart for Telegram is now driven by the GitHub Actions staging/production workflows
- `scripts/vpsctl.sh` remains available only for narrowly targeted diagnostics or emergency intervention

### Watchdog Behavior

Current watchdog treatment:
- `universal-agent-telegram` has **no default HTTP health probe**
- watchdog only checks active process state by default

This means Telegram on VPS has weaker liveness validation than services with HTTP health endpoints.

## 10. CSI Use of Telegram as Delivery Channel

Primary implementation:
- `CSI_Ingester/development/scripts/csi_rss_telegram_digest.py`
- `CSI_Ingester/development/scripts/csi_reddit_telegram_digest.py`
- `CSI_Ingester/development/scripts/csi_playlist_tutorial_digest.py`
- `CSI_Ingester/development/scripts/csi_rss_quality_gate.py`
- `CSI_Ingester/development/deployment/systemd/csi-ingester.env.example`

This is a separate major Telegram use in the repo.

Telegram here is not an interactive UI. It is a **delivery sink** for digests and updates.

### Current CSI Telegram Delivery Features

- RSS feed digest delivery
- Reddit digest delivery
- playlist tutorial update delivery
- optional per-stream chat separation
- optional per-stream Telegram thread/topic routing
- strict stream-routing mode to avoid accidental cross-posting across channels

### Current CSI Telegram Env Surface

Common controls include:
- `CSI_RSS_TELEGRAM_CHAT_ID`
- `CSI_REDDIT_TELEGRAM_CHAT_ID`
- `CSI_TUTORIAL_TELEGRAM_CHAT_ID`
- `CSI_RSS_TELEGRAM_THREAD_ID`
- `CSI_REDDIT_TELEGRAM_THREAD_ID`
- `CSI_TUTORIAL_TELEGRAM_THREAD_ID`
- `CSI_TELEGRAM_STRICT_STREAM_ROUTING`
- `CSI_REDDIT_TELEGRAM_STRICT_STREAM_ROUTING`
- `CSI_TUTORIAL_TELEGRAM_STRICT_STREAM_ROUTING`
- `CSI_RSS_TELEGRAM_BOT_TOKEN`
- `CSI_REDDIT_TELEGRAM_BOT_TOKEN`
- `CSI_TUTORIAL_TELEGRAM_BOT_TOKEN`

Fallbacks often resolve back to shared controls such as:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_DEFAULT_CHAT_ID`

This means Telegram is currently both:
- a direct user interface surface
- an outbound operational notification channel

## 11. MCP Telegram Toolkit

Primary implementation:
- `src/universal_agent/mcp_server_telegram.py`

Current tools:
- `telegram_send_message`
- `telegram_get_updates`

Important current constraint:
- `telegram_get_updates` is explicitly deprecated for normal use because it can conflict with the main running poller
- it should only be used for debugging when the main Telegram bot is stopped

## 12. Legacy or Stale Webhook-Era Material

Primary implementation/examples:
- `start_telegram_bot.sh`
- `scripts/register_webhook.py`
- older investigation notes in `heartbeat/07_Telegram_UI_Investigation.md`

These files show an older or local-dev-oriented webhook path involving:
- ngrok
- `WEBHOOK_URL`
- `WEBHOOK_SECRET`
- Docker-based startup helper behavior
- explicit webhook registration through Telegram API

### Current Interpretation

This material should be treated as:
- **legacy** or **local-dev helper** behavior
- **not the main current production runtime path**

Reason:
- current main bot runtime uses polling in `src/universal_agent/bot/main.py`
- current official run docs explicitly describe polling mode
- earlier investigation notes that describe webhook support are stale relative to current implementation

## Canonical Environment Controls

Interactive Telegram bot:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_USER_IDS`
- `UA_TELEGRAM_TASK_TIMEOUT_SECONDS`
- `UA_GATEWAY_URL`
- `UA_TELEGRAM_ALLOW_INPROCESS`
- `MAX_CONCURRENT_TASKS`

Webhook-era / legacy helper controls still present in repo:
- `WEBHOOK_URL`
- `WEBHOOK_SECRET`

Related storage/runtime defaults:
- `UA_WORKSPACES_DIR`

CSI Telegram delivery:
- `CSI_RSS_TELEGRAM_CHAT_ID`
- `CSI_REDDIT_TELEGRAM_CHAT_ID`
- `CSI_TUTORIAL_TELEGRAM_CHAT_ID`
- `CSI_RSS_TELEGRAM_THREAD_ID`
- `CSI_REDDIT_TELEGRAM_THREAD_ID`
- `CSI_TUTORIAL_TELEGRAM_THREAD_ID`
- `CSI_RSS_TELEGRAM_BOT_TOKEN`
- `CSI_REDDIT_TELEGRAM_BOT_TOKEN`
- `CSI_TUTORIAL_TELEGRAM_BOT_TOKEN`
- `CSI_TELEGRAM_STRICT_STREAM_ROUTING`
- `CSI_REDDIT_TELEGRAM_STRICT_STREAM_ROUTING`
- `CSI_TUTORIAL_TELEGRAM_STRICT_STREAM_ROUTING`

## What Is Actually Implemented Today

### Implemented and Current

- polling-based Telegram bot runtime
- per-chat ordered update processing
- per-user active-task guard
- fresh-session-plus-checkpoint Telegram semantics with optional `/continue` resume attempt
- external-gateway or in-process execution path
- bounded retry on outbound Telegram sends
- Telegram formatter with trace-link support
- Telegram as CSI digest/notification delivery channel

### Present but Not the Main Current Runtime Path

- webhook registration helper scripts
- ngrok-based startup flow
- webhook env vars in `.env.sample`
- older notes assuming webhook-first Telegram operation

## Current Gaps and Cleanup Opportunities

1. **Env naming drift**
   - current code uses `TELEGRAM_ALLOWED_USER_IDS`
   - `.env.sample` currently exposes `ALLOWED_USER_IDS`
   - this should be unified

2. **Webhook drift in docs/helpers**
   - webhook-era scripts remain in repo, but polling is the current runtime truth
   - older Telegram notes still describe webhook support inaccurately relative to current main bot code

3. **Service definition visibility gap**
   - operational docs reference `universal-agent-telegram` heavily, but the tracked service unit definition is not surfaced as clearly as some other deployment assets

4. **No default HTTP health probe for Telegram service**
   - watchdog only checks active-state by default
   - a logically stuck but still-active Telegram process may not be caught quickly

5. **Auth model is intentionally simple**
   - allowlist-only gating is workable, but not a richer identity model

6. **Session semantics remain specialized**
   - Telegram uses its own fresh-session/checkpoint-reinjection model rather than matching the web UI session model exactly

## Source Files That Define Current Truth

Primary interactive bot implementation:
- `src/universal_agent/bot/main.py`
- `src/universal_agent/bot/config.py`
- `src/universal_agent/bot/agent_adapter.py`
- `src/universal_agent/bot/task_manager.py`
- `src/universal_agent/bot/core/runner.py`
- `src/universal_agent/bot/core/middleware_impl.py`
- `src/universal_agent/bot/plugins/commands.py`
- `src/universal_agent/bot/normalization/formatting.py`

Tests:
- `tests/bot/test_telegram_gateway.py`
- `tests/unit/test_telegram_formatter.py`

Related operational docs:
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/46_Running_The_Agent.md`
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/44_Telegram_Functionality_Implementation_Plan_2026-02-18.md`
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/19_Universal_Agent_VPS_App_API_Telegram_Deployment_Explainer_2026-02-11.md`
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/24_VPS_Service_Recovery_System_Runbook_2026-02-12.md`

Legacy/helper material:
- `start_telegram_bot.sh`
- `scripts/register_webhook.py`
- `heartbeat/07_Telegram_UI_Investigation.md`

CSI Telegram delivery:
- `CSI_Ingester/development/scripts/csi_rss_telegram_digest.py`
- `CSI_Ingester/development/scripts/csi_reddit_telegram_digest.py`
- `CSI_Ingester/development/scripts/csi_playlist_tutorial_digest.py`
- `CSI_Ingester/development/deployment/systemd/csi-ingester.env.example`

Auxiliary tool surface:
- `src/universal_agent/mcp_server_telegram.py`

## Bottom Line

The canonical current Telegram story in Universal Agent is:
- **an interactive polling-based Telegram bot** feeding the agent through gateway/in-process paths
- **simple allowlist-based operator access**
- **fresh-session-plus-checkpoint semantics with optional continuation**
- **Telegram as a secondary delivery channel for CSI digests and alerts**
- **legacy webhook-era helpers still present, but no longer the main runtime truth**
