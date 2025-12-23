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
| `ZAI_API_KEY` | Z.AI endpoint (Anthropic API emulation) |
| `ZAI_BASE_URL` | `https://api.z.ai/v1/anthropic` |
| `ANTHROPIC_BASE_URL` | `https://api.z.ai/api/anthropic` |
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
    ├─→ Z.AI webReader MCP (web article extraction)
    └─→ Local Toolkit MCP (save_corpus, write_local_file, workbench_upload/download)
    ↓
Observer Pattern (async fire-and-forget artifact processing)
    ↓
AGENT_RUN_WORKSPACES/session_*/ (run.log, trace.json, search_results/, expanded_corpus.json, work_products/)
```

### Key Architectural Patterns

**1. Query Classification**: The agent classifies queries as SIMPLE (direct answer) vs COMPLEX (requires tools). Complex queries enter a tool loop.

**2. Observer Pattern**: Composio hooks (`@before_execute`, `@after_execute`) **do NOT work in MCP mode**. Instead, use async fire-and-forget observers:
```python
asyncio.create_task(observe_and_save_search_results(...))
asyncio.create_task(observe_and_enrich_corpus(...))
```

**3. Sub-Agent Delegation**: Complex tasks (report generation) are delegated to specialized sub-agents defined in `.claude/agents/`. The main agent uses the `Task` tool for delegation.

**4. Local-First Philosophy**: Keep reasoning/processing local; use Composio only for external actions. Download full data from Remote Workbench via `workbench_download` before processing.

## Key Files and Components

| File | Purpose |
|------|---------|
| `src/universal_agent/main.py` | Main agent: ClaudeSDKClient, observers, AgentDefinition, query classification |
| `src/mcp_server.py` | Custom MCP tools: save_corpus, write_local_file, workbench_upload/download |
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
├── expanded_corpus.json     # Full article extraction data
├── extracted_articles/      # Individual article JSON (optional)
└── work_products/           # Final outputs (reports, etc.)
    └── *.html
```

**Persistent Blacklist**: `AGENT_RUN_WORKSPACES/webReader_blacklist.json` tracks domains with persistent 404 failures.

## Critical Gotchas and Patterns

### MCP Mode Specifics

| Issue | Solution |
|-------|----------|
| Hooks don't fire in MCP mode | Use Observer Pattern with `asyncio.create_task()` |
| MCP content is string repr, not JSON | Use `ast.literal_eval()` to parse before JSON decoding |
| MULTI_EXECUTE_TOOL deeply nested | Path: `data.data.results[0].response.data.results.news_results` |
| Large data returns `data_preview` | Agent MUST `workbench_download` the full file; don't process preview directly |

### webReader (Z.AI MCP)

| Error Code | Size | Meaning | Action |
|------------|------|---------|--------|
| 1234 | 171 bytes | Network timeout | Retryable |
| 1214 | 90 bytes | Not found (404) | Permanent failure, add to blacklist |

**Optimization**: Always use `retain_images=false` to reduce response size.

### Composio Planner Behavior

The Composio Planner (`COMPOSIO_SEARCH_TOOLS` / `COMPOSIO_MULTI_EXECUTE_TOOL`) is a **sub-planner** for remote tools only. The Claude Agent SDK maintains full context as the "Master Brain" and will autonomously fill gaps (e.g., delegating to sub-agents for report generation after SERP completes).

## Sub-Agent Guidelines

The `report-creation-expert` sub-agent has mandatory hard stops:
- **10 successful extractions** → STOP immediately
- **2 batches completed** → STOP even if <10 successful
- **MAX 5 parallel webReader calls** per batch

Before writing any report, sub-agent **MUST** call `save_corpus` to save `expanded_corpus.json`.

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
