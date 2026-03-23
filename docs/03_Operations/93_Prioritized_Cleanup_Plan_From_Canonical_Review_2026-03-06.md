# 93. Prioritized Cleanup Plan From Canonical Review (2026-03-06)

## Purpose

This document is a prioritized, self-contained cleanup plan derived from a comprehensive review of all major Universal Agent subsystems. It was produced after creating 12 canonical source-of-truth documents covering: auth/session security, factory delegation, runtime bootstrap, artifacts/workspaces/sync, Telegram, CSI, Tailscale, webhooks, WebSockets, Infisical secrets, residential proxy, and email/AgentMail.

**This document is designed for handoff.** A coder picking this up does not need the history of the review that produced it. Each item includes the problem, the files to investigate, and the expected fix scope so the next person can proceed independently.

## How To Use This Document

- Items are ordered **highest value first**.
- Each item has a **tier**: P0 (should fix soon), P1 (important but not urgent), P2 (housekeeping/polish).
- Each item references the **canonical doc** that describes the current truth for that subsystem, so the coder can read that doc for full context before starting.
- Items are deliberately scoped small enough to be addressable in individual work packets.

---

## P0: High-Value Fixes

### 1. Remove Dev-Secret Fallback for Dashboard Session Signing

**Problem:** The dashboard session-signing secret falls back to a hardcoded `ua-dashboard-dev-secret` when no explicit secret is configured. This is convenient for local dev but is a real security gap if it is ever reached in a hardened deployment.

**Canonical doc:** `02_Flows/08_Gateway_And_Web_UI_Auth_And_Session_Security_Source_Of_Truth_2026-03-06.md`

**Files to investigate:**
- `web-ui/lib/dashboardAuth.ts` — look for `ua-dashboard-dev-secret` in the session-secret resolution chain
- `src/universal_agent/api/server.py` — mirror logic on the API server side

**Expected fix:** Either remove the hardcoded fallback entirely (forcing explicit configuration) or gate it behind a `NODE_ENV=development` check so it cannot activate on production profiles. Log a clear warning if the fallback is ever reached.

**Scope:** Small, single-file change on each side (TS + Python).

---

### 2. Unify Telegram Env Variable Naming

**Problem:** The Telegram bot code reads `TELEGRAM_ALLOWED_USER_IDS`, but `.env.sample` documents `ALLOWED_USER_IDS` (without the `TELEGRAM_` prefix). This creates silent misconfiguration risk where the allowlist appears set but the bot doesn't enforce it.

**Canonical doc:** `03_Operations/91_Telegram_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`

**Files to investigate:**
- `src/universal_agent/bot/config.py` — `get_allowed_user_ids()` reads `TELEGRAM_ALLOWED_USER_IDS`
- `.env.sample` — documents `ALLOWED_USER_IDS`
- `.env.example` — may also have drift
- `src/universal_agent/gateway_server.py` — reads `TELEGRAM_ALLOWED_USER_IDS` for identity candidates

**Expected fix:** Standardize on `TELEGRAM_ALLOWED_USER_IDS` everywhere. Update `.env.sample` and `.env.example`. Optionally add a fallback read of `ALLOWED_USER_IDS` with a deprecation warning.

**Scope:** Small, env-file + config.py alignment.

---

### 3. Gate Internal Token Bypass Documentation and Audit Trail

**Problem:** The `UA_INTERNAL_API_TOKEN` / `UA_OPS_TOKEN` bypass is effectively admin-grade access that skips normal dashboard cookie auth. This is practical, but it is used in multiple places without a single explicit audit or documentation marker explaining its blast radius.

**Canonical doc:** `02_Flows/08_Gateway_And_Web_UI_Auth_And_Session_Security_Source_Of_Truth_2026-03-06.md`

**Files to investigate:**
- `src/universal_agent/api/server.py` — `_authenticate_dashboard_request`, internal token extraction
- `src/universal_agent/gateway_server.py` — ops token paths
- `web-ui/app/api/dashboard/gateway/[...path]/route.ts` — proxy token injection

**Expected fix:** Add an explicit inline comment at each trusted-token bypass site that says `# SECURITY: this is an admin-equivalent bypass`. Consider adding a Logfire/log emit when internal-token auth is used so operators can audit how often and from where it is exercised.

**Scope:** Medium — annotation + optional logging.

---

### 4. CSI Subsystem Boundary Cleanup in Older Docs

**Problem:** The tutorial playlist polling responsibility has moved from CSI into native UA (`services/youtube_playlist_watcher.py`), but older docs and CSI-era notes may still imply CSI owns this end-to-end. This creates confusion about which system is authoritative for tutorial dispatch.

