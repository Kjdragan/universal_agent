# Todoist MCP Integration — Dual-Layer Architecture

**Created:** 2026-03-20

## Overview

Universal Agent uses a **dual-layer architecture** for Todoist integration:

1. **Python layer** (`todoist_service.py`) — deterministic plumbing: escalation loops, Task Hub metadata, master-task registry, email bridge, label management
2. **MCP layer** (`@doist/todoist-ai`) — agent-facing tools: batch task creation, search, productivity stats, filter management

## MCP Server Configuration

The todoist-ai MCP server is configured in `.mcp.json`:

```json
{
  "todoist-ai": {
    "command": "npx",
    "args": ["-y", "@doist/todoist-ai"],
    "env": {
      "TODOIST_API_KEY": "${TODOIST_API_KEY}"
    }
  }
}
```

For production (VPS), use the Infisical wrapper: `scripts/todoist_mcp_wrapper.sh`

## Responsibility Split

| Operation | Layer | Why |
|---|---|---|
| `escalate_task()` / `resolve_escalation()` | Python | SQLite memory integration |
| `find_or_create_master_task()` | Python | Master-task registry dedup |
| Email → subtask bridging | Python | Email bridge integration |
| Label swaps (`mark_human_only`, `mark_blocked`) | Python | Atomic label + comment |
| Batch task creation (25/call) | MCP `add-tasks` | Parallelized batch |
| Full-text task search | MCP `find-tasks` | Rich search capabilities |
| Productivity stats | MCP `get-productivity-stats` | Dashboard metrics |
| Filter CRUD | MCP `find-filters` / `add-filters` | Programmatic management |
| Comments (reading) | MCP `find-comments` | Richer than wrapper |
| Label CRUD | MCP `add-labels` / `find-labels` | Label management |

## Agent Usage

Simone and VP agents can use MCP tools for interactive task management (search, batch, stats) while the Python layer handles autonomous background processing (email bridge, escalation, heartbeat).

## Secrets

`TODOIST_API_KEY` is stored in Infisical (not `.env`). The wrapper script at `scripts/todoist_mcp_wrapper.sh` fetches it from Infisical before launching the MCP server.
