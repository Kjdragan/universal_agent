# Workspace Data Flow Architecture

**Document Version**: 2.0
**Last Updated**: 2026-01-06
**Status**: ACTIVE
**Related Files:**
- `src/universal_agent/durable/` (Persistence layer)
- `src/universal_agent/observers/` (Async data handlers)

---

## Overview

The Workspace Data Flow defines how data moves between the ephemeral execution runtime, the session-scoped filesystem, and durable long-term storage.

## Directory Structure

Every agent run creates a unique workspace:
`AGENT_RUN_WORKSPACES/session_<timestamp>_<uuid>/`

| Directory | Purpose | Access Pattern |
|-----------|---------|----------------|
| `search_results/` | Raw web content (JSON/MD) | **Write**: `crawl_parallel` <br> **Read**: `read_research_files` |
| `work_products/` | Final deliverables | **Write**: `Write` tool <br> **Read**: User/Observers |
| `workbench_activity/` | Code execution logs | **Write**: Observer <br> **Read**: Debugging |
| `media/` | Video/Audio outputs | **Write**: `youtube`/`video_audio` tool <br> **Read**: User |

## Data Flows

### 1. Ingestion Flow (Search)
```
Web Source 
  → crawl_parallel() 
  → AGENT_RUN_WORKSPACES/.../search_results/*.json
  → internal_monologue (Agent context)
```

### 2. Production Flow (Writing)
```
Agent Context 
  → Write(path=".../work_products/report.md") 
  → Filesystem 
  → [Async Observer] 
  → SAVED_REPORTS/report.md (Persistent Backup)
```

### 3. Execution Flow (Code)
```
Agent Context 
  → COMPOSIO_REMOTE_WORKBENCH 
  → Remote Docker Container 
  → [Async Observer] 
  → AGENT_RUN_WORKSPACES/.../workbench_activity/log.txt
```

## Tool-to-Storage Mapping

| Tool | Source | Destination | Persistence Mechanism |
|------|--------|-------------|-----------------------|
| `crawl_parallel` | Web | `search_results/` | File write (MCP) |
| `Write` | Agent | `work_products/` | Native tool |
| `youtube` | Web | `media/` | File write (MCP) |
| `video_audio` | File | `media/` | File write (MCP) |

## Durable State Database

Separate from the filesystem, the `agent_core.db` SQLite database tracks:

- **Run Metadata**: ID, start time, user ID
- **Trace**: Full execution history (Logfire span links)
- **Tool Ledger**: Audit log of every tool call (`tools` table)
- **Checkpoints**: Serialized state snapshots for resume

## Lifecycle

1. **Start**: `session_` directory created.
2. **Run**: Files accumulating in subdirectories. DB records steps.
3. **Stop**: Directory remains for inspection.
4. **Cleanup**: Background cron (future) or manual archival.
