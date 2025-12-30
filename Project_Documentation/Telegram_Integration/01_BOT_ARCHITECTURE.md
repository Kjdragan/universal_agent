# Bot Architecture Deep Dive

**Last Updated**: 2025-12-30

This document provides a detailed breakdown of how the Telegram bot works internally.

---

## 1. Application Lifecycle (`main.py`)

The bot uses FastAPI's **lifespan** context manager for startup/shutdown:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP
    1. Initialize AgentAdapter (loads Claude SDK session)
    2. Create PTB Application with HTTPXRequest (60s timeouts)
    3. Setup TaskManager with notification callback
    4. Register command handlers (/start, /agent, /status, /help)
    5. Attempt webhook registration (with 3 retries)
    6. Start background worker task
    
    yield  # App is running
    
    # SHUTDOWN
    1. Cancel worker task
    2. Stop and shutdown PTB app
```

### Resilient Startup (Degraded Mode)

If the bot cannot reach Telegram's API on startup (network issues), it will:
1. Log the error with manual fix instructions
2. Start in "degraded mode" instead of crashing
3. Keep the container running so webhooks can still be received

---

## 2. Webhook Handling

```python
@app.post("/webhook")
async def telegram_webhook(request: Request):
    # 1. Verify secret token header
    secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret_token != WEBHOOK_SECRET:
        return {"detail": "Unauthorized"}
    
    # 2. Parse and process update
    data = await request.json()
    update = Update.de_json(data, ptb_app.bot)
    await ptb_app.process_update(update)
    return {"status": "ok"}
```

---

## 3. Task Queue System

```
User sends /agent <prompt>
        │
        ▼
┌───────────────────┐
│  agent_command()  │  Creates Task object, enqueues
└─────────┬─────────┘
          │ queue.put(task_id)
          ▼
┌───────────────────┐
│  TaskManager      │  Background worker loop
│  worker()         │  Processes one at a time
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│  AgentAdapter     │  Bridges to main.py
│  execute(task)    │  Logs to per-task file
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│  Main Agent       │  Runs Claude SDK
│  process_turn()   │  
└───────────────────┘
```

### Task States

| Status | Icon | Description |
|--------|------|-------------|
| `pending` | ⏳ | Queued, waiting for worker |
| `running` | 🔄 | Currently executing |
| `completed` | ✅ | Finished successfully |
| `error` | ❌ | Failed with exception |

---

## 4. Notification Flow

When a task changes status, the `status_callback` sends a Telegram message:

1. **On Start**: "Task Update: `abc123` Status: RUNNING"
2. **On Complete**: Includes result preview (first 500 chars) + log file attachment
3. **On Error**: Includes error message + log file attachment

---

## 5. Request Timeout Configuration

The bot uses custom `HTTPXRequest` settings to avoid timeouts:

```python
HTTPXRequest(
    connect_timeout=60.0,
    read_timeout=60.0,
    write_timeout=60.0,
    pool_timeout=60.0,
    http_version="1.1",  # Avoid HTTP/2 hangs
)
```

---

## 6. Authentication

Every command handler checks authorization first:

```python
async def check_auth(update: Update) -> bool:
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USER_IDS:
        await update.message.reply_text("⛔ Unauthorized access.")
        return False
    return True
```

`ALLOWED_USER_IDS` is loaded from the environment as a comma-separated list.
