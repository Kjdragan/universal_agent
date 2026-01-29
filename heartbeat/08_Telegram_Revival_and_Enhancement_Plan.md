# 08. Telegram Revival and Enhancement Plan

## 1. Goals
- **Revive** the Telegram interface in a reliable, production-ready form.
- **Improve** it beyond the previous implementation (not just “bring it back”).
- **Align** it with the gateway execution model so all UIs share the same engine behavior.
- **Support** low-volume multi-user usage (family scale) safely.

## 2. Current state (from repo)
Telegram support exists, but it is **not gateway-aligned**:
- The bot uses `AgentAdapter` + `process_turn` directly, not `InProcessGateway`.
- Session handling is internal to the Telegram bot process.
- Startup uses webhook/polling logic + environment variables.

Key files:
- Bot service: `src/universal_agent/bot/main.py` @/home/kjdragan/lrepos/universal_agent/src/universal_agent/bot/main.py#23-231
- Handlers: `src/universal_agent/bot/telegram_handlers.py` @/home/kjdragan/lrepos/universal_agent/src/universal_agent/bot/telegram_handlers.py#12-117
- Task queue: `src/universal_agent/bot/task_manager.py` @/home/kjdragan/lrepos/universal_agent/src/universal_agent/bot/task_manager.py#21-107
- Agent adapter: `src/universal_agent/bot/agent_adapter.py` @/home/kjdragan/lrepos/universal_agent/src/universal_agent/bot/agent_adapter.py#32-207
- MCP Telegram toolkit: `src/universal_agent/mcp_server_telegram.py` @/home/kjdragan/lrepos/universal_agent/src/universal_agent/mcp_server_telegram.py#1-76

## 3. Target architecture (recommended)
### 3.1 Telegram bot becomes a **gateway client**
The Telegram bot should call the gateway API instead of running its own engine:
- `create_session` (per user)
- `execute` via WS or HTTP
- stream events + send final response back to Telegram

This keeps **parity** with web UI and CLI (same engine path).

### 3.2 Unified session mapping
- Maintain a mapping: `telegram_user_id -> gateway_session_id`.
- `/new` should request a new session.
- `/continue` should reuse the mapped session.

### 3.3 Event streaming -> Telegram
- **Short-term**: send only final response + summary stats (tool count, duration).
- **Mid-term**: send periodic progress updates (STATUS or heartbeat summary) to Telegram.

## 4. Major improvements (beyond “revival”)
### 4.1 Reliability & transport
- Prefer **webhook mode** in production (lower latency, fewer polling issues).
- Add **retry/backoff** when posting results to Telegram.
- Validate `WEBHOOK_SECRET` on every incoming update.

### 4.2 UX and features
- Command improvements:
  - `/agent <prompt>` (existing)
  - `/status` (show last run + heartbeat state)
  - `/cancel <task>` (future)
  - `/set <key> <value>` (future preference settings)
- Allow “summary-only” mode for long outputs.
- Provide trace URL + workspace reference in final message.

### 4.3 Session continuity + multi-user
- Keep **per-user session** in gateway.
- For family use (3–4 users), use an allowlist (existing `ALLOWED_USER_IDS`).
- Optional: add a lightweight “invite” flow in the future.

### 4.4 Heartbeat integration
- If heartbeat is enabled, allow Telegram to receive:
  - **alerts** only (default)
  - optional “OK” indicator messages (if user opts in)

## 5. Phased revival plan
### Phase 1 — Quick functional revival (low risk)
- Validate `TELEGRAM_BOT_TOKEN`, `WEBHOOK_URL`, `WEBHOOK_SECRET`.
- Ensure webhook registration works on Railway (public URL).
- Keep current `AgentAdapter` path temporarily.

### Phase 2 — Gateway alignment
- Replace `AgentAdapter` with a Gateway client:
  - Create / resume session per user
  - Use `execute` through WS
- Ensure parity with web UI results.

### Phase 3 — Enhanced UX
- Progress updates
- Cancellation support
- More robust formatting (long outputs, Markdown safety)

### Phase 4 — Multi-user hardening
- Role-based allowlist
- Optional rate limits
- Session cleanup policies

## 6. Risks / mitigation
- **Risk**: gateway WS not stable for Telegram.
  - Mitigation: optional HTTP execute fallback.
- **Risk**: large responses exceed Telegram limits.
  - Mitigation: summary + file upload or chunking.
- **Risk**: multiple concurrent tasks per user.
  - Mitigation: single active task per user in the bot layer.

## 7. Success criteria
- Telegram bot can send a request and receive a reply reliably.
- Output matches the web UI (parity).
- Heartbeat alerts can optionally reach Telegram.
- Works reliably when hosted remotely (Railway or alternative).
