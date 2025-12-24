# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Universal Agent** is a standalone AI agent using Claude Agent SDK with Composio Tool Router integration. It enables complex agentic workflows combining LLM reasoning with 500+ external tool integrations (web search, Gmail, Slack, file operations, code execution, etc.).

**Main Entry Point**: `src/universal_agent/main.py` (~1400 lines)
**Custom MCP Tools**: `src/mcp_server.py`

## Development Commands

```bash
# Install/refresh dependencies (UV package manager, NOT pip)
uv sync

# Add a new dependency
uv add <package>

# Run the main agent
uv run src/universal_agent/main.py

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
| `MODEL_NAME` | `claude-sonnet-4-5-20250514` |

## Architecture Overview

```
User Query
    ↓
Query Classification (SIMPLE vs COMPLEX)
    ↓
[Complex Path] → Sub-Agent Delegation (report-creation-expert)
    ↓
Claude Agent SDK (Main Brain)
    ├─→ Composio MCP Server (500+ tools: SERP, Gmail, Slack, etc.)
    ├─→ Local Toolkit MCP (crawl_parallel, write_local_file, upload_to_composio)
    ↓
Observer Pattern (async fire-and-forget artifact processing)
    ↓
AGENT_RUN_WORKSPACES/session_*/ (run.log, trace.json, search_results/, work_products/)
```

### Key Architectural Patterns

**1. Query Classification**: The agent classifies queries as SIMPLE (direct answer) vs COMPLEX (requires tools). Complex queries enter a tool loop.

**2. Observer Pattern**: Composio hooks (`@before_execute`, `@after_execute`) **do NOT work in Composio MCP mode**. Instead, use async fire-and-forget observers.
   - *Feature*: Observer prevents redundant manual saves by warning agent if data is already persisted locally.

**3. Sub-Agent Delegation**: Complex tasks (report generation) are delegated to specialized sub-agents defined in `.claude/agents/`. The main agent uses the `Task` tool for delegation.

**4. Local-First Philosophy**: Keep reasoning/processing local; use Composio only for external actions. Download full data from Remote Workbench via `workbench_download` before processing.

## Key Files and Components

| File | Purpose |
|------|---------|
| `src/universal_agent/main.py` | Main agent: ClaudeSDKClient, observers, AgentDefinition, query classification |
| `src/mcp_server.py` | Custom MCP tools: crawl_parallel, write_local_file, upload_to_composio |
| `src/tools/workbench_bridge.py` | Local-Remote file transfer bridge using Composio SDK |
| `.claude/agents/report-creation-expert.md` | Sub-agent for comprehensive research + report synthesis |
| `Project_Documentation/000_CURRENT_CONTEXT.md` | **START HERE** - Current project state |
| `Project_Documentation/010_LESSONS_LEARNED.md` | 17 lessons on patterns and gotchas |

## Session Workspace Structure

Each run creates `AGENT_RUN_WORKSPACES/session_YYYYMMDD_HHMMSS/`:
```
session_*/
├── run.log                  # Full console output
├── summary.txt              # Brief summary
├── trace.json               # Tool call/result trace
├── search_results/          # Cleaned SERP artifacts (*.json)
└── work_products/           # Final outputs (reports, etc.)
    └── *.html
```

## Critical Gotchas and Patterns

### MCP Mode Specifics

| Issue | Solution |
|-------|----------|
| Hooks don't fire in MCP mode | Use Observer Pattern with `asyncio.create_task()` |
| MCP content is string repr, not JSON | Use `ast.literal_eval()` to parse before JSON decoding |
| MULTI_EXECUTE_TOOL deeply nested | Path: `data.data.results[0].response.data.results.news_results` |
| Large data returns `data_preview` | Agent MUST `workbench_download` the full file; don't process preview directly |
| **Email Attachments** | **NEVER** use `workbench_upload` manually. Use `upload_to_composio` (1-step) -> `GMAIL_SEND_EMAIL` |

### crawl4ai (Local Web Scraping)
`crawl_parallel` provides fast, parallel web extraction using crawl4ai.
Saves markdown directly to `search_results/`.

### Composio Planner Behavior

The Composio Planner (`COMPOSIO_SEARCH_TOOLS` / `COMPOSIO_MULTI_EXECUTE_TOOL`) is a **sub-planner** for remote tools only. The Claude Agent SDK maintains full context as the "Master Brain" and will autonomously fill gaps (e.g., delegating to sub-agents for report generation after SERP completes).

**Toolkit Bans**: Session is configured with `toolkits={"disable": ["firecrawl", "exa"]}` so `COMPOSIO_SEARCH_TOOLS` won't recommend external crawlers—use our `mcp__local_toolkit__crawl_parallel` instead.

## Sub-Agent Guidelines

**✅ SubagentStop Hook Pattern**

When the main agent delegates to a sub-agent via `Task`, a `SubagentStop` hook automatically fires when the sub-agent completes:
1. The hook verifies artifacts were created in `work_products/`
2. Injects a system message with next steps (upload, email, update TodoWrite)
3. Main agent receives the message and continues workflow

This is event-driven—no polling required.

**🔧 Tool Inheritance**

Sub-agents inherit tools from the parent. Per SDK docs:
- **Omit `tools` field** → inherits ALL tools including MCP tools
- **Specify `tools`** → only those tools are available (use simple names like `"Read"`, `"Bash"`)

Our `report-creation-expert` sub-agent omits `tools` to inherit `mcp__local_toolkit__*` tools.

The `report-creation-expert` sub-agent:
- **10 successful extractions** → STOP immediately
- **Use `crawl_parallel`** for all URLs in a single call
- **Report saved to** `work_products/*.html`

## Report Quality Standards

Reports must include:
- Specific numbers, dates, direct quotes (e.g., "70.7% on GDPval", not "performed well")
- Thematic synthesis (NOT source-by-source)
- Executive Summary with highlight box
- Table of Contents with anchor links
- Modern HTML with gradients, info boxes, stats cards

## Documentation Priority

When working on this codebase, read documentation in this order:
1. `Project_Documentation/000_CURRENT_CONTEXT.md` - Project state, what's working, next steps
2. `Project_Documentation/010_LESSONS_LEARNED.md` - 17 lessons on Composio SDK patterns, gotchas
3. `Project_Documentation/004_HOOKS_ARCHITECTURE.md` - Hooks vs Observer pattern design
4. `Project_Documentation/012_LOCAL_VS_WORKBENCH_ARCHITECTURE.md` - Local-first vs remote workbench

## Package Manager

**Always use UV, never pip**:
```bash
uv sync           # Install dependencies
uv add <package>  # Add new dependency
uv run <command>  # Run in UV environment
```

## Python Version

Python 3.13+ (specified in `.python-version`)

## Global Context Notes

From the user's global CLAUDE.md:
- **Primary Path Philosophy**: Always work on primary path; fallbacks are considered failures and should be clearly indicated
- **OpenAI Responses API**: For OpenAI LLMs, use Responses API (not in training data)
- **RUBE Agent-First Discovery**: If working with Rube Framework, always let agents discover tools dynamically via `RUBE_SEARCH_TOOLS`, never hardcode tool slugs