**Canonical doc:** `03_Operations/92_CSI_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`

**Files to investigate:**
- `heartbeat/07_Telegram_UI_Investigation.md` — may reference CSI playlist path
- `heartbeat/08_Telegram_Revival_and_Enhancement_Plan.md` — may reference CSI playlist assumptions
- `CSI_Ingester/development/README.md` — youtube_playlist section
- `CSI_Ingester/documentation/` — older runbooks

**Expected fix:** Add a clear note at the top of each stale section stating that native UA playlist watching is now authoritative. Do not delete the CSI playlist adapter code or config, just ensure docs accurately reflect the current split.

**Scope:** Medium — doc updates across several files.

---

### 5. Telegram Service Health Probe Gap

**Problem:** The VPS watchdog only checks Telegram's systemd active-state by default. There is no HTTP health probe. A logically stuck but still-active Telegram process may not be detected.

**Canonical doc:** `03_Operations/91_Telegram_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`

**Files to investigate:**
- `scripts/vps_service_watchdog.sh` — `DEFAULT_SERVICE_SPECS` line for `universal-agent-telegram`
- `src/universal_agent/bot/main.py` — no health endpoint currently exists

**Expected fix:** Either add a minimal HTTP health endpoint to the Telegram bot process (simplest: a tiny side-server or a health flag file like the gateway heartbeat), or add a heartbeat-file check similar to the gateway's `process_heartbeat.py` pattern.

**Scope:** Medium — new health probe + watchdog config update.

---

## P1: Important But Not Urgent

### 6. Converge Tailscale Host Defaults Across Scripts

**Problem:** Some helper scripts still reference the older IP `100.106.113.93` instead of the MagicDNS host `uaonvps`. This isn't broken (Tailscale routes both), but it creates drift.

**Canonical doc:** `03_Operations/87_Tailscale_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`

**Files to investigate:**
- `scripts/deploy_vps.sh`
- `scripts/vpsctl.sh`
- `scripts/sync_remote_workspaces.sh`
- `scripts/pull_remote_workspaces_now.sh`
- Older runbooks under `docs/03_Operations/`

**Expected fix:** Standardize all default host references to the MagicDNS name. Keep `UA_VPS_HOST` / `UA_REMOTE_SSH_HOST` as the override mechanism.

**Scope:** Small — string replacements across scripts and docs.

---

### 7. Decide Tailscale SSH Auth Mode Long-Term Default

**Problem:** The current default SSH auth mode is `keys`, but `tailscale_ssh` is also supported and deployed. The long-term default has not been formally decided, so scripts and docs vary.

**Canonical doc:** `03_Operations/87_Tailscale_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`

**Files to investigate:**
- `scripts/vpsctl.sh` — `UA_SSH_AUTH_MODE` default
- `scripts/deploy_vps.sh` — auth mode resolution
- `scripts/sync_remote_workspaces.sh` — auth mode usage

**Expected fix:** Make a decision (either `keys` or `tailscale_ssh` as default), then align all script defaults and document the decision.

**Scope:** Small — decision + script defaults.

---

### 8. Legacy WebSocket Surface Classification

**Problem:** `src/web/server.py` contains a legacy standalone WebSocket server. The primary transport is now the gateway WebSocket in `gateway_server.py`. The legacy surface should be explicitly classified as deprecated or removed.

**Canonical doc:** `02_Flows/07_WebSocket_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`

**Files to investigate:**
- `src/web/server.py` — the legacy WebSocket chat handler
- any references to `/ws/chat` in docs or frontend code

**Expected fix:** Add a deprecation header comment to `src/web/server.py`. If it is confirmed unused in production, consider removing it. Update any docs that reference it.

**Scope:** Small to medium depending on whether removal is warranted.

---

### 9. Bootstrap Feature-Toggle Unification

**Problem:** Runtime bootstrap centralizes profile, secrets, and role policy in one contract. But some downstream capability toggles still read env flags directly rather than deriving from the bootstrap result. This means the bootstrap is not the single source of all runtime behavior.

**Canonical doc:** `03_Operations/89_Runtime_Bootstrap_Deployment_Profiles_And_Factory_Role_Source_Of_Truth_2026-03-06.md`

**Files to investigate:**
- `src/universal_agent/agent_setup.py` — post-bootstrap env reads
- `src/universal_agent/gateway_server.py` — post-bootstrap env reads
- `src/universal_agent/runtime_role.py` — the policy builder

**Expected fix:** Audit which env flags are read after bootstrap. For flags that should be bootstrap-derived, either fold them into `FactoryRuntimePolicy` or document why they remain independent.

**Scope:** Medium — audit + incremental policy expansion.

---

