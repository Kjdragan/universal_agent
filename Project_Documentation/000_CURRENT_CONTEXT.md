# 000: Current Project Context

> [!IMPORTANT]
> **For New AI Agents**: Read this document first to understand the current state of the project.
> This is a living document that tracks where we are and where we're going.

**Last Updated**: 2025-12-27 08:25 CST

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

## 📍 Current State (December 27, 2025)

### ✅ What's Working Well

| Feature | Status | Notes |
|---------|--------|-------|
| **Research & Report Generation** | ✅ Production-ready | Full workflow tested and optimized |
| **PDF/PPTX Creation** | ✅ Working | Skills-based, conditional routing |
| **Email Delivery (Gmail)** | ✅ Working | Attachments via `upload_to_composio` |
| **Memory System** | ✅ Working | Core blocks, archival search |
| **Agent College (Basic)** | ✅ Working | LogfireFetch + Critic + Sandbox |
| **Logfire Tracing** | ✅ Working | Full observability |

### 🆕 Recent Additions (This Session)

1. **Agent College Implementation**:
   - `AgentCollege/logfire_fetch/` — FastAPI service for trace querying and webhooks
   - `src/universal_agent/agent_college/` — Professor, Critic, Scribe modules
   - `[AGENT_COLLEGE_NOTES]` — Sandbox memory block for unverified learnings
   - Integration with existing Memory System (shared SQLite database)

2. **LogfireFetch Service**:
   - `GET /traces/recent` — Query recent traces
   - `GET /failures` — Query error traces
   - `POST /webhook/alert` — Receive alerts → Critic → Sandbox

3. **Database Fix**:
   - Fixed split-brain issue where LogfireFetch wrote to wrong database
   - Now both `main.py` and `LogfireFetch` use `Memory_System_Data/agent_core.db`

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

### Immediate Priority: Agent College Refinement

A comprehensive exploration of Agent College design decisions is needed. See [036_AGENT_COLLEGE_OPEN_QUESTIONS.md](./036_AGENT_COLLEGE_OPEN_QUESTIONS.md) for the full agenda.

**Key Questions to Explore**:

| Topic | Question |
|-------|----------|
| **Polling vs Webhooks** | Implement background polling for automatic error capture |
| **Critic Thresholds** | What severity level triggers notes? |
| **HITL Triggers** | `/review-notes` command? Startup check? |
| **Staleness Detection** | How to mark issues as resolved? |
| **Scribe Filtering** | How to identify "noteworthy" successes? |
| **Professor Workflow** | When/how to graduate skills? |
| **Deployment** | Docker/always-on architecture? |

**Next Dialogue Goals**:
1. Implement polling-based error capture in LogfireFetch
2. Design filtering thresholds for Critic
3. Create `/review-notes` workflow
4. Explore Docker packaging for always-on operation

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
