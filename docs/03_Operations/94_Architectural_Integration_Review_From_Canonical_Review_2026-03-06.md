# 94. Architectural and Integration Review From Canonical Review (2026-03-06)

## Purpose

This document is a strategic architectural review of how Universal Agent's subsystems integrate with each other. It was produced after creating 12 canonical source-of-truth documents covering every major subsystem.

Unlike the companion cleanup plan (document 93), this document is not about individual bugs or small fixes. It is about whether the **overall integration of subsystems is coherent**, whether any approaches are incongruous with each other now that all systems are visible together, and whether there are structural improvements that would make the architecture more aligned without rewriting what already works.

**This document is designed for handoff.** A coder picking this up does not need the history of the review that produced it. Each observation includes enough context to drive independent investigation and decision-making.

## How This Review Was Conducted

The reviewer read every authoritative implementation file for: auth/session security, factory delegation, runtime bootstrap, artifacts/workspaces, Telegram, CSI, Tailscale, webhooks, WebSockets, Infisical secrets, residential proxy, and email/AgentMail. The 12 canonical docs produced from that review are listed at the end of this document.

This review looks at the **system as a whole** — not just each subsystem in isolation.

---

## Part 1: What Is Well-Aligned Today

Before listing concerns, it is important to acknowledge what is working well architecturally. These are not suggestions; they are affirmations that the following integration choices are sound and should be preserved.

### 1.1 Runtime Bootstrap Is Centralized

The bootstrap pipeline (`runtime_bootstrap.py` → `infisical_loader.py` → `runtime_role.py`) is used consistently by the gateway, the core agent, the agent setup, and the factory bridge. This means there is a single contract for "what kind of node is starting and how strictly must it source secrets."

**Verdict:** This is good. Protect this centralization.

### 1.2 Factory Delegation Uses Redis as Transport and SQLite as Local State

The decision to keep Redis as transport and SQLite as the local execution queue is architecturally clean. It avoids making Redis the state machine while still getting cross-machine work distribution.

**Verdict:** This is good. Do not collapse these into a single store.

### 1.3 CSI -> UA Delivery Is Signed and Contract-Validated

CSI does not bypass auth. It delivers through a real HTTP endpoint with shared-secret + signature + timestamp validation. UA validates events against a typed contract before dispatch. This means CSI integration is auditable and testable.

**Verdict:** This is good. This is the right integration pattern for a subsystem that runs as a separate process.

### 1.4 WebSocket Usage Is Purpose-Separated

The project does not have one generic WebSocket bus. Instead:
- Gateway streaming handles live session transport to the dashboard
- AgentMail WebSocket handles inbound email
- Each has its own lifecycle, auth, and reconnect policy

**Verdict:** This is good. Purpose-separated transports are easier to reason about than a shared bus.

### 1.5 Webhook Ingress Has a Central Dispatcher

All external webhook traffic enters through `HooksService` and is subject to transform, auth, and bounded dispatch. In-process dispatch is explicitly separated from external webhook ingress.

**Verdict:** This is good. Do not bypass HooksService for new external integrations.

---

## Part 2: Architectural Observations and Improvement Opportunities

These are structural observations where the current integration is either incongruous, has a missed opportunity, or could be better aligned now that the full system is visible.

Each observation is rated:
- **Alignment** — the current approach works but could be more consistent with the rest of the system
- **Missed integration** — a capability exists but is not fully connected to adjacent subsystems
- **Structural concern** — a design pattern that may cause increasing friction as the system grows

---

### 2.1 Telegram Is the Only UI Channel That Bypasses the Gateway Session Model

**Type:** Structural concern

**Observation:** The Web UI creates sessions through the gateway and uses gateway-owned session metadata for ownership, streaming, and resume. Telegram does not. Telegram creates sessions through `AgentAdapter` using either `InProcessGateway` or `ExternalGateway`, but its session-id scheme (`tg_<user_id>`), checkpoint-reinjection model, and workspace path convention are all Telegram-specific and diverge from the gateway session model.

This means:
- Telegram sessions do not necessarily appear in the gateway session directory the same way web sessions do
- Telegram's `/continue` resume behavior is adapter-specific, not gateway-native
- the dashboard may not show Telegram work with the same fidelity as web sessions

**Why this matters now:** As more work is dispatched through Telegram (operator DMs, proactive messages, heartbeat actions), the session visibility gap grows. It also means the auth/ownership enforcement documented in the auth/session security canonical doc does not fully apply to Telegram sessions.

**Investigation path:**
- `src/universal_agent/bot/agent_adapter.py` — how sessions are created
- `src/universal_agent/gateway_server.py` — how gateway sessions are tracked
- `web-ui/lib/sessionDirectory.ts` — how the dashboard lists sessions

