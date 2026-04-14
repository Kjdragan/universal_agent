# OpenClaw Sync Report — 2026-04-14

**Releases Analyzed:** v2026.4.12, v2026.4.14-beta.1
**Analyst:** VP Analysis Agent (CODIE)
**Project:** Universal Agent

---

## Executive Summary

Two OpenClaw releases landed this week with **55+ changes** spanning security hardening, gateway lifecycle, cron/scheduler reliability, memory system evolution, and Telegram forum topic support. The most significant themes:

1. **Security hardening wave** — 11 security fixes addressing SSRF bypass, hook:wake privilege escalation, config redaction, shell injection vectors, and approval system gaps.
2. **Cron/scheduler resilience** — Multiple fixes for refire loops, error-backoff erosion, and next-run calculation failures.
3. **Gateway session isolation** — Fix for synthetic heartbeat/cron turns poisoning shared-session routing metadata.
4. **Active Memory plugin** — New memory sub-agent that proactively recalls context before main reply turns.
5. **Telegram forum topic support** — Learning human topic names from Telegram forum service messages.

**Action items for Universal Agent:**
- **3 ADOPT** (security hardening, cron refire-loop prevention, gateway session metadata isolation)
- **5 INVESTIGATE** (active memory pattern, delivery queue persistence, markdown sanitization, exec-policy CLI, commands.list RPC)
- **3 WATCH** (context engine ID validation, dreaming replay guard, plugin loading isolation)

---

## Detailed Feature Analysis

### 1. Security: Force Owner Downgrade for Untrusted `hook:wake` Events

**OpenClaw Component:** Security / Heartbeat
**OpenClaw References:** `#66031` — @pgondhi987
**Relevance:** HIGH
**Recommendation:** ADOPT
**Our Counterpart:** `src/universal_agent/hooks_service.py`, `src/universal_agent/heartbeat_service.py`
**Gap Analysis:** Our heartbeat service dispatches hook events but does not enforce owner-role downgrade for system-triggered wake events. A malicious or misconfigured external trigger could gain elevated agent context.
**Implementation Notes:** In `hooks_service.py`, when processing `hook:wake` or equivalent system events, force the session identity to the lowest-trust role before dispatching to the agent turn. Add a `source_trust_level` field to `GatewayRequest` and check it in `gateway_server.py` before elevating session capabilities. Key: any event arriving via webhook/external trigger (not direct user CLI) should start as untrusted and require explicit elevation.
**Effort:** S
**Priority:** 2

---

### 2. Security: Enforce SSRF Policy on Snapshot, Screenshot, and Tab Routes

**OpenClaw Component:** Security / Browser
**OpenClaw References:** `#66040` — @pgondhi987
**Relevance:** HIGH
**Recommendation:** ADOPT
**Our Counterpart:** `src/universal_agent/gateway_server.py` (browser snapshot/screenshot MCP routes), `src/universal_agent/guardrails/`
**Gap Analysis:** Our browser automation via agent-browser MCP does not enforce SSRF policy on the URLs passed to snapshot/screenshot/tab routes. An agent could request snapshots of internal services (localhost, cloud metadata endpoints, etc.).
**Implementation Notes:** Add SSRF validation middleware in `gateway_server.py` for any browser tool that accepts a URL parameter. Block requests to private IP ranges (10.x, 172.16-31.x, 192.168.x, 127.x, 169.254.169.254, ::1) unless explicitly allowed. This is a classic SSRF pattern — add URL resolution + IP range check before passing to Playwright/CDP.
**Effort:** S
**Priority:** 2

---

### 3. Security: Config Redaction for `sourceConfig` and `runtimeConfig` Alias Fields

**OpenClaw Component:** Security / Config
**OpenClaw References:** `#66030` — @pgondhi987
**Relevance:** MEDIUM
**Recommendation:** ADOPT
**Our Counterpart:** `src/universal_agent/gateway_server.py` (config snapshot endpoints), `src/universal_agent/identity/`
**Gap Analysis:** Our gateway likely exposes configuration in debug/diagnostic endpoints. If alias fields in config contain secrets or credentials, they may leak through config snapshot APIs.
**Implementation Notes:** Audit all config snapshot/health-check endpoints in `gateway_server.py` for fields that could contain secrets. Add a `redact_config()` function that strips or masks known secret patterns (API keys, tokens, passwords) and any fields listed in a `REDACTED_CONFIG_KEYS` set. Apply this to all `/api/config/*` and debug endpoints.
**Effort:** S
**Priority:** 3