### 10. AgentMail Webhook Parity

**Problem:** AgentMail webhook transform exists (`webhook_transforms/agentmail_transform.py`), but current production email ingress uses the WebSocket listener path. If webhooks are ever reactivated for AgentMail, they need reply-extraction parity with the WebSocket path.

**Canonical docs:** `03_Operations/82_Email_Architecture_And_AgentMail_Source_Of_Truth_2026-03-06.md` and `03_Operations/83_Webhook_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`

**Files to investigate:**
- `webhook_transforms/agentmail_transform.py`
- `src/universal_agent/services/agentmail_service.py` — the WebSocket path with reply extraction

**Expected fix:** Either bring the webhook transform to parity or add a clear deprecation note. Don't leave a silent divergence.

**Scope:** Small if deprecation note, medium if parity implementation.

---

### 11. Storage Root Default Alignment Across Tools

**Problem:** API server, sync scripts, and older runbooks all reference the same workspace/artifacts roots, but some defaults still reflect older remote host or path conventions. This is cosmetic but creates friction for new operators.

**Canonical doc:** `03_Operations/90_Artifacts_Workspaces_And_Remote_Sync_Source_Of_Truth_2026-03-06.md`

**Files to investigate:**
- `src/universal_agent/api/server.py` — `UA_REMOTE_WORKSPACES_DIR`, `UA_REMOTE_ARTIFACTS_DIR` defaults
- `scripts/sync_remote_workspaces.sh` — default paths
- Older runbooks referencing `/opt/universal_agent/` paths

**Expected fix:** Verify all default paths agree and update any lagging references.

**Scope:** Small.

---

## P2: Housekeeping and Polish

### 12. Remove or Archive Webhook-Era Telegram Helpers

**Problem:** `start_telegram_bot.sh` and `scripts/register_webhook.py` reflect an older ngrok/webhook/Docker model. Current runtime is polling. These files are confusing to new readers.

**Canonical doc:** `03_Operations/91_Telegram_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`

**Expected fix:** Either delete or move to an `archive/` directory with a README note.

**Scope:** Small.

---

### 13. Infisical Ops Visibility Enhancement

**Problem:** No rich operator-facing endpoint shows which secret source was used at runtime (Infisical, dotenv, environment-only). Diagnosis currently requires log reading.

**Canonical doc:** `03_Operations/85_Infisical_Secrets_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`

**Expected fix:** Add a field to the health/deployment-profile ops endpoint showing `secret_bootstrap_source`, `strict_mode`, `loaded_count`.

**Scope:** Small — extend an existing ops response.

---

### 14. Residential Proxy Policy Centralization

**Problem:** Proxy usage rules previously lived across code, tests, and discussion rather than one canonical place. The canonical doc now exists, but inline code references should point to it.

**Canonical doc:** `03_Operations/86_Residential_Proxy_Architecture_And_Usage_Policy_Source_Of_Truth_2026-03-06.md`

**Expected fix:** Add a comment in the proxy client code pointing to the canonical doc.

**Scope:** Tiny.

---

### 15. Factory Capability Reporting Enrichment

**Problem:** Heartbeat capability reporting is partly env-tag-derived rather than runtime-probed. This means a factory might report a capability it cannot actually exercise.

**Canonical doc:** `03_Operations/88_Factory_Delegation_Heartbeat_And_Registry_Source_Of_Truth_2026-03-06.md`

**Expected fix:** Optionally add a lightweight runtime probe to verify key capabilities before including them in the heartbeat payload.

**Scope:** Medium — one-time enrichment.

---

### 16. WebSocket Observability Expansion

**Problem:** Gateway WebSocket failures, reconnect storms, and stale sessions lack rich operator-facing metrics.

**Canonical doc:** `02_Flows/07_WebSocket_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`

**Expected fix:** Add counters/logging for WebSocket connection failures, reconnect events, and stale-session close reasons.

**Scope:** Medium.

---

### 17. Cross-Doc Consistency Pass

**Problem:** Several older operational docs have not been updated to reflect the current canonical source-of-truth documents. Stale sections in older docs can mislead operators.

**Canonical docs:** All 12 canonical source-of-truth documents.

**Expected fix:** Audit older docs under `docs/03_Operations/` and add forward references to the relevant canonical doc where appropriate.

**Scope:** Medium — batch doc pass.

---

## Summary

| Tier | Count | Focus |
|------|-------|-------|
| P0 | 5 | Security, naming, health, boundary clarity |
| P1 | 6 | Defaults, parity, unification, classification |
| P2 | 6 | Polish, observability, archival, cross-references |
| **Total** | **17** | |

## Canonical Docs This Plan Is Derived From

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
