# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Universal Agent** is a standalone AI agent using Claude Agent SDK with Composio Tool Router integration. It enables complex agentic workflows combining LLM reasoning with 500+ external tool integrations (web search, Gmail, Slack, file operations, code execution, etc.).

**Main Entry Point**: `src/universal_agent/main.py` (~5,588 lines)
**Custom MCP Tools**: `src/mcp_server.py`

## Development Commands

```bash
# Install/refresh dependencies (UV package manager, NOT pip)
uv sync

# Add a new dependency
uv add <package>

# Run the CLI Agent + Agent College locally (RECOMMENDED)
./local_dev.sh

# Manual: Run CLI Agent only
PYTHONPATH=src uv run python -m universal_agent.main

# Manual: Run Telegram Bot
PYTHONPATH=src uv run uvicorn universal_agent.bot.main:app --port 8000

# Manual: Run Agent College sidecar only
PYTHONPATH=src uv run uvicorn AgentCollege.logfire_fetch.main:app --port 8001

# Run the local MCP server standalone
python src/mcp_server.py
```

## Required Environment Variables (.env)

| Variable | Purpose |
|----------|---------|
| `COMPOSIO_API_KEY` | Composio tool router authentication |
| `ANTHROPIC_API_KEY` | Anthropic auth token |
| `DEFAULT_USER_ID` | Composio user ID (format: `pg-test-xxx`) |
| `LOGFIRE_TOKEN` | Logfire distributed tracing (optional) |
| `MODEL_NAME` | `claude-sonnet-4-5-20250929` |
| `TELEGRAM_BOT_TOKEN` | Telegram bot authentication (for bot mode) |
| `WEBHOOK_URL` | Telegram webhook URL (for bot mode) |
| `ALLOWED_USER_IDS` | Comma-separated Telegram user IDs (for bot mode) |

## Architecture Overview

```
User Query
    ↓
Query Classification (SIMPLE vs COMPLEX) with _is_memory_intent() heuristic
    ↓
[Complex Path] → Sub-Agent Delegation (report-creation-expert, video-creation-expert, image-expert)
    ↓
Claude Agent SDK (Main Brain)
    ├─→ Composio MCP Server (500+ tools: SERP, Gmail, Slack, etc.)
    ├─→ Local Toolkit MCP (crawl_parallel, finalize_research, read_research_files, append_to_file)
    ├─→ External MCPs (edgartools, video_audio, youtube, zai_vision)
    ├─→ Skills (.claude/skills/: pdf, pptx, xlsx, docx, mcp-builder, skill-creator)
    └─→ Letta Memory System (persistent sub-agent memory)
    ↓
Observer Pattern (async fire-and-forget artifact processing)
    ↓
Durable System (SQLite state management, checkpointing, tool ledger)
    ↓
AGENT_RUN_WORKSPACES/session_*/ (run.log, trace.json, search_results/, work_products/)
```

### Key Architectural Patterns

**1. Query Classification**: The agent classifies queries as SIMPLE (direct answer) vs COMPLEX (requires tools) using `_is_memory_intent()` heuristic and LLM fallback.

**2. Observer Pattern**: Composio hooks (`@before_execute`, `@after_execute`) **do NOT work in Composio MCP mode**. Instead, use async fire-and-forget observers in `src/universal_agent/observers/`.

**3. Sub-Agent Delegation**: Complex tasks (report generation) are delegated to specialized sub-agents defined in `.claude/agents/`. The main agent uses the `Task` tool for delegation.

**4. Letta Memory Integration**: Sub-agents have persistent memory via Letta Learning SDK with memory blocks: human, system_rules, project_context, recent_queries, recent_reports.

**5. Durable State System**: SQLite-based state management with checkpointing, tool ledger, and policy-based tool execution via `src/universal_agent/durable/`.

**6. Hook System**: Event-driven hooks (SubagentStop, PreToolUse, PostToolUse, UserPromptSubmit) for injecting guidance and tracking tool calls.