---

### 4. Security: Prevent Empty Approver List from Granting Authorization

**OpenClaw Component:** Security / Approval
**OpenClaw References:** `#65714` — @pgondhi987
**Relevance:** MEDIUM
**Recommendation:** ADOPT
**Our Counterpart:** `src/universal_agent/guardrails/tool_schema.py`, permission system in gateway
**Gap Analysis:** If our approval/permission system has a default-deny posture, an empty approver list might accidentally grant access (deny-by-default vs deny-by-empty-list ambiguity). Need to verify.
**Implementation Notes:** Check our permission evaluation logic in `guardrails/tool_schema.py` and gateway permission middleware. Ensure that an empty or null approver/permission list evaluates as DENY, not ALLOW. Add explicit test: `permissions=[] → action blocked`.
**Effort:** XS
**Priority:** 3

---

### 5. Security: Broaden Shell-Wrapper Detection and Block Env-Argv Injection

**OpenClaw Component:** Security / Shell
**OpenClaw References:** `#65717` — @pgondhi987
**Relevance:** MEDIUM
**Recommendation:** ADOPT
**Our Counterpart:** `src/universal_agent/guardrails/`, `src/universal_agent/execution_engine.py`
**Gap Analysis:** Our execution engine runs shell commands. If an agent crafts a command like `env VAR=value /bin/bash -c "..."`, it could bypass shell wrapper restrictions.
**Implementation Notes:** In execution engine's shell command sanitization, add detection for env-argv assignment patterns (`env VAR=... command`) and block them. Ensure our command allowlist/rejection logic catches `env` as a command prefix and validates the full argv chain, not just the first element.
**Effort:** S
**Priority:** 3

---

### 6. Security: Remove Busybox/Toybox from Interpreter-Like Safe Bins

**OpenClaw Component:** Security / Sandbox
**OpenClaw References:** `#65713` — @pgondhi987
**Relevance:** LOW
**Recommendation:** SKIP
**Our Counterpart:** N/A
**Gap Analysis:** This is a sandbox-level binary allowlist change specific to OpenClaw's container security. We don't have an equivalent binary allowlist mechanism — our execution runs in the host environment under the Python process.
**Implementation Notes:** N/A
**Effort:** N/A
**Priority:** N/A

---

### 7. Gateway: Session Routing Metadata Isolation (Heartbeat/Cron Poisoning)

**OpenClaw Component:** Gateway / Sessions
**OpenClaw References:** `#66073, #63733, #35300` — @mbelinky
**Relevance:** HIGH
**Recommendation:** ADOPT
**Our Counterpart:** `src/universal_agent/gateway.py`, `src/universal_agent/heartbeat_service.py`, `src/universal_agent/cron_service.py`
**Gap Analysis:** **This is a recurring innovation gap.** Our heartbeat service creates sessions via `InProcessGateway` and sets routing metadata. If a heartbeat turn modifies shared session state (routing target, origin metadata), subsequent cron-triggered or user-triggered turns in the same session could be poisoned — e.g., a heartbeat that sets `target=heartbeat` could cause a later cron job to deliver its output to the heartbeat channel instead of the intended recipient.
**Implementation Notes:** In `gateway.py`, ensure that synthetic turns (heartbeat, cron) use isolated `GatewaySession` objects or reset routing metadata after each turn. Add a `session_context_isolation` flag: when a turn is triggered by heartbeat/cron, it must NOT mutate the parent session's routing state. Clean routing metadata at turn completion. Review how `GatewaySession` state persists across turns.
**Effort:** M
**Priority:** 2

---

### 8. Gateway: Entrypoint Resolution Unification

**OpenClaw Component:** Gateway / Build
**OpenClaw References:** `#65984` — @mbelinky
**Relevance:** LOW
**Recommendation:** SKIP
**Our Counterpart:** N/A
**Gap Analysis:** This is about unifying JS entrypoint paths (`dist/entry.js` vs `dist/index.js`) in OpenClaw's Node.js gateway. Our Python gateway doesn't have this build-artifact path drift issue.
**Implementation Notes:** N/A
**Effort:** N/A
**Priority:** N/A

