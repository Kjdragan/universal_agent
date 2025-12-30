# 000: Current Project Context

> [!IMPORTANT]
> **For New AI Agents**: Read this document first to understand the current state of the project.
> This is a living document that tracks where we are and where we're going.

**Last Updated**: 2025-12-30 08:30 CST

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
- **Agent College** self-improvement subsystem (NEW)
- Automatic workspace and artifact management
- Observer pattern for async result processing and error tracking

**Main Entry Point**: `src/universal_agent/main.py`
**MCP Server Tools**: `src/mcp_server.py`

---

## 📍 Current State (December 30, 2025)

### ✅ What's Working Well

| Feature | Status | Notes |
|---------|--------|-------|
| **Research & Report Generation** | ✅ Production-ready | JIT Delegation fixed via Knowledge Base |
| **Telegram Integration** | ✅ Working | Multi-user, async messaging support |
| **PDF/PPTX Creation** | ✅ Working | Skills-based, conditional routing |
| **Email Delivery (Gmail)** | ✅ Working | Attachments via `upload_to_composio` |
| **Memory System** | ✅ Working | Core blocks, archival search |
| **Logfire Tracing** | ✅ Working | Dual Trace (Main + Subprocess) |

### 🆕 Recent Additions (This Session)

1.  **JIT Delegation Guide Rail**:
    -   **Problem**: Agent summarizing snippets instead of delegating.
    -   **Solution**: `Knowledge Base Injection` (.claude/knowledge/report_workflow.md).
    -   **Result**: 100% reliable delegation to `report-creation-expert`.

2.  **Architecture Documentation Overhaul (v1.1)**:
    -   Updated `Project_Documentation/Architecture/` to reflect current state.
    -   Added docs for Telegram, JIT Guide Rails, and Sub-Agent Specialists.

3.  **Codebase Cleanup**:
    -   Removed dead code (failed JIT hooks, redundant startup logs).
    -   Merged `main-yolo` branches into `main`.

### Architectural Inspiration: LangSmith-Fetch

The Agent College design is inspired by [LangSmith-Fetch](https://github.com/langchain-ai/langsmith-fetch), which provides API access to LangSmith traces. We're adapting this pattern for Logfire:

| LangSmith-Fetch | Our LogfireFetch |
|-----------------|------------------|
| REST API to LangSmith | SQL queries via `LogfireQueryClient` |
| Push-based webhooks | Polling (TBD) or FastAPI endpoints |
| Trace analysis | Critic/Professor agents |

**Open Question**: Should we build a more complete FastAPI layer that mirrors LangSmith-Fetch's endpoints, or is polling sufficient?

---

## 🚧 Where We're Going Next

### Immediate Priority: Railway Deployment
We are ready to deploy the Universal Agent to **Railway**.

**Keys for Deployment**:
1.  **Plan**: Follow **[11_railway_deployment_plan.md](./Architecture/11_railway_deployment_plan.md)** (Created Dec 30).
2.  **Strategy**:
    *   **GitHub Integration**: Push-to-Deploy workflow.
    *   **Automation**: `bot/main.py` MUST be updated to self-register webhooks on startup.
    *   **Dependencies**: Hybrid approach (Cloud API for crawling, Docker `apt` packages for PDF/Video).
3.  **Persistence**: Ensure `AGENT_RUN_WORKSPACES` and `Memory_System_Data` are mounted as Volumes.

**Key Questions to Explore**:

| Topic | Question |
|-------|----------|
| **Agent College** | How to run the `LogfireFetch` service alongside the bot? (Plan: Monolith via script) |
| **Cost** | "Always-On" RAM reservation is required (Stateful architecture). |

**Next Dialogue Goals**:
1.  **Execute Plan**: Create `Dockerfile`, `.dockerignore`, and update `bot/main.py` code.
2.  **Deploy**: Connect GitHub to Railway and go live.

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

**Key Files**:
| File | Purpose |
|------|---------|
| `AgentCollege/logfire_fetch/main.py` | FastAPI service |
| `src/universal_agent/agent_college/critic.py` | Error analysis → Sandbox |
| `src/universal_agent/agent_college/professor.py` | Skill graduation |
| `src/universal_agent/agent_college/integration.py` | Boot-time hook |
| `Memory_System/manager.py` | Core memory management |

---

## 🔧 Running the System

### Main Agent
```bash
cd /home/kjdragan/lrepos/universal_agent
uv run src/universal_agent/main.py
```

### LogfireFetch Service (Agent College)
```bash
uv run uvicorn AgentCollege.logfire_fetch.main:app --port 8000
```

### Test Webhook
```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"trace_id": "test", "error": "example failure"}' \
  http://localhost:8000/webhook/alert
```

### Check Sandbox Contents
```bash
sqlite3 Memory_System_Data/agent_core.db \
  "SELECT value FROM core_blocks WHERE label='AGENT_COLLEGE_NOTES';"
```

---

## 📚 Key Documentation

| Priority | Document | Purpose |
|----------|----------|---------|
| 1 | `036_AGENT_COLLEGE_OPEN_QUESTIONS.md` | **READ FIRST** — Exploration agenda |
| 2 | `035_AGENT_COLLEGE_ARCHITECTURE.md` | Agent College overview |
| 3 | `034_LETTA_MEMORY_SYSTEM_MANUAL.md` | Memory System design |
| 4 | `010_LESSONS_LEARNED.md` | 39 lessons on patterns and gotchas |
| 5 | `.claude/skills/` | Skill definitions (pdf, pptx, etc.) |

---

## 🏗️ Project Structure

```
universal_agent/
├── src/
│   ├── universal_agent/
│   │   ├── main.py                 # Main agent
│   │   └── agent_college/          # Professor, Critic, Scribe 🆕
│   │       ├── integration.py
│   │       ├── professor.py
│   │       ├── critic.py
│   │       └── scribe.py
│   └── mcp_server.py               # Local MCP tools
├── AgentCollege/                    # 🆕
│   └── logfire_fetch/              # FastAPI service
│       ├── main.py
│       ├── logfire_reader.py
│       └── models.py
├── Memory_System/                   # Letta-style memory
│   ├── manager.py
│   └── storage.py
├── Memory_System_Data/              # Databases (gitignored)
│   └── agent_core.db
├── .claude/
│   ├── skills/                     # Skill definitions
│   └── knowledge/                  # Knowledge base
├── Project_Documentation/
│   ├── 000_CURRENT_CONTEXT.md      # This file
│   ├── 035_AGENT_COLLEGE_ARCHITECTURE.md
│   └── 036_AGENT_COLLEGE_OPEN_QUESTIONS.md 🆕
└── AGENT_RUN_WORKSPACES/           # Session artifacts
```

---

## ⚠️ Known Issues

1. **Agent College Notes not auto-surfaced** — User must manually query database or implement `/review-notes`
2. **Logfire Webhooks not configured** — Currently using polling/curl, not push from Logfire cloud
3. **Professor not triggered automatically** — Skill graduation requires manual invocation

---

## 🎯 Success Metrics

| Metric | Target | Status |
|--------|--------|--------|
| Research workflow | <10 min | ✅ ~8 min |
| PDF/PPTX generation | Working | ✅ |
| Memory persistence | Across sessions | ✅ |
| Agent College capture | Errors to sandbox | ✅ (manual) |
| Skill graduation | HITL workflow | ⏳ Not yet |

---

*Update this document whenever significant progress is made or context changes.*