**7. Local-First Philosophy**: Keep reasoning/processing local; use Composio only for external actions.

## Key Files and Components

| File | Purpose |
|------|---------|
| `src/universal_agent/main.py` | Main agent (~5588 lines): ClaudeSDKClient, observers, AgentDefinition, query classification, hooks |
| `src/mcp_server.py` | Custom MCP tools: crawl_parallel, finalize_research, read_research_files, append_to_file, generate_image |
| `src/tools/workbench_bridge.py` | Local-Remote file transfer bridge using Composio SDK |
| `src/universal_agent/observers/core.py` | 5 observer functions for async artifact processing |
| `src/universal_agent/durable/` | State management: db.py, state.py, ledger.py, checkpointing.py, tool_gateway.py |
| `src/universal_agent/bot/` | Modular Telegram Bot: main.py (FastAPI), agent_adapter.py, task_manager.py, telegram_handlers.py |
| `src/universal_agent/agent_college/` | Agent College: critic.py, professor.py, scribe.py, runner.py (self-improvement) |
| `src/universal_agent/agent_operator/` | CLI for agent operations: operator_cli.py, operator_db.py |
| `src/universal_agent/guardrails/` | Tool schema validation: tool_schema.py |
| `.claude/agents/report-creation-expert.md` | Sub-agent for comprehensive research + report synthesis |
| `.claude/skills/` | Claude Skills: pdf, docx, pptx, xlsx, skill-creator, mcp-builder, frontend-design, webapp-testing |
| `Project_Documentation/Architecture/` | **START HERE** - Architecture documentation (updated) |
| `Project_Documentation/000_CURRENT_CONTEXT.md` | Current project state |
| `Project_Documentation/010_LESSONS_LEARNED.md` | Lessons on patterns and gotchas |

## Session Workspace Structure

Each run creates `AGENT_RUN_WORKSPACES/session_YYYYMMDD_HHMMSS/`:
```
session_*/
├── .prompt_history          # Prompt toolkit history
├── run.log                  # Full console output
├── session_summary.txt      # Session metrics summary
├── trace.json               # Tool call/result trace (live-updated)
├── transcript.md            # Rich markdown transcript (live-updated)
├── downloads/               # Files downloaded via Composio
├── search_results/          # Cleaned SERP artifacts + crawled markdown
├── workbench_activity/      # Remote code execution logs
└── work_products/           # Final outputs (reports, PDFs, etc.)
    └── media/               # Video/audio outputs
```

## Critical Gotchas and Patterns

### MCP Mode Specifics

| Issue | Solution |
|-------|----------|
| Hooks don't fire in MCP mode | Use Observer Pattern with `asyncio.create_task()` |
| MCP content is string repr, not JSON | Use `ast.literal_eval()` to parse before JSON decoding |
| MULTI_EXECUTE_TOOL deeply nested | Path: `data.data.results[0].response.data.results.news_results` |
| Large data returns `data_preview` | Agent MUST `workbench_download` the full file |
| **Email Attachments** | **NEVER** use `workbench_upload` manually. Use `upload_to_composio` (1-step) -> `GMAIL_SEND_EMAIL` |

### Report Workflow (Updated)

The `report-creation-expert` now uses a NEW workflow:
1. **MANDATORY**: Call `mcp__local_toolkit__finalize_research` BEFORE reading any content
2. This creates `search_results/research_overview.md` and `search_results_filtered_best/`
3. Use `mcp__local_toolkit__read_research_files` to batch-read filtered crawl files
4. DO NOT read raw `search_results/crawl_*.md` files manually
5. DO NOT call `crawl_parallel` directly (finalize_research handles this)

### crawl4ai (Local Web Scraping)
`crawl_parallel` provides fast, parallel web extraction using crawl4ai.
Saves markdown directly to `search_results/`.

### MCP Server Configuration

