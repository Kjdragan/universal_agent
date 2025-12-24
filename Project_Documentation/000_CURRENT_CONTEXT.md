# 000: Current Project Context

> [!IMPORTANT]
> **For New AI Agents**: Read this document first to understand the current state of the project.
> This is a living document that tracks where we are and where we're going.

**Last Updated**: 2025-12-23 13:40 CST

---

## 🎯 Project Overview

**Universal Agent** is a standalone agent using Claude Agent SDK with Composio Tool Router integration.

**Core Capabilities**:
- Claude Agent SDK for agentic workflows
- Composio Tool Router for 500+ tool integrations (Gmail, SERP, Slack, etc.)
- Crawl4AI parallel web extraction via local MCP server
- Sub-agent delegation for specialized tasks (report generation)
- Logfire tracing for observability
- Automatic workspace and artifact management
- Observer pattern for async result processing and error tracking

**Main Entry Point**: `src/universal_agent/main.py`
**MCP Server Tools**: `src/mcp_server.py`

---

## 📍 Current State (December 22, 2025)

### ✅ What's Working Well

| Feature | Status | Notes |
|---------|--------|-------|
| **Research & Report Generation** | ✅ Production-ready | Full workflow tested and optimized |
| **Sub-Agent Delegation** | ✅ Working | `report-creation-expert` handles extraction + synthesis |
| **Web Extraction (crawl_parallel)** | ✅ Working | Fast parallel extraction via crawl4ai |
| **Save Corpus** | ✅ Working | Custom MCP tool saves extracted articles to JSON |
| **Email Delivery** | ✅ Working | Gmail integration with HTML attachment support |
| **Error Code Tracking** | ✅ Working | 1234 (timeout), 1214 (not found) logged and handled |
| **Domain Blacklist** | ✅ Working | Tracks failing domains for future optimization |
| **Logfire Tracing** | ✅ Working | Full observability with deep links |

### Recent Optimizations (Dec 24, 2025)

1. **Eliminated Redundant Data Saving**:
   - Fixed issue where agent was manually saving search results to Remote Workbench
   - Now relies on Observer Pattern's auto-save to local `search_results/`
   - **Saved ~100s** per run by avoiding unnecessary remote operations

2. **Streamlined File Uploads (One-Step)**:
   - Switched from complex multi-step upload (workbench_upload + script) to single-step `upload_to_composio` tool
   - Handles local -> remote -> S3 attachment staging in one call
   - **Saved ~200s** per run by avoiding "file not found" retry loops

3. **crawl_parallel Performance**:
   - Parallel extraction of all URLs in a single call
   - Clean markdown output with noise removal
   - Saves directly to session `search_results/`

### Next Steps (Performance Engineering)

1. **Pre-warm MCP Connections**:
   - Investigate lazy initialization delays (~60s startup)
   - Explore "pre-priming" connections during agent boot

2. **Parallel Crawl Initialization**:
   - Investigate crawl4ai browser context startup time
   - Optimizing readiness for the first crawl batch

### Architecture

### Architecture

```
User Query → Claude SDK → MCP Servers (Composio + Local Toolkit)
                    ↓
            Query Classification (SIMPLE vs COMPLEX)
                    ↓
            [Complex] → Sub-Agent Delegation (report-creation-expert)
                    ↓
            Sub-Agent: crawl_parallel → read_local_file → write_local_file
                    ↓
            Observer Pattern → Error tracking, artifact saving
                    ↓
            Final Response → Optional Email Delivery
```

### Key Files to Review

| Priority | File | Purpose |
|----------|------|---------|
| 1 | `src/universal_agent/main.py` | Main agent, observers, AgentDefinition |
| 2 | `src/mcp_server.py` | Custom MCP tools (save_corpus, write_local_file, etc.) |
| 3 | `.claude/agents/report-creation-expert.md` | Sub-agent prompt with quality guidelines |
| 4 | `docs/010_LESSONS_LEARNED.md` | 21 lessons on gotchas and patterns |
| 5 | `docs/012_LOCAL_VS_WORKBENCH_ARCHITECTURE.md` | Local-first vs remote workbench |

---

## 🚀 Capability Expansion Testing (Completed Dec 22, 2025)

### Test Results

| # | Category | Query | Result | Tools Used |
|---|----------|-------|--------|------------|
| 1 | Code Gen | Password generator script | ✅ PASS | Bash, Read |
| 2 | File Read | Dependency summary | ✅ PASS | Glob, Read, write_local_file |
| 3 | Email | Gmail send test | ✅ PASS | GMAIL_SEND_EMAIL |
| 4 | Data Analysis | CSV + revenue calc | ✅ PASS | write_local_file (x2) |
| 5 | Multi-Step | Search → Extract → Summarize | ✅ PASS | COMPOSIO_SEARCH, crawl_parallel |
| 6 | Slack | Post to #general | 🔐 AUTH | Correctly surfaced auth link |

### Fix Applied: Work Products Auto-Save

**Issue**: Agent generated outputs (tables, summaries) but didn't save them to `work_products/`.

**Fix**: Added mandatory save-first guidance to `main.py` system prompt (lines 1073-1083):
- Agent now saves significant outputs BEFORE displaying
- Uses `mcp__local_toolkit__write_local_file` to `work_products/`

### Observations

1. **Claude native tools preferred** for local operations (Glob, Read, Bash)
2. **Composio tools used correctly** for external services (Gmail, Slack, SERP)
3. **crawl_parallel integration works** in multi-step workflows
4. **Auth handling is graceful** - surfaced Composio link when needed

---

