# 09. Deployment Strategy and Railway Guidance

## 1. Goals
- Deploy UA so it can run **remotely** (no dependency on local PC).
- Support **web UI**, **Telegram**, and **heartbeat** reliably.
- Optimize for **low usage** but still allow **overnight runs**.

## 2. Deployment realities
### 2.1 Heartbeat requires uptime
If heartbeat is enabled, the gateway must **stay online**:
- Scale-to-zero means missed heartbeats.
- If sleep mode is desired, heartbeat must be disabled or moved to a separate always-on worker.

### 2.2 Long-running tasks require stable runtime
- URW/harness runs and long tasks benefit from a single always-on service.
- The runtime DB (`runtime.db`) and workspace artifacts should persist between runs.

## 3. Railway as the primary option
Railway is viable and convenient for a small deployment:

### Pros
- Simple deployment with public URL
- Supports WebSocket traffic (needed for gateway)
- Good for webhook-based Telegram

### Cons / constraints to verify
- **Sleep/scale-to-zero** policies may interrupt heartbeats.
- **Persistent storage** needs verification (workspaces + runtime DB).

## 4. Recommended Railway topology
### Option R1 (simplest)
Single service that runs:
- Gateway server
- Web UI (if bundled)
- Telegram bot (if integrated)

Pros: lowest cost, simplest to manage.
Cons: mixed concerns in a single process.

### Option R2 (preferred for clarity)
Two services:
1. **Gateway/Web UI** service
2. **Telegram bot** service (lightweight client of gateway)

Pros: clearer separation, easier to scale, easier to restart bot without affecting gateway.

## 5. Cost + performance considerations
### Low usage, high availability
- Prefer **small always-on** instance.
- Heartbeat and long-running tasks depend on uptime.

### Spiky usage
- If you want scale-to-zero, disable heartbeat or move heartbeat to a small always-on worker.

## 6. Alternative deployment options
### Fly.io
- Good for long-running workloads, can keep small instances alive.
- Allows multiple regions.

### Render
- Similar to Railway, but free tiers may sleep.

### Small VPS (cheap + always-on)
- Most control, predictable uptime.
- Best if you want heartbeat to run continuously.

## 7. Recommendation
- **Primary**: Railway with an always-on service if heartbeat is required.
- If you want sleep mode sometimes, implement a **heartbeat toggle** to avoid false expectations.

## 8. Deployment checklist (summary)
- Ensure env vars:
  - `ANTHROPIC_API_KEY`
  - `LOGFIRE_TOKEN` (optional)
  - `TELEGRAM_BOT_TOKEN`, `WEBHOOK_URL`, `WEBHOOK_SECRET` (if Telegram enabled)
- Confirm persistent storage for:
  - `AGENT_RUN_WORKSPACES/`
  - `runtime.db`
- Confirm WebSocket support in Railway (gateway uses WS).
- Set resource limits to avoid runaway cost.
