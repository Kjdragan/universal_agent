# 000: Current Project Context

> [!IMPORTANT]
> **For New AI Agents**: Read this document first to understand the current state of the project.
> This is a living document that tracks where we are and where we're going.

**Last Updated**: 2025-12-30 16:15 CST

---

## 🎯 Project Overview

**Universal Agent** is a standalone agent using Claude Agent SDK with Composio Tool Router integration.

**Core Capabilities**:
- Claude Agent SDK for agentic workflows
- Composio Tool Router for 500+ tool integrations (Gmail, SERP, Slack, etc.)
- Crawl4AI parallel web extraction via local MCP server
- Sub-agent delegation for specialized tasks (report generation)
- Logfire tracing for observability
- **Letta-style Memory System** with Core Memory blocks (persona, human, system_rules)
- **Agent College** self-improvement subsystem
- Automatic workspace and artifact management
- Observer pattern for async result processing and error tracking

**Main Entry Point**: `src/universal_agent/main.py`
**MCP Server Tools**: `src/mcp_server.py`

---

## 📍 Current State (December 30, 2025)

### ✅ What's Working

| Feature | Status | Notes |
|---------|--------|-------|
| **Railway Deployment** | ✅ Production | US West, Static IP, Pro plan |
| **Telegram Bot** | ✅ Working | Webhook mode, FastAPI + PTB |
| **Research & Report Generation** | ✅ Production-ready | JIT Delegation via Knowledge Base |
| **PDF/PPTX Creation** | ✅ Working | Skills-based, conditional routing |
| **Email Delivery (Gmail)** | ✅ Working | Attachments via `upload_to_composio` |
| **Image Generation** | ✅ Working | Gemini 2.5 Flash Image |
| **Memory System** | ✅ Working | Core blocks, archival search |
| **Logfire Tracing** | ✅ Working | Dual Trace (Main + Subprocess) |

### 🆕 Recent Fixes (Dec 31, 2025)

1. **Double Execution Bug (CLI)**: Identify & Fixed a critical bug where `main.py` was calling `process_turn` then immediately re-running the task in a redundant loop. The CLI is now streamlined.
2. **Session Optimization**: `work_products/media` is now pre-created during session init, preventing runtime errors.
3. **Live Trace Saving**: `trace.json` is now saved incrementally after every turn (alongside `transcript.md`) for real-time debugging.
4. **Local Dev Script**: Added `./local_dev.sh` for easy one-command start of Agent College + CLI.

---

## 🚧 Known Issues & Next Steps

### ✅ RESOLVED: Session Persistence After Task (Fixed Dec 31, 2025)
**Fix**: Watchdog timeout + Worker health checks implemented in Bot.

### 🟡 Other Issues

| Issue | Status | Notes |
|-------|--------|-------|
| Agent College not auto-triggered | ⏳ Pending | Requires manual invocation |
| `/files` command not implemented | ⏳ Pending | Users can't download artifacts |
| `/stop` command not implemented | ⏳ Pending | Can't cancel running tasks |
| Document Run Instructions | ⏳ In Progress | `0000_how_to_run.md` created |

---

## 🏗️ Architecture Overview

### Railway Deployment

```
GitHub (git push main)
        │
        ▼
Railway Auto-Deploy
        │
        ▼
┌─────────────────────────────────────────┐
│ Container (python:3.12-slim-bookworm)   │
│                                         │
│  start.sh → Bot (FastAPI + PTB)         │
│           → Agent College (internal)    │
│                                         │
│  /app/data (Persistent Volume)          │
│   └── memory/, workspaces/              │
└─────────────────────────────────────────┘
```

**Production URL**: `https://web-production-3473.up.railway.app`

### Telegram Bot Flow

```
Telegram Cloud
    │
    │ HTTPS POST /webhook
    ▼
FastAPI (Uvicorn)
    │
    ▼
PTB Command Handlers
    │
    ▼
TaskManager (Queue)
    │
    ▼
AgentAdapter → Claude SDK
```

---

## 🧠 Agent College Architecture

```
Agent Runtime                    LogfireFetch Service
     │                                  │
     │ (errors/successes)               │
     ▼                                  ▼
  Logfire  ─────── polling ──────►  LogfireFetch
     │                                  │
     │                                  ▼
     │                            Critic/Scribe
     │                                  │
     │                                  ▼
     │                         [AGENT_COLLEGE_NOTES]
     │                           (Sandbox Memory)
     │                                  │
     │◄──────────────── read ───────────┘
     │
     ▼
  Professor (HITL Review)
     │
     ▼
  Graduation (New Skill / Rule)
```

---

## 🔧 Running the System

### Production (Railway)
Automatic on `git push main`. Monitor via Railway Dashboard.

### Local Development
```bash
cd /home/kjdragan/lrepos/universal_agent

# Start bot with ngrok
ngrok http 8080  # Get URL, update .env WEBHOOK_URL
uv run uvicorn universal_agent.bot.main:app --host 0.0.0.0 --port 8080
```

### Useful Commands
```bash
# Check webhook status
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"

# Force webhook registration
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=<URL>&secret_token=<SECRET>"

# Check health
curl https://web-production-3473.up.railway.app/health
```

---

## 📚 Key Documentation

| Priority | Document | Purpose |
|----------|----------|---------|
| 1 | `Telegram_Integration/` | Bot architecture & deployment |
| 2 | `Architecture/11_railway_deployment_plan.md` | Railway setup |
| 3 | `013_AGENT_COLLEGE_ARCHITECTURE.md` | Agent College overview |
| 4 | `012_LETTA_MEMORY_SYSTEM_MANUAL.md` | Memory System design |
| 5 | `002_LESSONS_LEARNED.md` | Patterns and gotchas |

---

## 🏗️ Project Structure

```
universal_agent/
├── src/
│   ├── universal_agent/
│   │   ├── main.py                 # Main agent
│   │   ├── bot/                    # Telegram bot
│   │   │   ├── main.py             # FastAPI + PTB
│   │   │   ├── config.py           # Environment vars
│   │   │   ├── telegram_handlers.py# Commands
│   │   │   ├── task_manager.py     # Async queue
│   │   │   └── agent_adapter.py    # Claude SDK bridge
│   │   └── agent_college/          # Professor, Critic, Scribe
│   └── mcp_server.py               # Local MCP tools
├── AgentCollege/                   # FastAPI service
├── Memory_System/                  # Letta-style memory
├── .claude/
│   ├── skills/                     # Skill definitions
│   └── knowledge/                  # Knowledge base
├── Project_Documentation/
│   ├── 000_CURRENT_CONTEXT.md      # This file
│   └── Telegram_Integration/       # Bot docs
├── Dockerfile                      # Container build
├── start.sh                        # Container entrypoint
└── AGENT_RUN_WORKSPACES/           # Session artifacts (local)
```

---

*Update this document whenever significant progress is made or context changes.*
