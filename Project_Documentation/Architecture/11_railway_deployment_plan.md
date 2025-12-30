# Railway Deployment Architecture

**Document Version**: 1.0
**Last Updated**: 2025-12-30
**Status**: ✅ COMPLETED (Deployed Dec 30 2025)
**Primary Components**: `Dockerfile`, `railway.json`, `Webhooks`

---

## Table of Contents

1. [Overview](#overview)
2. [Containerization Strategy](#containerization-strategy)
3. [Environment Configuration](#environment-configuration)
4. [Persistence (Volumes)](#persistence-volumes)
5. [Telegram Webhook Architecture](#telegram-webhook-architecture)
6. [Agent College Service](#agent-college-service)

---

## Overview

We are deploying the Universal Agent to **Railway.app** to transition from a local CLI tool to an **always-on cloud service**.

### Goals
1.  **GitHub Integrated**: Deploys automatically on `git push main`.
2.  **Always Online**: Telegram bot responds 24/7.
3.  **Stateful**: Memory and Workspaces survive restarts.

---

## Deployment Workflow (GitHub)

We will use **Railway's GitHub Integration** for a seamless CI/CD pipeline.

1.  **Connect**: Link GitHub repository to Railway Project.
2.  **Trigger**: Commits to `main` branch trigger a new build.
3.  **Build**: Railway detects `Dockerfile`, builds the image.
4.  **Deploy**: New container replaces the old one (with rolling updates).

### Pre-Deployment Checklist
Before pushing to GitHub:
- [x] `Dockerfile` present in root.
- [x] `.dockerignore` filters out `AGENT_RUN_WORKSPACES` (local) and `__pycache__`.
- [x] `railway.json` (optional) or Dashboard config set.
- [x] Secrets (API Keys) added to Railway Dashboard variables.

---

## Containerization Strategy

We will use a multi-stage `Dockerfile` to handle our complex dependencies:

### Base Image
*   **OS**: Debian Bookworm (Slim) - needed for `apt` packages.
*   **Python**: 3.12+

### System Dependencies & The "Headless Browser" Challenge

**Risk**: Running headless Chrome/Playwright in Docker is notoriously unstable (memory leaks, crashes, missing libs).
**Mitigation**: We will prioritize **API-based services** where possible, but keep system libraries for local fallbacks (PDF/Video).

#### 1. Web Crawling Strategy
*   **Primary (Production)**: Use `CRAWL4AI_API_KEY` (offload to Cloud API).
*   **Backup (Local)**: Local `crawl4ai` (requires Playwright/Chrome).
*   **Configuration**: Set `CRAWL4AI_API_KEY` in Railway Variables.

#### 2. PDF & Video Dependencies
Even if we offload crawling, we still need system libraries for:
*   **Video**: `ffmpeg` (Required for video skills).
*   **PDF**: `google-chrome-stable` (Preferred) or `weasyprint` (requires `libcairo2`, `libpango`).

### Dockerfile Strategy
Our `Dockerfile` must install these deps explicitly.

```dockerfile
# Packages required for Chrome, FFmpeg, and WeasyPrint
RUN apt-get update && apt-get install -y \
    ffmpeg \
    google-chrome-stable \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    ...
```

### Python Environment
*   **Toolchain**: `uv` (Astral) for fast dependency management.
*   **Project**: Install via `uv sync --frozen --no-dev`.

```dockerfile
# Conceptual Dockerfile
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Install Chrome & FFmpeg
RUN apt-get update && apt-get install -y ffmpeg google-chrome-stable ...

# Install Deps
RUN uv sync --frozen

# Entrypoint
CMD ["uv", "run", "src/universal_agent/main.py"]
```

---

## Environment Configuration

We need to map our `.env` to Railway Variables. This list is comprehensive.

### Core Identity & AI
| Variable | Usage | Railway Type |
|----------|-------|--------------|
### Core Identity & AI
| Variable | Usage | Railway Type |
|----------|-------|--------------|
| `ANTHROPIC_API_KEY` | LLM Inference (Claude/Z.AI) | Secret |
| `ANTHROPIC_BASE_URL` | Z.AI Proxy URL (e.g. `https://api.z.ai...`) | Variable |
| `ZAI_API_KEY` | Z.AI Vision/Proxy | Secret |
| `COMPOSIO_API_KEY` | Tool Router (Gmail, GitHub, etc) | Secret |
| `DEFAULT_USER_ID` | Composio Identity (`pg-test-...`) | Variable |
| `CRAWL4AI_API_KEY` | Cloud Crawling Service | Secret |
| `GEMINI_API_KEY` | Google Gemini (Vision Fallback) | Secret |
| `EXA_API_KEY` | Exa Search (Optional) | Secret |
| `CONTEXT7_API_KEY`| Context7 Tools | Secret |
| `MODEL_NAME` | LLM Model ID | Variable |
| `LOGFIRE_TOKEN` | Observability & Tracing | Secret |
| `LOGFIRE_PROJECT` | Logfire Project Slug | Variable |
| `LOGFIRE_ORG` | Logfire Organization | Variable |

### Telegram Bot (Runtime)
| Variable | Purpose | Value / Example |
|----------|---------|-----------------|
| `TELEGRAM_BOT_TOKEN` | Bot Authentication | `123456:ABC-DEF...` |
| `ALLOWED_USER_IDS` | Security Whitelist | `12345678, 87654321` (Comma separated) |
| `WEBHOOK_URL` | Public Endpoint | `https://universal-agent.up.railway.app/webhook` |
| `WEBHOOK_SECRET` | Securing the Endpoint | `random-string-123` |
| `PORT` | Web Server Port | `${PORT}` (Railway sets this automatically) |
| `MAX_CONCURRENT_TASKS`| Async Limit | `5` (Increase for production) |

### Persistence
| Variable | Purpose | Value |
|----------|---------|-------|
| `PERSIST_DIRECTORY` | Memory DB Path | `/app/data/memory` |
| `LOGFIRE_DB_PATH` | Logfire Cache | `/app/data/logfire.db` |
| `AGENT_WORKSPACE_ROOT`| Artifacts storage | `/app/data/workspaces` |

---

## Persistence (Volumes)

The agent is **stateful**. We cannot use an ephemeral filesystem.

### Required Volumes
1.  **Agent Workspaces**: `AGENT_RUN_WORKSPACES/`
    *   Stores: Search results, reports, artifacts, run logs.
    *   Mount: `/app/AGENT_RUN_WORKSPACES`
2.  **Memory Database**: `Memory_System_Data/`
    *   Stores: `agent_core.db` (Letta memory).
    *   Mount: `/app/Memory_System_Data`

**Railway Configuration**:
*   Add a **Volume** service.
*   Mount it to the main service at `/app/data` (recommended to consolidate mounts).
*   Update `LOGFIRE_DB_PATH` and code to point to the mount.

---

## Telegram Webhook Architecture

### Current (Local)
*   **Method**: `updater.start_polling()`
*   **Pros**: Easy, works behind NAT.
*   **Cons**: Slow, resource intensive, drops during restarts.

### Future (Railway)
*   **Method**: `updater.start_webhook()`
*   **Flow**:
    `Telegram Cloud` → `POST https://universal-agent.railway.app/webhook` → `Bot`
*   **Setup**:
    1.  Expose `PORT` (e.g., 8000).
    2.  Set `WEBHOOK_URL` env var.
    3.  **Code Change Needed**: Update `bot/main.py` to automatically call `await bot.set_webhook(url=WEBHOOK_URL)` on startup if the env var is present.

---

## Cost Reality & "Always-On" Architecture

**Q: Will this run (and bill) 24/7?**
**A: YES.**

### Why can't we "Scale to Zero"?
While Railway and Telegram Webhooks *can* support "Serverless" (wake on request), the **Universal Agent cannot** for two reasons:
1.  **Statefulness (The Brain)**: We rely on `Memory_System_Data` (SQLite) and `AGENT_RUN_WORKSPACES` (Files). These require a **Persistent Volume**. Attaching/detaching volumes on every request is too slow and complex for this architecture.
2.  **Startup Time**: The agent loads heavy imports (Logfire, Composio SDK, internal tools) on startup. A "Cold Start" takes 5-10 seconds. Telegram requires a response in <3 seconds or it retries/fails.

### Cost Estimation (Railway)
You are paying for **RAM Reservation** (24/7) and **CPU Usage** (Bursts).
*   **Idle**: You pay for the RAM (e.g., 512MB or 1GB) keeping the "Brain" loaded.
*   **Active**: You pay for the CPU cycles when processing a query.

**Verdict**: This is a "Bot Server" model, not a "Lambda Function" model. Expect a small but constant monthly cost (typically $5-$10/mo depending on resource tier) to keep the agent's memory alive and ready updates.

---

## Agent College Service

We have a second service: `AgentCollege/logfire_fetch/main.py` (FastAPI).

### Deployment Options

**Option A: Monolith (Supervisord)**
*   Run `main.py` (Bot) AND `uvicorn` (College) in the SAME container.
*   **Pros**: Shared file access (easy DB sharing), cheaper (1 service).
*   **Cons**: Complex Dockerfile entrypoint.

**Option B: Microservices**
*   Service 1: Bot
*   Service 2: College API
*   **Pros**: Clean separation.
*   **Cons**: **Database Locking**. Since both use SQLite (`agent_core.db`), they CANNOT easily write to the same file if separated into different containers (Railway Volumes don't support multi-writer well across instances).

**Decision**: **Option A (Monolith)** is safer for SQLite. We will use a `start.sh` script to launch both processes.