### High-Volume Research Architecture (Scout/Expert Protocol)
- **Problem**: Context window limits prevented processing comprehensive search results (30+ URLs).
- **Solution**: "Scout/Expert" Protocol.
    - **Scout (Main Agent)**: Finds *location* of data (`search_results/`) and delegates. Forbidden from processing URLs.
    - **Expert (Sub-Agent)**: Uses `list_directory` to find all JSONs, extracts ALL URLs, and runs `crawl_parallel` (bulk scraping).
- **Status**: Verified with 27 concurrent URLs.
- **Documentation**: See `docs/014_SCOUT_EXPERT_PROTOCOL.md`.

### Universal File Staging (Cloud Upload)
- **Problem**: Cloud tools (Gmail, Slack, Code Interpreter) cannot access local files directly.
- **Solution**: Use `upload_to_composio` to "teleport" files to the cloud environment.
- **Workflow**:
    1.  **Stage**: Call `upload_to_composio(path="/abs/path/to/file")`.
        *   *Result*: Returns JSON with `s3_key` (for attachments) and `s3_url` (for links).
    2.  **Act**: Pass the `s3_key` to the destination tool.
        *   *Example (Gmail)*: `GMAIL_SEND_EMAIL(..., attachment={"s3key": "..."})`
        *   *Example (Slack)*: `SLACK_SEND_MESSAGE(..., attachments=[{"s3_key": "..."}])`

### SubagentStop Hook Implementation
- Replaced `TaskOutput` polling with event-driven `SubagentStop` hook
- Sub-agent completion now automatically triggers next-step guidance
- See Lesson 18 in `010_LESSONS_LEARNED.md`

### Toolkit Banning via Session Configuration
- Added `toolkits={"disable": ["firecrawl", "exa"]}` to `composio.create()`
- Prevents `COMPOSIO_SEARCH_TOOLS` from recommending external crawlers
- Forces use of local `mcp__local_toolkit__crawl_parallel`
- See Lesson 19 in `010_LESSONS_LEARNED.md`

### Sub-Agent Tool Inheritance
- Removed explicit `tools` field from `AgentDefinition`
- Sub-agents now inherit ALL parent tools including local MCP tools
- See Lesson 21 in `010_LESSONS_LEARNED.md`


---

## 🔧 Development Environment

### Running the Agent
```bash
cd /home/kjdragan/lrepos/universal_agent
uv sync
uv run src/universal_agent/main.py
```

### Required Environment Variables
Create `.env` from `.env.example`:
- `COMPOSIO_API_KEY` - Composio authentication
- `ZAI_API_KEY` - Z.AI endpoint (Anthropic API emulation)
- `ANTHROPIC_BASE_URL` - `https://api.z.ai/api/anthropic`
- `LOGFIRE_TOKEN` - Logfire tracing (optional)

### Key Dependencies
- `claude-agent-sdk` - Claude agentic framework
- `composio` - Tool router SDK
- `logfire` - Observability
- `prompt-toolkit` - Better terminal input
- `httpx` - HTTP client for MCP tools

---

## 🧠 Key Concepts

### 1. MCP Mode
We use Composio's MCP server for tool routing. Tools execute on Composio's cloud, not locally.

### 2. Observer Pattern
Since Composio hooks don't work in MCP mode, we observe tool results after they return:
```python
asyncio.create_task(observe_and_save_search_results(...))
asyncio.create_task(observe_and_enrich_corpus(...))
```

### 3. Sub-Agent Delegation
Complex tasks are delegated to specialized sub-agents:
- `report-creation-expert` - Full article extraction, corpus saving, report synthesis

### 4. Workspace Structure
Each session creates:
```
AGENT_RUN_WORKSPACES/session_YYYYMMDD_HHMMSS/
├── run.log              # Full console output
├── summary.txt          # Brief summary
├── trace.json           # Tool call/result trace
├── search_results/      # Cleaned SERP artifacts
├── extracted_articles/  # Individual article JSON (optional)
├── expanded_corpus.json # Full corpus from extraction
└── work_products/       # Reports, outputs
    └── *.html
```

### 5. Error Handling (crawl_parallel)
crawl_parallel handles errors internally and continues with successful URLs.
Failed URLs are logged but don't block the batch.

---

## 📁 Project Structure

```
universal_agent/
├── src/
│   ├── universal_agent/
│   │   ├── __init__.py
│   │   └── main.py              # Main agent implementation
│   └── mcp_server.py            # Custom MCP tools (save_corpus, etc.)
├── .claude/agents/
│   └── report-creation-expert.md  # Sub-agent prompt
├── docs/
│   ├── 000_CURRENT_CONTEXT.md   # This file (READ FIRST)
│   ├── 004_HOOKS_ARCHITECTURE.md
│   ├── 010_LESSONS_LEARNED.md   # 12 lessons on patterns and gotchas
│   └── 012_LOCAL_VS_WORKBENCH_ARCHITECTURE.md
├── AGENT_RUN_WORKSPACES/        # Runtime session artifacts (gitignored)
├── pyproject.toml               # Dependencies
├── .env                         # Environment variables (gitignored)
├── .env.example                 # Environment template
└── README.md
```

---

## ⚠️ Known Issues & Gotchas

1. **Some domains may timeout** - crawl_parallel handles gracefully
2. **Hooks don't fire in MCP mode** - Use Observer Pattern instead
3. **MULTI_EXECUTE structure is deeply nested** - See Lesson 3 in lessons learned
4. **MCP content is string repr, not JSON** - Use `ast.literal_eval` to parse

---

## 🎯 Success Metrics (Research Workflow)

| Metric | Target | Actual |
|--------|--------|--------|
| Report quality | Professional with citations | ✅ Excellent |
| Extraction success rate | >70% | ~80% |
| Total workflow time | <10 min | ~8-10 min |
| Email delivery | 100% | ✅ Working |

---

*Update this document whenever significant progress is made or context changes.*
