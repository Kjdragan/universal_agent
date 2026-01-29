# 07. Telegram UI Investigation (Existing Code + Gaps)

## 1. Goal
Identify the current Telegram integration state, what exists today, and what will be needed to make it reliable again.

## 2. Existing Telegram components (found in repo)
### 2.1 FastAPI Telegram bot service
**File**: `src/universal_agent/bot/main.py`

Highlights:
- Uses `python-telegram-bot` (PTB) with FastAPI lifecycle.
- Supports both **webhook** and **polling**.
- Registers webhook using env variables:
  - `TELEGRAM_BOT_TOKEN`
  - `WEBHOOK_URL`
  - `WEBHOOK_SECRET`
- Has retries and “degraded mode” if webhook registration fails.

References:
- Startup flow: @/home/kjdragan/lrepos/universal_agent/src/universal_agent/bot/main.py#23-204
- Webhook handler: @/home/kjdragan/lrepos/universal_agent/src/universal_agent/bot/main.py#217-231

### 2.2 Telegram handlers
**File**: `src/universal_agent/bot/telegram_handlers.py`

Capabilities:
- `/start`, `/help`, `/status`, `/agent <prompt>`, `/continue`, `/new`
- Simple **authorized-user** gating via `ALLOWED_USER_IDS`

References:
- Handlers: @/home/kjdragan/lrepos/universal_agent/src/universal_agent/bot/telegram_handlers.py#12-117

### 2.3 Task queue + execution adapter
**File**: `src/universal_agent/bot/task_manager.py`

- Simple queue for tasks, concurrency limit via `MAX_CONCURRENT_TASKS`.
- Uses **continuation mode** to keep session context.

References:
- Task queue + worker: @/home/kjdragan/lrepos/universal_agent/src/universal_agent/bot/task_manager.py#21-107

**File**: `src/universal_agent/bot/agent_adapter.py`

- Wraps `process_turn` and keeps a background client loop alive.
- Supports “continue session” to reuse the same workspace.
- Captures logs with `ExecutionLogger` and attaches to the task.

References:
- Agent session lifecycle: @/home/kjdragan/lrepos/universal_agent/src/universal_agent/bot/agent_adapter.py#32-207

### 2.4 Telegram formatter
**File**: `src/universal_agent/bot/telegram_formatter.py`

- Formats `ExecutionResult` for Telegram Markdown (with fallback).

References:
- Formatting logic: @/home/kjdragan/lrepos/universal_agent/src/universal_agent/bot/telegram_formatter.py#1-65

### 2.5 Startup helper script
**File**: `start_telegram_bot.sh`

- Uses ngrok to expose port 8000 and registers webhook.
- Starts Docker container and runs `register_webhook.py`.

References:
- Script: @/home/kjdragan/lrepos/universal_agent/start_telegram_bot.sh#1-88

### 2.6 MCP Telegram toolkit
**File**: `src/universal_agent/mcp_server_telegram.py`

- Provides MCP tools `telegram_send_message` and `telegram_get_updates`.
- Likely intended for agent-initiated Telegram actions.

References:
- MCP Telegram server: @/home/kjdragan/lrepos/universal_agent/src/universal_agent/mcp_server_telegram.py#1-76

## 3. Observed gaps / likely staleness
1. **Telegram bot is separate from gateway**
   - The Telegram bot uses `AgentAdapter` and a local execution session.
   - It does **not** use the new gateway / `InProcessGateway` path.
   - This creates divergence from the gateway web UI execution model.

2. **Authentication is simple**
   - `ALLOWED_USER_IDS` is static and env-driven.
   - No user/session management beyond that.

3. **Deployment assumptions**
   - The start script assumes local ngrok and docker-compose.
   - If running on Railway, webhook registration + TLS URLs need to be aligned.

4. **No shared session model across UIs**
   - Telegram sessions are local to the bot process.
   - Web UI sessions are managed by gateway.
   - A unified multi-UI design would route Telegram requests through the gateway.

## 4. Recommended integration direction (high level)
### 4.1 Short-term (revive Telegram quickly)
- Keep the existing bot and verify env + webhook flow.
- Ensure `ALLOWED_USER_IDS` is set.
- Use `AgentAdapter` as-is to validate basic functionality.

### 4.2 Medium-term (align with gateway)
- Replace `AgentAdapter` with gateway API calls.
- Telegram bot becomes a **client** of the gateway:
  - `create_session` per user
  - `execute` via WS or HTTP
- This ensures parity with web/CLI.

### 4.3 Long-term (multi-user)
- Introduce user-to-session mapping in the gateway.
- Use per-user heartbeat configurations and visibility.

## 5. Open questions
- Do we want Telegram to be a **first-class UI** with its own session semantics, or a “remote control” for the gateway?
- Should Telegram messages attach to an existing session if the user is also active in web UI?
- How do we persist user preferences (continuation mode, default session, etc.)?

## 6. Notes for Railway deployment
- Railway will provide the public URL; webhook should point there.
- We should remove ngrok-specific steps in production.
- Confirm PTB configuration uses the correct external URL and secret token.