**Recommendation:** Consider whether Telegram's `ExternalGateway` path should become the only path (removing the `InProcessGateway` option), and whether Telegram sessions should be created through the same gateway session API that the web UI uses. This would unify session visibility and ownership enforcement without changing user-facing behavior.

---

### 2.2 CSI Analytics Events Route Through Hooks But Not Through the Same Session Model as Other Agent Work

**Type:** Alignment

> **2026-04-19 update:** This observation is historical. CSI analytics events no longer dispatch through `to_csi_analytics_action()` or hardcoded agent-session lanes. The current direction is passive digest capture plus selected proactive/convergence producers that create Task Hub work only when warranted. Do not restore the old per-event CSI hook dispatch path without a new explicit architecture decision.

**Observation:** CSI analytics events arrive via `/api/v1/signals/ingest`, get validated, and then dispatch into the hooks pipeline. This is correct for the delivery path. But the resulting agent actions use hardcoded session-key lanes (`csi_trend_analyst`, `csi_data_analyst`) that are separate from the normal session lifecycle.

This is functional, but it means:
- CSI-driven agent work does not follow the same session creation/ownership/visibility flow as web or Telegram sessions
- CSI agent sessions may not appear consistently in session-management surfaces
- if CSI analytics volume grows, these special lanes become harder to manage because they lack the same lifecycle controls (timeout, cancel, owner enforcement) as normal sessions

**Investigation path:**
- `src/universal_agent/signals_ingest.py` — signed ingest validation and YouTube playlist handoff
- `src/universal_agent/gateway_server.py` — how CSI actions enter the execution pipeline
- `src/universal_agent/hooks_service.py` — internal dispatch

**Recommendation:** This is acceptable for now, but if CSI analytics work becomes more complex or if operators need to manage/cancel CSI-driven sessions, consider routing CSI analytics through the same gateway session creation path with a `source=csi` marker rather than using hardcoded session keys.

---

### 2.3 Auth Model Has Three Separate Trust Surfaces That Do Not Share a Common Abstraction

**Type:** Alignment

**Observation:** The system currently has three separate trust/auth models:
1. **Dashboard auth:** cookie + HMAC session token with owner resolution
2. **Gateway/ops auth:** `UA_OPS_TOKEN` / `UA_INTERNAL_API_TOKEN` bearer header
3. **CSI ingest auth:** separate shared secret + HMAC signature + timestamp

Each works correctly for its purpose. But there is no shared auth abstraction, no common middleware, and no unified audit surface. The dashboard proxy in Next.js injects ops tokens on behalf of authenticated dashboard users, which means the gateway sees an ops-token-authenticated request, not the original dashboard owner identity.

This is workable today but has two structural implications:
- adding a new trusted caller means implementing auth from scratch for that caller
- auditing "who did what" across all three surfaces requires correlating three different auth log formats

**Investigation path:**
- `web-ui/lib/dashboardAuth.ts` — cookie auth
- `web-ui/app/api/dashboard/gateway/[...path]/route.ts` — proxy token injection
- `src/universal_agent/api/server.py` — dashboard auth + internal token bypass
- `src/universal_agent/signals_ingest.py` — CSI auth
- `src/universal_agent/gateway_server.py` — ops token paths

**Recommendation:** No immediate rewrite needed. But if a fourth trust surface is ever added, strongly consider extracting a shared auth-result type and audit-log format that all surfaces can emit. This would make cross-surface auditing possible without retroactively unifying the auth mechanisms.

---

### 2.4 Webhook and WebSocket Paths for Email Have Diverged Without a Convergence Plan

**Type:** Missed integration

**Observation:** AgentMail has both:
- a webhook transform (`webhook_transforms/agentmail_transform.py`)
- a WebSocket listener (`src/universal_agent/services/agentmail_service.py`)

Current production uses the WebSocket path. The webhook transform exists but does not have reply-extraction parity. Neither path is explicitly deprecated or promoted as the canonical future direction.

This is not actively broken, but it means:
- if the WebSocket connection drops and the system falls back to webhooks (or an operator switches), reply extraction behavior silently changes
- two code paths exist for the same purpose with different feature sets

**Investigation path:**
- `webhook_transforms/agentmail_transform.py`
- `src/universal_agent/services/agentmail_service.py`

**Recommendation:** Make an explicit decision: either bring the webhook path to parity (so it can serve as a reliable fallback) or formally deprecate it and remove it. Do not leave two paths with different behavior for the same integration.

---

### 2.5 Factory Heartbeat and CSI Delivery Health Are Parallel Liveness Models That Don't Talk to Each Other

**Type:** Missed integration