---

### 9. Gateway: Add `commands.list` RPC for Command Discovery

**OpenClaw Component:** Gateway / API
**OpenClaw References:** `#62656` — @samzong
**Relevance:** MEDIUM
**Recommendation:** INVESTIGATE
**Our Counterpart:** `src/universal_agent/gateway_server.py`, skill system in `.claude/skills/`
**Gap Analysis:** We have a rich skill system but no unified RPC endpoint to discover available commands/skills at runtime. External clients (VP agents, web UI) currently have to know what's available through hardcoded configuration.
**Implementation Notes:** Add a `/api/commands` endpoint to `gateway_server.py` that returns available skills, slash commands, and registered tool capabilities with metadata (description, required parameters, category). This would enable dynamic UI rendering of available actions and better VP agent self-service. Study OpenClaw's `commands.list` RPC schema for the response format.
**Effort:** M
**Priority:** 4

---

### 10. Gateway: Auth Hardening (Blank Example Credentials, Startup Fail)

**OpenClaw Component:** Gateway / Auth
**OpenClaw References:** `#64586` — @navarrotech and @vincentkoc
**Relevance:** MEDIUM
**Recommendation:** ADOPT
**Our Counterpart:** `.env` files, `src/universal_agent/auth/`, deployment configuration
**Gap Analysis:** We should verify our `.env.example` or example config files don't contain placeholder credentials that could be accidentally deployed. Our Infisical-based secrets system mitigates this, but example configs could still have risk.
**Implementation Notes:** Audit all `.env.example`, `config.example.*`, and documentation config snippets. Ensure placeholder values are clearly marked (e.g., `YOUR_TOKEN_HERE`) and add startup validation that refuses to start if a placeholder pattern is detected in a live config. Our `infisical_loader.py` already handles this for Infisical-managed secrets, but bootstrapping configs may not.
**Effort:** XS
**Priority:** 4

---

### 11. Gateway: WebSocket Keepalive Tick Broadcast Fix

**OpenClaw Component:** Gateway / WebSocket
**OpenClaw References:** `#65256` — @100yenadmin and @vincentkoc
**Relevance:** LOW
**Recommendation:** SKIP
**Our Counterpart:** `src/universal_agent/gateway_server.py` (WebSocket handlers)
**Gap Analysis:** Our WebSocket implementation may already handle this correctly or may not have the same backpressure issue. Low priority unless we see tick-timeout disconnects during long runs.
**Implementation Notes:** Monitor for WebSocket disconnects during long-running tasks. If observed, ensure keepalive frames are not marked as droppable in our WebSocket handler.
**Effort:** XS (if needed)
**Priority:** 5

---

### 12. Cron/Scheduler: Stop Refire Loops on Invalid Next-Run

**OpenClaw Component:** Cron & Scheduling
**OpenClaw References:** `#66019, #66083` — @mbelinky
**Relevance:** HIGH
**Recommendation:** ADOPT
**Our Counterpart:** `src/universal_agent/cron_service.py`
**Gap Analysis:** Our cron service uses `croniter` for next-run calculation. If a cron expression becomes invalid or a next-run calculation returns no valid future slot, our scheduler could enter a tight retry loop, rapidly attempting to fire a job that can never be scheduled. We need to guard against this.
**Implementation Notes:** In `cron_service.py`, add a guard: if `croniter.get_next()` raises or returns a time in the past, do NOT immediately retry. Instead: (1) log the error, (2) set the job to `errored` state with backoff, (3) schedule a maintenance check at a future time. Add a maximum retry rate limiter: if a job has failed to compute next-run more than N times in a row, disable it and alert. Review the `_compute_next_run` equivalent in our cron service.
**Effort:** S
**Priority:** 2

---

### 13. Cron/Scheduler: Preserve Error-Backoff Floor During Maintenance Repair

