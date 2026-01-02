# 000: Current Project Context

> [!IMPORTANT]
> **For New AI Agents**: Read this document first to understand the current state of the project.
> This is a living document that tracks where we are and where we're going.

**Last Updated**: 2026-01-02 00:15 CST

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

## 📍 Current State (January 2, 2026)

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
| **Durable Runs (Phase 0–2)** | ✅ Working | Run/step tracking, checkpoints, resume UX |
| **Filtered Research Corpus** | ✅ Working | `finalize_research` + filtered corpus + overview |

### 🆕 Recent Fixes (Jan 1–2, 2026)

1. **Durable Jobs Phase 0–2**: Runtime DB, tool-call ledger, idempotency, step checkpoints, resume UX.
2. **Ctrl-C Reliability**: SIGINT handler saves interrupt checkpoints; fallback to last step_id in DB.
3. **Filtered Research Pipeline**: `finalize_research` builds filtered corpus + `research_overview.md`.
4. **Filter Tuning**: Looser drop thresholds; explicit filtered vs dropped tables in overview.
5. **Report Prompt Unification**: Report sub-agent now uses filtered corpus only (no raw crawl reads).
6. **MCP Server Fix**: Syntax/indent error fixed; Crawl4AI Cloud API handling stabilized.

---

## 🚧 Known Issues & Next Steps

### ✅ RESOLVED: Session Persistence After Task (Fixed Dec 31, 2025)
**Fix**: Watchdog timeout + Worker health checks implemented in Bot.

### 🟡 Known Issues / In Progress

| Issue | Status | Notes |
|-------|--------|-------|
| Resume does not auto-continue | ⏳ Pending | Resume loads checkpoint but waits for new input |
| Multiple local-toolkit trace IDs | ⏳ Known | Local MCP uses multiple trace IDs per window |
| Agent College not auto-triggered | ⏳ Pending | Requires manual invocation |
| `/files` command not implemented | ⏳ Pending | Users can't download artifacts |
| `/stop` command not implemented | ⏳ Pending | Can't cancel running tasks |

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

### Local Development (CLI)
```bash
cd /home/kjdragan/lrepos/universal_agent

# Start Agent College + CLI
./local_dev.sh
```

### Durable Test Run (CLI)
```bash
PYTHONPATH=src uv run python -m universal_agent.main --job /home/kjdragan/lrepos/universal_agent/src/universal_agent/durable_demo.json
```

Interrupt with Ctrl-C to save a checkpoint. Resume with:
```bash
PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>
```

Latest resume command is written to:
`Project_Documentation/Long_Running_Agent_Design/KevinRestartWithThis.md`

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
| 6 | `Project_Documentation/Long_Running_Agent_Design/` | Durable Jobs v1 + tracking |

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