**Observation:** The factory delegation system has a heartbeat model: factories POST to HQ every 60s, HQ tracks staleness, and the registry shows online/stale/offline status. CSI has its own delivery-health system: canaries, SLO gates, and auto-remediation timers that track whether CSI -> UA delivery is healthy.

These are parallel liveness/health models that could inform each other but currently do not:
- if the gateway goes down, both factory heartbeats AND CSI delivery will fail, but neither system tells the other
- if CSI delivery health degrades, the gateway's factory fleet view does not reflect that
- if a factory goes offline, CSI doesn't know

**Investigation path:**
- `src/universal_agent/delegation/heartbeat.py` — factory heartbeat
- `src/universal_agent/delegation/factory_registry.py` — HQ registry
- `CSI_Ingester/development/scripts/csi_delivery_health_*.py` — CSI health probes
- `CSI_Ingester/development/deployment/systemd/csi-delivery-health-*.service` — CSI health timers

**Recommendation:** Consider a lightweight cross-health surface: the factory fleet ops endpoint could include a `csi_delivery_healthy` flag, and CSI health probes could emit a summary event that the gateway consumes. This would give the ops/dashboard view a single place to see "is everything alive?"

---

### 2.6 Run Workspace Ownership Is Enforced by Convention, Not by the Filesystem

**Type:** Structural concern

**Observation:** Run workspaces are created under `AGENT_RUN_WORKSPACES/` with run-based naming plus some legacy session-shaped names during migration. The auth/session security model enforces ownership at the API level by comparing the authenticated owner with the session or run metadata. But the filesystem itself has no per-workspace access control — any process with access to `AGENT_RUN_WORKSPACES` can read another workspace's files.

Today this is fine because all processes run under the same user. But if factories, VP workers, or CSI processes ever need workspace access, the convention-based model becomes a shared-filesystem trust surface.

**Investigation path:**
- `src/universal_agent/api/server.py` — session ownership enforcement is API-level
- `src/universal_agent/gateway_server.py` — session creation
- `scripts/sync_remote_workspaces.sh` — sync has access to all workspaces

**Recommendation:** No immediate change needed. But if multi-user or multi-tenant use is ever considered, the ownership boundary must move from API-level enforcement to filesystem-level isolation (per-run-workspace directories with restricted permissions, or a workspace-access proxy).

---

### 2.7 The Timer Fleet Model Has No Central Registry or Dashboard Visibility

**Type:** Alignment

**Observation:** CSI alone now has 30+ systemd timers. Combined with UA's own timers (workspace sync, service watchdog, OOM alert), the total timer count on VPS is large. There is no central ops surface that shows:
- which timers are enabled/disabled
- when each last ran
- whether each last succeeded or failed

Operators currently use `systemctl list-timers` on VPS or read journal logs.

**Investigation path:**
- `CSI_Ingester/development/scripts/csi_install_systemd_extras.sh` — the full CSI timer list
- `deployment/systemd/` — UA-level timers
- `scripts/vps_service_watchdog.sh` — the service-level watchdog

**Recommendation:** Consider a lightweight `/api/v1/ops/timers` endpoint (or a dashboard panel) that reads `systemctl list-timers --output=json` and presents it. This would give operators a single view of the entire timer fleet without SSHing into the VPS.

---

### 2.8 Telegram and CSI Both Use Telegram Bot API But Through Separate Mechanisms

**Type:** Alignment

**Observation:** The interactive Telegram bot (`src/universal_agent/bot/`) and the CSI digest scripts both send messages to Telegram, but they use completely separate mechanisms:
- The bot uses `python-telegram-bot` with polling and PTB's send helpers
- CSI scripts directly call the Telegram Bot API via `httpx` or `requests`

They share the same `TELEGRAM_BOT_TOKEN` (or CSI-specific overrides). But there is no shared Telegram send utility, no common retry policy, and no shared rate-limit awareness.

**Investigation path:**
- `src/universal_agent/bot/main.py` — `_send_with_retry()`
- `CSI_Ingester/development/scripts/csi_rss_telegram_digest.py` — direct send logic
- `src/universal_agent/mcp_server_telegram.py` — MCP-based send (third mechanism)

**Recommendation:** Extract a shared `telegram_send(chat_id, text, *, bot_token, retry, thread_id)` utility. Both the bot and CSI scripts could use it. This would unify retry policy, rate limiting, and error handling without changing behavior.

---

### 2.9 Runtime Role Policy Shapes the Gateway but Does Not Shape Other Consumers Equally

**Type:** Alignment

**Observation:** `FactoryRuntimePolicy` determines gateway mode, UI availability, telegram polling, heartbeat scope, and delegation mode. The gateway enforces this policy aggressively (blocking routes for LOCAL_WORKER, disabling WebSocket for non-HQ). But other consumers (Telegram bot, CSI, agent core) do not read the factory policy at all.