**6 MCP Servers Configured**:
1. `composio` (HTTP) - 500+ SaaS integrations
2. `local_toolkit` (stdio) - Local file ops, web extraction
3. `edgartools` (stdio) - SEC Edgar research
4. `video_audio` (stdio) - FFmpeg video/audio editing
5. `youtube` (stdio) - yt-dlp downloads
6. `zai_vision` (stdio) - GLM-4.6V analysis

**Configuration Location**: `src/universal_agent/main.py` lines 4427-4470

### Observer Pattern Integration

**5 Observer Functions** (in `src/universal_agent/observers/core.py`):
- `observe_and_save_search_results()` - SERP artifact cleaning
- `observe_and_save_workbench_activity()` - Code execution logging
- `observe_and_save_work_products()` - Dual-save to persistent storage
- `observe_and_save_video_outputs()` - Media file tracking
- `verify_subagent_compliance()` - Quality assurance verification

**Integration**: Spawned via `asyncio.create_task()` in main.py lines 3807-3838

### Composio Planner Behavior

The Composio Planner (`COMPOSIO_SEARCH_TOOLS` / `COMPOSIO_MULTI_EXECUTE_TOOL`) is a **sub-planner** for remote tools only.

**Toolkit Bans**: Session is configured with `toolkits={"disable": ["firecrawl", "exa"]}` so `COMPOSIO_SEARCH_TOOLS` won't recommend external crawlers.

## Sub-Agent Guidelines

**✅ SubagentStop Hook Pattern**

When the main agent delegates to a sub-agent via `Task`, a `SubagentStop` hook automatically fires when the sub-agent completes:
1. The hook verifies artifacts were created in `work_products/`
2. Injects a system message with next steps (upload, email, update TodoWrite)
3. Main agent receives the message and continues workflow

**🔧 Tool Inheritance**

Sub-agents inherit tools from the parent. Per SDK docs:
- **Omit `tools` field** → inherits ALL tools including MCP tools
- **Specify `tools`** → only those tools are available

All current sub-agents omit `tools` to inherit ALL tools.

**🧠 Letta Memory Integration**

Sub-agents have persistent memory via Letta Learning SDK:
- Environment: `UA_LETTA_SUBAGENT_MEMORY=1` (default)
- Memory blocks: human, system_rules, project_context, recent_queries, recent_reports
- Agent naming: `universal_agent {subagent_type}` for typed agents

## Report Quality Standards

Reports must include:
- Specific numbers, dates, direct quotes (e.g., "70.7% on GDPval", not "performed well")
- Thematic synthesis (NOT source-by-source)
- Executive Summary with highlight box
- Table of Contents with anchor links
- Modern HTML with gradients, info boxes, stats cards

## Documentation Priority

When working with this codebase, read documentation in this order:
1. `Project_Documentation/Architecture/01_overall_system_architecture.md` - Complete system overview (UPDATED)
2. `Project_Documentation/Architecture/04_observer_pattern.md` - Observer pattern details (UPDATED)
3. `Project_Documentation/Architecture/03_subagent_delegation.md` - Sub-agent architecture (UPDATED)
4. `Project_Documentation/Architecture/05_mcp_servers.md` - MCP server configuration (UPDATED)
5. `Project_Documentation/000_CURRENT_CONTEXT.md` - Project state, what's working, next steps
6. `Project_Documentation/010_LESSONS_LEARNED.md` - Lessons on Composio SDK patterns, gotchas

## Package Manager

**Always use UV, never pip**:
```bash
uv sync           # Install dependencies
uv add <package>  # Add new dependency
uv run <command>  # Run in UV environment
```

## Python Version

Python 3.12+ (specified in `pyproject.toml`)

## Global Context Notes

From the user's global CLAUDE.md:
- **Primary Path Philosophy**: Always work on primary path; fallbacks are considered failures and should be clearly indicated
- **OpenAI Responses API**: For OpenAI LLMs, use Responses API (not in training data)