**OpenClaw Component:** Cron & Scheduling
**OpenClaw References:** `#66019, #66083, #66113` — @mbelinky
**Relevance:** HIGH
**Recommendation:** ADOPT
**Our Counterpart:** `src/universal_agent/cron_service.py`
**Gap Analysis:** Related to #12 above. If our cron service has an error-backoff mechanism (e.g., jobs that fail are retried with increasing delays), a maintenance repair that recomputes next-run should NOT reset the backoff to zero. Otherwise, a transiently-failing job could start firing immediately after a restart/maintenance event.
**Implementation Notes:** In `cron_service.py`, ensure the error-backoff floor (minimum delay between retries) is persisted in the database alongside the job state. When maintenance repair recomputes next-run, it should use `max(computed_next_run, current_time + error_backoff_floor)`. The backoff floor should only be reset on explicit user action (job edit/re-enable), never automatically.
**Effort:** S
**Priority:** 2

---

### 14. Cron/Scheduler: Maintenance Wake for Unscheduled Jobs

**OpenClaw Component:** Cron & Scheduling
**OpenClaw References:** `#66019, #66083` — @mbelinky
**Relevance:** MEDIUM
**Recommendation:** INVESTIGATE
**Our Counterpart:** `src/universal_agent/cron_service.py`
**Gap Analysis:** OpenClaw keeps a "maintenance wake" armed so enabled-but-unscheduled jobs (e.g., jobs with no valid cron expression) can recover when their schedule is fixed, rather than permanently going silent.
**Implementation Notes:** In our cron service, add a periodic "maintenance sweep" (e.g., every 5-10 minutes) that re-evaluates jobs that are in an error/unschedulable state. If a job's cron expression is now valid (possibly because it was fixed), re-enable it. This prevents the operational headache of having to manually restart the service after fixing a broken cron expression.
**Effort:** S
**Priority:** 4

---

### 15. Memory: Active Memory Plugin (Proactive Recall Sub-Agent)

**OpenClaw Component:** Memory & Search
**OpenClaw References:** `#63286` — @Takhoffman
**Relevance:** HIGH
**Recommendation:** INVESTIGATE
**Our Counterpart:** `src/universal_agent/memory/`, `src/universal_agent/prompt_builder.py`
**Gap Analysis:** This is the most architecturally significant new feature. OpenClaw now runs a dedicated memory sub-agent before each main reply turn, automatically pulling in relevant preferences, context, and past details. Our memory system (`src/universal_agent/memory/`) is relatively thin — we have `memory_search` MCP tools but no proactive recall that runs before each agent turn. Our `prompt_builder.py` assembles context but doesn't do a semantic memory recall step.
**Implementation Notes:** This is a pattern we should study deeply. The concept: before each agent turn, run a lightweight recall step that queries memory with the current conversation context and injects relevant memories into the system prompt. In our architecture, this would be: (1) add a `recall_phase` in `agent_core.py` or `prompt_builder.py` that runs `memory_search` with the user's latest message, (2) inject top-K results into the system prompt, (3) make this configurable (on/off, result count, context window). Start with keyword-based search (our existing `memory_search`) and evolve to semantic. This would significantly improve agent continuity across sessions.
**Effort:** L
**Priority:** 3

---

### 16. Memory: Move Recalled Memory to Hidden Untrusted Prompt-Prefix Path