This means:
- a LOCAL_WORKER node with Telegram bot running would still try to operate as an interactive bot
- CSI does not check whether it is running on a node whose policy should prevent it from emitting

**Investigation path:**
- `src/universal_agent/runtime_role.py` — the policy builder
- `src/universal_agent/bot/main.py` — does not read factory policy
- `CSI_Ingester/development/csi_ingester/service.py` — does not read factory policy

**Recommendation:** If Telegram or CSI are ever deployed on non-HQ nodes, they should respect the same runtime role policy. For now, document that Telegram and CSI are HQ-only services. If that changes, extend `FactoryRuntimePolicy` to include `enable_telegram` and `enable_csi_ingest` fields.

---

### 2.10 Infisical Is Used for UA Secrets but CSI Manages Its Own Auth Independently

**Type:** Alignment

**Observation:** UA has a full Infisical-first secret bootstrap with strict/fallback modes. CSI does not use Infisical for its own startup — it reads secrets from its systemd env file. However, CSI's Threads token refresh automation does use Infisical for syncing tokens.

This creates a split:
- UA secrets are Infisical-managed and fail-closed on VPS
- CSI secrets are env-file-managed and do not fail-closed
- Threads tokens bridge both worlds

**Investigation path:**
- `src/universal_agent/infisical_loader.py` — UA bootstrap
- `CSI_Ingester/development/csi_ingester/config.py` — CSI config (no Infisical)
- `CSI_Ingester/development/scripts/csi_threads_auth_bootstrap.py` — Threads Infisical usage

**Recommendation:** If CSI grows further, consider adding an optional Infisical bootstrap path to CSI's startup (similar to the UA pattern). This would allow CSI to also fail-closed on VPS when Infisical is available. Not urgent, but it would reduce the operational gap between the two systems.

---

## Part 3: Integration Patterns That Should NOT Change

These are patterns that the review confirmed are correct and should be preserved even under refactoring pressure.

1. **Keep CSI as a separate process.** CSI's signed HTTP delivery into UA is a cleaner integration boundary than embedding CSI into the gateway process.

2. **Keep the webhook transform model.** External payloads should always pass through a transform layer before entering the agent runtime.

3. **Keep the factory heartbeat separate from the service watchdog.** The factory heartbeat is an application-level liveness signal; the service watchdog is a process-level recovery tool. They serve different purposes.

4. **Keep `local` and `mirror` as distinct storage roots.** The API's explicit `root_source` parameter correctly separates canonical local state from mirrored VPS copies.

5. **Keep the gateway as the session authority.** The gateway owns session metadata, and the API server and dashboard defer to it. This should remain the canonical ownership model even if Telegram or other channels are unified.

---

## Canonical Docs This Review Is Derived From

1. `02_Flows/07_WebSocket_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`
2. `02_Flows/08_Gateway_And_Web_UI_Auth_And_Session_Security_Source_Of_Truth_2026-03-06.md`
3. `03_Operations/82_Email_Architecture_And_AgentMail_Source_Of_Truth_2026-03-06.md`
4. `03_Operations/83_Webhook_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`
5. `03_Operations/85_Infisical_Secrets_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`
6. `03_Operations/86_Residential_Proxy_Architecture_And_Usage_Policy_Source_Of_Truth_2026-03-06.md`
7. `03_Operations/87_Tailscale_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`
8. `03_Operations/88_Factory_Delegation_Heartbeat_And_Registry_Source_Of_Truth_2026-03-06.md`
9. `03_Operations/89_Runtime_Bootstrap_Deployment_Profiles_And_Factory_Role_Source_Of_Truth_2026-03-06.md`
10. `03_Operations/90_Artifacts_Workspaces_And_Remote_Sync_Source_Of_Truth_2026-03-06.md`
11. `03_Operations/91_Telegram_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`
12. `03_Operations/92_CSI_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`

---

## Bottom Line

The system architecture is fundamentally sound. The core patterns — centralized bootstrap, purpose-separated transports, signed cross-process delivery, gateway-as-session-authority — are correct and should be preserved.

The main structural improvements are about **consistency and convergence**:
- unify Telegram's session model with the gateway session model
- make an explicit decision on the AgentMail webhook vs WebSocket path
- add cross-health visibility between factory heartbeats and CSI delivery health
- extract shared utilities where parallel mechanisms exist (Telegram send, auth audit)
- extend runtime role policy to cover Telegram and CSI if they ever deploy on non-HQ nodes

None of these require rewrites. They are alignment improvements that reduce the gap between how each subsystem works independently and how they should work together.