**OpenClaw Component:** Memory & Search
**OpenClaw References:** `#66144` — @Takhoffman
**Relevance:** MEDIUM
**Recommendation:** INVESTIGATE
**Our Counterpart:** `src/universal_agent/prompt_builder.py`
**Gap Analysis:** Related to #15. OpenClaw moved recalled memories from system prompt injection to a hidden untrusted prompt-prefix path. This prevents the model from treating recalled memories as ground-truth instructions (which could be manipulated) and keeps them as context-only.
**Implementation Notes:** When implementing our active memory recall (#15), follow this pattern: inject recalled memories with a label like `[Recalled Context — untrusted]` in a separate section of the prompt, distinct from system instructions. This is a prompt engineering best practice for preventing prompt injection via poisoned memory.
**Effort:** Included in #15
**Priority:** 3

---

### 17. Memory: QMD Fix — Stop Treating Legacy Lowercase `memory.md` as Second Root

**OpenClaw Component:** Memory & Search
**OpenClaw References:** `#66141` — @mbelinky
**Relevance:** LOW
**Recommendation:** SKIP
**Our Counterpart:** N/A
**Gap Analysis:** This is a QMD (OpenClaw's memory search engine) specific fix. Our memory system doesn't have a dual-collection ambiguity issue.
**Implementation Notes:** N/A
**Effort:** N/A
**Priority:** N/A

---

### 18. Memory: Dreaming Replay Guard (Require Live Cron Event)

**OpenClaw Component:** Memory & Search
**OpenClaw References:** `#66139` — @mbelinky
**Relevance:** MEDIUM
**Recommendation:** WATCH
**Our Counterpart:** `src/universal_agent/heartbeat_service.py`, `src/universal_agent/cron_service.py`
**Gap Analysis:** Our heartbeat service can trigger recurring actions. If a scheduled memory sweep (similar to OpenClaw's "Dreaming") runs and completes, but a subsequent heartbeat also tries to trigger it, we could get duplicate processing. We should ensure that cron-triggered actions have a consumed-event guard.
**Implementation Notes:** When implementing proactive memory maintenance (if/when we add it), ensure the heartbeat hook checks for an active/consumed cron event before triggering the sweep. Add a "last_sweep_time" or "event_consumed" guard to prevent replay on subsequent heartbeats.
**Effort:** S (when building memory maintenance)
**Priority:** 4

---

### 19. Messaging: Telegram Forum Topic Names in Agent Context

**OpenClaw Component:** Messaging Channels / Telegram
**OpenClaw References:** `#65973` — @ptahdunbar
**Relevance:** MEDIUM
**Recommendation:** INVESTIGATE
**Our Counterpart:** `src/universal_agent/bot/core/`, `src/universal_agent/bot/normalization/`
**Gap Analysis:** Our Telegram bot integration (`src/universal_agent/bot/`) handles messages but may not surface forum topic names as structured context. When users interact in forum-style groups, the agent should know which topic thread it's responding in.
**Implementation Notes:** In our Telegram message handler (`src/universal_agent/bot/core/`), extract the forum topic name from incoming messages (Telegram provides `forum_topic_created` service messages and `message_thread_id`). Include the topic name in the normalized message context passed to the agent. This improves the agent's ability to maintain topic-aware conversations in forum groups.
**Effort:** S
**Priority:** 4

---

### 20. Messaging: Heartbeat Topic Isolation on Telegram Forums

**OpenClaw Component:** Messaging Channels / Telegram
**OpenClaw References:** `#66035` — @mbelinky
**Relevance:** MEDIUM
**Recommendation:** WATCH
**Our Counterpart:** `src/universal_agent/bot/heartbeat_adapter.py`
**Gap Analysis:** Our heartbeat adapter sends heartbeat results to Telegram. If the bot is in a forum group, heartbeat replies should stay in the bound forum topic, not leak into the group root chat.
**Implementation Notes:** In `heartbeat_adapter.py`, ensure that when sending heartbeat results to a forum group, the `message_thread_id` is preserved from the triggering message. Add a check: if the original message was in a forum topic, the reply must also go to that topic.
**Effort:** XS
**Priority:** 4

---

### 21. Messaging: Send Policy Fix (Deny ≠ Block Processing)

**OpenClaw Component:** General / Delivery
**OpenClaw References:** `#65461, #53328` — @omarshahine
**Relevance:** LOW
**Recommendation:** SKIP
**Our Counterpart:** N/A
**Gap Analysis:** This fix ensures that `sendPolicy: "deny"` blocks outbound delivery but still allows the agent to process the inbound message (useful for observer-mode setups). Our system doesn't have an equivalent send-policy mechanism — we either process and respond or don't.
**Implementation Notes:** If we ever add a "listen-only" or observer mode to our Telegram/Slack integration, remember this pattern: decouple "process the message" from "send the reply."
**Effort:** N/A
**Priority:** N/A

---

### 22. General: Delivery Queue Persistence of Session Context

**OpenClaw Component:** General / Delivery Queue
**OpenClaw References:** `#66025` — @eleqtrizit
**Relevance:** MEDIUM
**Recommendation:** INVESTIGATE
**Our Counterpart:** `src/universal_agent/durable/`, message queue in gateway
**Gap Analysis:** If we queue outbound messages for delivery (e.g., write-ahead queue for reliability), we may lose session context if the agent restarts. OpenClaw now persists the originating session context with queued delivery entries.
**Implementation Notes:** Audit our outbound message delivery path. If we have any queuing mechanism (write-ahead log, Redis queue), ensure the original session context (routing target, delivery policy, origin metadata) is persisted alongside the message payload. This prevents "plain message degradation" after restarts. Our `durable/checkpointing.py` may already handle this — verify.
**Effort:** M
**Priority:** 4

---

### 23. Control UI: Replace marked.js with markdown-it (ReDoS Fix)

**OpenClaw Component:** Control UI
**OpenClaw References:** `#46707` — @zhangfnf
**Relevance:** MEDIUM
**Recommendation:** INVESTIGATE
**Our Counterpart:** `web-ui/` (Next.js dashboard)
**Gap Analysis:** If our web-ui renders user-generated markdown using marked.js (or any regex-based parser), it could be vulnerable to ReDoS (Regular Expression Denial of Service) via maliciously crafted markdown.
**Implementation Notes:** Check `web-ui/package.json` for markdown rendering dependencies. If using `marked`, evaluate migrating to `markdown-it` which has better performance and security characteristics. At minimum, add input sanitization/length limits on user-provided markdown before rendering.
**Effort:** S
**Priority:** 4

---

### 24. General: Per-Provider Private Network Allowlist

**OpenClaw Component:** Models / Providers
**OpenClaw References:** `#63671` — @qas
**Relevance:** LOW
**Recommendation:** SKIP
**Our Counterpart:** N/A
**Gap Analysis:** This adds per-provider `allowPrivateNetwork` config for self-hosted LLM endpoints. Our LLM provider configuration (in `.env` / Infisical) doesn't have this complexity — we connect to external API endpoints directly.
**Implementation Notes:** N/A
**Effort:** N/A
**Priority:** N/A

---

### 25. Agent Runtime: Context Engine ID Validation

**OpenClaw Component:** Agent Runtime / Context Engines
**OpenClaw References:** `#63222` — @fuller-stack-dev
**Relevance:** MEDIUM
**Recommendation:** WATCH
**Our Counterpart:** `src/universal_agent/execution_engine.py`, MCP tool loading
**Gap Analysis:** If our system loads MCP tools or context engines dynamically, a mismatch between a registered tool's ID and its actual implementation could cause silent misbehavior. We should validate that loaded tools report the expected identity.
**Implementation Notes:** When loading MCP tools, verify that the tool's reported identity matches its registration slot. Add a validation step in our MCP tool initialization that fails fast on ID mismatch rather than allowing silent misrouting.
**Effort:** S
**Priority:** 4

---

### 26. Agent Runtime: Orphaned Message Recovery

**OpenClaw Component:** Agent Runtime / Queueing
**OpenClaw References:** `#65388` — @adminfedres and @vincentkoc
**Relevance:** LOW
**Recommendation:** SKIP
**Our Counterpart:** `src/universal_agent/gateway.py`
**Gap Analysis:** OpenClaw now carries orphaned mid-run user messages into the next prompt. Our system handles messages sequentially via the gateway, so orphaned messages are less likely. Low risk.
**Implementation Notes:** Monitor for dropped messages in high-concurrency scenarios. If observed, add a message queue drain step before transcript repair.
**Effort:** N/A
**Priority:** N/A

---

### 27. Agent Runtime: Turn Maintenance as Idle-Aware Background Work

**OpenClaw Component:** Agent Runtime
**OpenClaw References:** `#65233` — @100yenadmin
**Relevance:** LOW
**Recommendation:** SKIP
**Our Counterpart:** N/A
**Gap Analysis:** OpenClaw runs opt-in turn maintenance (context pruning, etc.) as idle-aware background work. Our system handles this at the prompt-builder level, which is sufficient for our current scale.
**Implementation Notes:** N/A
**Effort:** N/A
**Priority:** N/A

---

### 28. Agent Runtime: OpenAI GPT Reasoning Effort Mapping

**OpenClaw Component:** Agent Runtime / OpenAI
**OpenClaw References:** (steipete)
**Relevance:** LOW
**Recommendation:** SKIP
**Our Counterpart:** N/A
**Gap Analysis:** Maps `minimal` thinking to OpenAI's `low` reasoning effort for GPT-5.4. We don't use OpenAI's reasoning effort parameter in our current configuration.
**Implementation Notes:** N/A
**Effort:** N/A
**Priority:** N/A

---

### 29. Plugin SDK: Narrow Plugin Loading to Manifest-Declared Needs

**OpenClaw Component:** Plugin SDK
**OpenClaw References:** `#65120, #65259, #65298, #65429, #65459` — @vincentkoc
**Relevance:** LOW
**Recommendation:** SKIP
**Our Counterpart:** N/A
**Gap Analysis:** This optimizes OpenClaw's plugin loading by scoping to manifest-declared needs. Our MCP tool loading is simpler and doesn't have a plugin manifest system.
**Implementation Notes:** N/A
**Effort:** N/A
**Priority:** N/A

---

### 30. Browser: Local Loopback CDP Bypass for SSRF Policy

**OpenClaw Component:** Browser / CDP
**OpenClaw References:** `#65695, #66043` — @mbelinky
**Relevance:** LOW
**Recommendation:** SKIP
**Our Counterpart:** N/A
**Gap Analysis:** This allows local Chrome CDP control to bypass browser SSRF policy for loopback addresses. This is a companion fix to #2 (SSRF enforcement) — once we implement SSRF policy, we'd need the same loopback bypass. But we don't have SSRF policy yet, so this is moot.
**Implementation Notes:** When implementing #2, include a loopback bypass for our own browser control plane (typically localhost:9222 for CDP).
**Effort:** Included in #2
**Priority:** N/A

---

### 31. macOS Client: Local MLX Speech Provider

**OpenClaw Component:** macOS Client
**Relevance:** NOT_APPLICABLE
**Recommendation:** SKIP
**Our Counterpart:** N/A
**Gap Analysis:** Platform-specific macOS feature. Not transferable.
**Implementation Notes:** N/A
**Effort:** N/A
**Priority:** N/A

---

### 32. Exec Policy: CLI Commands for Exec Approvals

**OpenClaw Component:** CLI / Exec Policy
**OpenClaw References:** `#64050`
**Relevance:** LOW
**Recommendation:** INVESTIGATE
**Our Counterpart:** `src/universal_agent/guardrails/`, `src/universal_agent/execution_engine.py`
**Gap Analysis:** OpenClaw adds CLI commands (`openclaw exec-policy show/preset/set`) for managing execution approval policies. We have guardrails but no CLI management interface for them.
**Implementation Notes:** Consider adding a CLI interface for viewing and managing execution policies (allowed tools, sandbox rules, permission overrides). This would improve operational visibility and debugging. Low priority — our guardrails are primarily code-configured.
**Effort:** S
**Priority:** 5

---

### 33. Control UI: Hide Synthetic Transcript-Repair Tool Results

**OpenClaw Component:** Control UI
**OpenClaw References:** `#65247` — @wangwllu
**Relevance:** LOW
**Recommendation:** WATCH
**Our Counterpart:** `web-ui/`
**Gap Analysis:** OpenClaw hides internal recovery tool results from visible chat. Our web-ui may leak internal tool-call results (like memory_search or system diagnostics) into the visible chat transcript.
**Implementation Notes:** When displaying agent responses in the web-ui, filter out tool-call results from system/internal tools. Only show the final text response and explicitly user-facing tool outputs.
**Effort:** XS
**Priority:** 5

---

### 34. Doctor: Warn on Orphaned Agent Directories

**OpenClaw Component:** Doctor / Maintenance
**OpenClaw References:** `#65113` — @neeravmakwana
**Relevance:** LOW
**Recommendation:** SKIP
**Our Counterpart:** N/A
**Gap Analysis:** OpenClaw warns about orphaned agent directories. Our VP worker system may have similar stale workspace issues, but this is a minor maintenance concern.
**Implementation Notes:** Consider adding a cleanup check in our session reaper (`src/universal_agent/session/reaper.py`) for orphaned VP worker workspaces.
**Effort:** XS
**Priority:** 5

---

### 35-37. QA/Docs/Platform-Specific

**Relevance:** NOT_APPLICABLE
**Recommendation:** SKIP
**Our Counterpart:** N/A
**Gap Analysis:** QA infrastructure (Convex-backed credential leasing, multipass VM testing), docs i18n improvements, and memory-wiki bridge-mode docs are specific to OpenClaw's tooling and not transferable patterns.
**Implementation Notes:** N/A

---

## Recurring Innovation Gap Check

Previous watch items checked against this release:

| Previous Watch Item | Status in This Release | Action |
|---|---|---|
| SSRF Bypass Prevention After Browser Interactions | **UPGRADED** — Now enforced on snapshot/screenshot/tab routes (#66040) | ADOPT (see #2) |
| Gateway Sessions: Clear Auto-Fallback Model Overrides | No direct match, but session routing metadata isolation (#66073) is related | Covered by #7 |
| Dotenv Security: Block Runtime-Control Env Vars | No direct match in this release | Keep watching |
| Character-Vibes QA Evaluation Reports | No match | Keep watching |
| Matrix Gateway Crash Isolation | No match | Keep watching |
| LLM Idle Timeout Inheritance | No match | Keep watching |
| Vendor Error Classification for Failover | No match | Keep watching |

**Recurring Innovation Gap — SSRF Bypass Prevention:** This has now appeared in multiple releases with escalating enforcement (initial policy -> browser routes -> loopback bypass). **We should prioritize building SSRF protection for our browser automation tools.** The pattern is clear and the attack surface is real.

---

## Summary Table

| # | Feature | Relevance | Recommendation | Effort | Priority |
|---|---------|-----------|----------------|--------|----------|
| 1 | Hook:wake owner downgrade | HIGH | ADOPT | S | 2 |
| 2 | SSRF on browser routes | HIGH | ADOPT | S | 2 |
| 3 | Config alias redaction | MEDIUM | ADOPT | S | 3 |
| 4 | Empty approver -> deny | MEDIUM | ADOPT | XS | 3 |
| 5 | Shell env-argv injection | MEDIUM | ADOPT | S | 3 |
| 7 | Session routing isolation | HIGH | ADOPT | M | 2 |
| 9 | Commands.list RPC | MEDIUM | INVESTIGATE | M | 4 |
| 10 | Auth hardening (placeholders) | MEDIUM | ADOPT | XS | 4 |
| 12 | Cron refire loop prevention | HIGH | ADOPT | S | 2 |
| 13 | Error-backoff floor preservation | HIGH | ADOPT | S | 2 |
| 14 | Maintenance wake for cron | MEDIUM | INVESTIGATE | S | 4 |
| 15 | Active Memory plugin | HIGH | INVESTIGATE | L | 3 |
| 16 | Memory as untrusted prefix | MEDIUM | INVESTIGATE | (with #15) | 3 |
| 18 | Dreaming replay guard | MEDIUM | WATCH | S | 4 |
| 19 | Telegram forum topic names | MEDIUM | INVESTIGATE | S | 4 |
| 20 | Heartbeat topic isolation | MEDIUM | WATCH | XS | 4 |
| 22 | Delivery queue session persistence | MEDIUM | INVESTIGATE | M | 4 |
| 23 | Markdown-it (ReDoS fix) | MEDIUM | INVESTIGATE | S | 4 |
| 25 | Context engine ID validation | MEDIUM | WATCH | S | 4 |
| 32 | Exec-policy CLI | LOW | INVESTIGATE | S | 5 |
| 33 | Hide repair tool results | LOW | WATCH | XS | 5 |

---

## Top 5 Implementation Priorities

1. **Session routing metadata isolation** (P2) — Prevents heartbeat/cron turns from poisoning shared session state. Architecturally critical.
2. **SSRF policy on browser routes** (P2) — Security hardening for our agent-browser MCP tools.
3. **Cron refire loop prevention + error-backoff preservation** (P2) — Reliability fixes for our cron service.
4. **Hook:wake owner downgrade** (P2) — Security: prevents privilege escalation via external triggers.
5. **Active Memory pattern** (P3) — Largest effort but highest architectural impact for agent continuity.
