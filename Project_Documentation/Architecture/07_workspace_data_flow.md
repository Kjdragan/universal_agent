# Workspace Structure and Data Flow

This document describes the session workspace architecture, directory structure, data flow patterns, and artifact lifecycle for the Universal Agent.

---

## Table of Contents

1. [Overview](#overview)
2. [Directory Structure](#directory-structure)
3. [Session Lifecycle](#session-lifecycle)
4. [Data Flow Diagrams](#data-flow-diagrams)
5. [Observer Pattern Architecture](#observer-pattern-architecture)
6. [Artifact Specifications](#artifact-specifications)
7. [Local vs Remote Data Flow](#local-vs-remote-data-flow)

---

## Overview

The Universal Agent uses a **session-based workspace architecture** where each agent execution creates an isolated workspace directory. This provides:

- **Temporal isolation** - Each session has its own artifact storage
- **Auditability** - Complete trace of all operations, tool calls, and outputs
- **Reproducibility** - Full preservation of intermediate data
- **Observability** - Rich tracing via Logfire with local artifact persistence

### Workspace Root

```
AGENT_RUN_WORKSPACES/
```

The base workspace directory contains:
- **Session workspaces** (`session_YYYYMMDD_HHMMSS/`) - Per-execution isolated environments
- **Persistent artifacts** (`webReader_blacklist.json`) - Cross-session domain failure tracking

---

## Directory Structure

### Complete Session Workspace

```mermaid
graph TD
    subgraph AGENT_RUN_WORKSPACES["AGENT_RUN_WORKSPACES/"]
        subgraph SESSION["session_20251222_143052/"]
            RUN_LOG["run.log<br/>Full console output<br/>(DualWriter: stdout/stderr)"]
            SUMMARY["summary.txt<br/>Brief execution summary"]
            TRACE["trace.json<br/>Complete tool call trace"]

            subgraph SEARCH["search_results/"]
                SERP1["COMPOSIO_SEARCH_NEWS_<timestamp>.json<br/>Cleaned SERP artifacts"]
                SERP2["RUBE_MULTI_EXECUTE_TOOL_<timestamp>.json<br/>Multi-execute results"]
            end

            CORPUS["expanded_corpus.json<br/>Full article extraction corpus<br/>(saved by sub-agent)"]

            subgraph ARTICLES["extracted_articles/"]
                ART1["<domain>_<timestamp>.json<br/>Individual article JSON"]
                ART2["<domain>_<timestamp>.json<br/>Individual article JSON"]
            end

            subgraph WORKBENCH["workbench_activity/"]
                WB1["workbench_<timestamp>.json<br/>Remote execution logs"]
                WB2["workbench_<timestamp>.json<br/>Code execution I/O"]
            end

            subgraph PRODUCTS["work_products/"]
                WP1["<topic>_<month>_<year>.html<br/>Final report HTML"]
                WP2["<topic>_<month>_<year>.html<br/>Analysis output"]
            end

            subgraph DOWNLOADS["downloads/"]
                DL1["<filename> - Auto-downloaded<br/>from Composio tools"]
            end
        end

        PERSISTENT["webReader_blacklist.json<br/>Cross-session domain<br/>failure tracking"]
    end
```

### File Purpose Reference

| File/Directory | Purpose | Written By | Format |
|----------------|---------|------------|--------|
| `run.log` | Complete console output with timestamps | `DualWriter` (main.py:23-46) | Text |
| `summary.txt` | Quick execution metrics summary | `main()` (main.py:1344-1347) | Text |
| `trace.json` | Full tool call trace with timing | `run_conversation()` (main.py:1149-1164) | JSON |
| `search_results/*.json` | Cleaned SERP artifacts | `observe_and_save_search_results()` (main.py:218-414) | JSON |
| `expanded_corpus.json` | Article extraction corpus | `save_corpus()` tool (mcp_server.py:70-135) | JSON |
| `extracted_articles/*.json` | Individual article records | `observe_and_enrich_corpus()` (main.py:479-627) | JSON |
| `workbench_activity/*.json` | Remote execution logs | `observe_and_save_workbench_activity()` (main.py:416-476) | JSON |
| `work_products/*` | Final outputs (reports, etc) | Sub-agents via `write_local_file()` | Various |
| `downloads/*` | Composio auto-downloaded files | Composio SDK | Binary |
| `webReader_blacklist.json` | Domain failure tracking (persistent) | `_update_domain_blacklist()` (main.py:167-205) | JSON |

---

## Session Lifecycle

### Session Initialization

```mermaid
sequenceDiagram
    participant Main as main()
    participant FS as File System
    participant Comp as Composio
    participant Log as Logfire

    Main->>FS: Create workspace directory<br/>AGENT_RUN_WORKSPACES/session_YYYYMMDD_HHMMSS/
    Main->>FS: Create downloads/ subdirectory
    Main->>Comp: Initialize with file_download_dir
    Main->>FS: Open run.log for writing
    Main->>Main: Install DualWriter on stdout/stderr
    Main->>Main: Set OBSERVER_WORKSPACE_DIR global
    Main->>Log: Create "standalone_composio_test" span
    Main->>Main: Extract OpenTelemetry trace_id
    Main->>Main: Initialize trace dict
```

**Code Reference**: `main.py:1006-1169`

### Session Completion

```mermaid
sequenceDiagram
    participant Main as main()
    participant FS as File System
    participant Log as Logfire

    Main->>Main: Calculate total_duration_seconds
    Main->>Log: Log "session_complete" metrics
    Main->>FS: Write trace.json
    Main->>FS: Write summary.txt
    Main->>Main: Display execution summary
    Main->>Main: Log Logfire trace URL
```

**Code Reference**: `main.py:1276-1357`

---

## Data Flow Diagrams

### 1. User Query to Artifacts Flow

```mermaid
flowchart TD
    subgraph Input["User Input"]
        QUERY["User Query"]
    end

    subgraph Agent["Claude Agent SDK"]
        CLIENT["ClaudeSDKClient"]
        CLASSIFY["classify_query()"]
        SIMPLE["handle_simple_query()<br/>Fast Path"]
        COMPLEX["run_conversation()<br/>Tool Loop"]
    end

    subgraph Tools["MCP Tools"]
        COMPOSIO["Composio MCP<br/>(SEARCH, EMAIL, etc)"]
        LOCAL["local_toolkit MCP<br/>(save, read, write)"]
        READER["web_reader MCP<br/>(article extraction)"]
        WORKBENCH["COMPOSIO_REMOTE_WORKBENCH<br/>(code execution)"]
    end

    subgraph Observers["Observer Pattern"]
        SERP_OBS["observe_and_save_search_results()"]
        CORP_OBS["observe_and_enrich_corpus()"]
        WB_OBS["observe_and_save_workbench_activity()"]
    end

    subgraph Workspace["Session Workspace"]
        SR["search_results/*.json"]
        EA["extracted_articles/*.json"]
        CORP["expanded_corpus.json"]
        WA["workbench_activity/*.json"]
        WP["work_products/*.html"]
        TRACE["trace.json"]
        LOG["run.log"]
    end

    QUERY --> CLASSIFY
    CLASSIFY -->|SIMPLE| SIMPLE
    CLASSIFY -->|COMPLEX| COMPLEX

    COMPLEX --> COMPOSIO
    COMPLEX --> LOCAL
    COMPLEX --> READER
    COMPLEX --> WORKBENCH

    COMPOSIO --> SERP_OBS
    READER --> CORP_OBS
    WORKBENCH --> WB_OBS

    SERP_OBS --> SR
    CORP_OBS --> EA
    CORP_OBS --> CORP
    WB_OBS --> WA

    LOCAL --> WP
    COMPLEX --> TRACE
    all --> LOG
```

### 2. Observer Pattern Data Writes

```mermaid
flowchart LR
    subgraph ToolExecution["Tool Execution"]
        TC["ToolUseBlock<br/>(name, id, input)"]
        TR["ToolResultBlock<br/>(tool_use_id, content)"]
    end

    subgraph AsyncObservers["Fire-and-Forget Async Tasks"]
        SERP["observe_and_save_search_results()"]
        ART["observe_and_enrich_corpus()"]
        WB["observe_and_save_workbench_activity()"]
        COMP["verify_subagent_compliance()"]
    end

    subgraph Outputs["Workspace Outputs"]
        SEARCH["search_results/<slug>_<timestamp>.json"]
        ARTICLE["extracted_articles/<domain>_<timestamp>.json"]
        WBA["workbench_activity/workbench_<timestamp>.json"]
        ERROR["Compliance error message<br/>(injected if missing corpus)"]
    end

    TC -->|"Record in trace"| TRACE["trace.json"]
    TR -->|"Record in trace"| TRACE
    TR -->|"Tool name lookup"| TOOL_LOOKUP["Find tool name from tool_use_id"]
    TOOL_LOOKUP -->|"Matches SERP keywords"| SERP
    TOOL_LOOKUP -->|"webReader tool"| ART
    TOOL_LOOKUP -->|"REMOTE_WORKBENCH tool"| WB
    TOOL_LOOKUP -->|"Task tool"| COMP

    SERP --> SEARCH
    ART --> ARTICLE
    WB --> WBA
    COMP -->|"if corpus missing"| ERROR
```

**Code Reference**: `main.py:857-903` (observer dispatch in `run_conversation`)

### 3. Local vs Remote Data Flow

```mermaid
flowchart TB
    subgraph Local["Local Environment"]
        AGENT["Claude Agent<br/>(Primary Process)"]
        LOCAL_FS["Local Filesystem<br/>/home/kjdragan/..."]
        WORKSPACE["Session Workspace<br/>AGENT_RUN_WORKSPACES/"]
        MCP_LOCAL["local_toolkit MCP Server<br/>(stdio)"]
    end

    subgraph Remote["Remote Composio Workbench"]
        WORKBENCH_S["Sandbox<br/>(/home/user/...)"]
        CODEINTERPRETER["COMPOSIO_REMOTE_WORKBENCH<br/>Code Execution"]
    end

    subgraph Bridge["WorkbenchBridge"]
        UPLOAD["upload()<br/>local -> remote"]
        DOWNLOAD["download()<br/>remote -> local"]
    end

    subgraph Tools["MCP Tools"]
        MCP_COMPOSIO["Composio MCP Server<br/>(http)"]
    end

    AGENT <-->|"stdio"| MCP_LOCAL
    AGENT <-->|"http"| MCP_COMPOSIO

    MCP_LOCAL <-->|"read/write"| LOCAL_FS
    MCP_LOCAL <-->|"read/write"| WORKSPACE

    MCP_COMPOSIO <-->|"http"| CODEINTERPRETER
    CODEINTERPRETER <-->|"execute"| WORKBENCH_S

    AGENT <-->|"workbench_upload"| UPLOAD
    AGENT <-->|"workbench_download"| DOWNLOAD

    UPLOAD <-->|"Python script"| CODEINTERPRETER
    DOWNLOAD <-->|"CODEINTERPRETER_GET_FILE_CMD"| CODEINTERPER

    UPLOAD <--> LOCAL_FS
    DOWNLOAD <--> LOCAL_FS
```

**Key Principle**: **LOCAL-FIRST** data flow. Only use remote workbench for:
- External action execution (APIs, browsing)
- Heavy operations requiring specific binaries
- Untrusted code execution

---

## Observer Pattern Architecture

### Design Rationale

The Observer Pattern enables **non-blocking artifact preservation** while the agent continues execution. Since Composio hooks (`@after_execute`) don't fire in MCP mode (execution happens on remote server), observers process results **after** they return to the client.

### Observer Functions

```mermaid
classDiagram
    class ObserverFunction {
        <<abstract>>
        +tool_name: str
        +content: Any
        +workspace_dir: str
        +check_tool() bool
        +extract_data() dict
        +save_artifact() path
    }

    class SERPObserver {
        +observe_and_save_search_results()
        +is_serp_tool() bool
        +parse_multi_execute()
        +clean_news_results()
        +clean_web_results()
    }

    class CorpusObserver {
        +observe_and_enrich_corpus()
        +handle_mcp_errors()
        +track_domain_blacklist()
        +enrich_search_results()
    }

    class WorkbenchObserver {
        +observe_and_save_workbench_activity()
        +parse_execution_output()
        +log_code_io()
    }

    class ComplianceChecker {
        +verify_subagent_compliance()
        +check_corpus_exists()
        +inject_error_message()
    }

    ObserverFunction <|-- SERPObserver
    ObserverFunction <|-- CorpusObserver
    ObserverFunction <|-- WorkbenchObserver
    ObserverFunction <|-- ComplianceChecker
```

### SERP Result Processing

```mermaid
flowchart TD
    INPUT["Tool Result Content"]

    subgraph Extraction["Extract JSON from Typed Content"]
        CHECK["Check content type"]
        TEXTBLOCK["Iterate TextBlock objects"]
        DICT["Check dict type"]
        RAW["Extract raw JSON text"]
    end

    subgraph Parsing["Parse JSON Structure"]
        PARSE["json.loads()"]
        UNWRAP_DATA["Unwrap 'data' wrapper"]
        CHECK_MULTI["Check for MULTI_EXECUTE"]
    end

    subgraph Processing["Process Payloads"]
        PAYLOADS["Prepare list of (slug, data)"]
        MULTI_RESULTS["Extract from results[]"]
        SINGLE["Single tool result"]
    end

    subgraph Cleaning["Clean and Normalize"]
        NEWS["Clean news_results"]
        WEB["Clean organic_results"]
        NORMALIZE["Normalize fields:<br/>position, title, url, snippet, source"]
    end

    subgraph Saving["Save Artifacts"]
        MKDIR["Create search_results/ dir"]
        TIMESTAMP["Generate unique filename"]
        WRITE["Write JSON file"]
        VERIFY["Verify file creation"]
    end

    INPUT --> CHECK
    CHECK -->|TextBlock list| TEXTBLOCK
    CHECK -->|dict| DICT
    CHECK -->|str| RAW
    TEXTBLOCK --> RAW
    DICT --> RAW
    RAW --> PARSE
    PARSE --> UNWRAP_DATA
    UNWRAP_DATA --> CHECK_MULTI
    CHECK_MULTI -->|Multi-execute| MULTI_RESULTS
    CHECK_MULTI -->|Single| SINGLE
    MULTI_RESULTS --> PAYLOADS
    SINGLE --> PAYLOADS
    PAYLOADS --> NEWS
    PAYLOADS --> WEB
    NEWS --> NORMALIZE
    WEB --> NORMALIZE
    NORMALIZE --> MKDIR
    MKDIR --> TIMESTAMP
    TIMESTAMP --> WRITE
    WRITE --> VERIFY
```

**Code Reference**: `main.py:218-414`

### Domain Blacklist Tracking

```mermaid
stateDiagram-v2
    [*] --> CheckError: webReader MCP error

    CheckError --> Error1234: "code":"1234"<br/>(Network timeout)
    CheckError --> Error1214: "code":"1214"<br/>(404 Not found)
    CheckError --> LogDebug: Other errors

    Error1234 --> LogWarning: Log as retryable
    Error1214 --> ExtractDomain: Parse URL for domain

    ExtractDomain --> LoadBlacklist: Read existing file
    LoadBlacklist --> UpdateCount: Increment failure count
    UpdateCount --> CheckThreshold

    CheckThreshold --> LogWarning: failures >= 3
    CheckThreshold --> SaveFile: failures < 3

    LogWarning --> SaveFile: Always save updated count
    SaveFile --> [*]

    LogDebug --> [*]
    LogWarning --> [*]
```

**Code Reference**: `main.py:167-205`

---

## Artifact Specifications

### trace.json Structure

```json
{
  "session_info": {
    "url": "https://mcp.composio.dev/...",
    "user_id": "user_123",
    "timestamp": "2025-12-22T14:30:52.123456"
  },
  "trace_id": "0123456789abcdef0123456789abcdef",
  "query": "User's original query text",
  "start_time": "2025-12-22T14:30:52.123456",
  "end_time": "2025-12-22T14:35:23.654321",
  "total_duration_seconds": 271.531,
  "tool_calls": [
    {
      "iteration": 1,
      "name": "mcp__composio__COMPOSIO_SEARCH_NEWS",
      "id": "toolu_01ABC...",
      "time_offset_seconds": 2.345,
      "input": { "query": "example", "num_results": 10 },
      "input_size_bytes": 45
    }
  ],
  "tool_results": [
    {
      "tool_use_id": "toolu_01ABC...",
      "time_offset_seconds": 3.789,
      "is_error": false,
      "content_size_bytes": 15234,
      "content_preview": "First 1000 chars..."
    }
  ],
  "iterations": [
    {
      "iteration": 1,
      "query": "First 200 chars...",
      "duration_seconds": 45.234,
      "tool_calls": 3,
      "needs_user_input": false,
      "auth_link": null
    }
  ],
  "logfire_enabled": true
}
```

### search_results/*.json Structure (News)

```json
{
  "type": "news",
  "timestamp": "2025-12-22T14:31:15.123456",
  "tool": "COMPOSIO_SEARCH_NEWS",
  "articles": [
    {
      "position": 1,
      "title": "Article Title",
      "url": "https://example.com/article",
      "source": "Example News",
      "date": "2025-12-22",
      "snippet": "Article snippet text..."
    }
  ]
}
```

### search_results/*.json Structure (Web)

```json
{
  "type": "web",
  "timestamp": "2025-12-22T14:31:15.123456",
  "tool": "COMPOSIO_SEARCH_WEB",
  "results": [
    {
      "position": 1,
      "title": "Page Title",
      "url": "https://example.com/page",
      "snippet": "Page description..."
    }
  ]
}
```

### expanded_corpus.json Structure

```json
{
  "extraction_timestamp": "2025-12-22T14:35:00.000000Z",
  "total_articles": 25,
  "successful": 22,
  "failed": 3,
  "articles": [
    {
      "url": "https://example.com/article",
      "title": "Article Title",
      "content": "Full markdown content from webReader...",
      "status": "success"
    }
  ]
}
```

### extracted_articles/*.json Structure

```json
{
  "timestamp": "2025-12-22T14:32:10.123456",
  "source_url": "https://example.com/article",
  "title": "Article Title",
  "description": "Meta description...",
  "content": "Truncated content (first 10k chars)...",
  "extraction_success": true
}
```

### workbench_activity/*.json Structure

```json
{
  "timestamp": "2025-12-22T14:33:20.123456",
  "tool": "COMPOSIO_REMOTE_WORKBENCH",
  "input": {
    "code": "import os\nprint('hello')...",
    "session_id": "sess_abc123",
    "current_step": "Step 1",
    "thought": "Running analysis script..."
  },
  "output": {
    "stdout": "hello\n",
    "stderr": "",
    "results": "",
    "successful": true
  }
}
```

### webReader_blacklist.json Structure (Persistent)

```json
{
  "domains": {
    "example-failing-domain.com": {
      "failures": 4,
      "last_failure": "2025-12-22T14:30:00.000000"
    }
  },
  "threshold": 3
}
```

---

## Local vs Remote Data Flow

### Data Flow Policy

**LOCAL-FIRST**: Prefer receiving data directly into agent context.

1. **Default Behavior** (`sync=False` or no `sync_response_to_workbench`):
   - Faster - no unnecessary download steps
   - Data returned directly in tool response
   - Suitable for < 5MB responses

2. **Workbench Sync** (`sync_response_to_workbench=True`):
   - Only for massive data (> 5MB)
   - Results saved to remote file
   - Requires explicit `workbench_download` to retrieve

### File Transfer

```mermaid
sequenceDiagram
    participant Agent as Agent
    participant Local as Local FS
    participant Bridge as WorkbenchBridge
    participant Remote as Remote Workbench

    Note over Agent,Remote: UPLOAD (Local -> Remote)
    Agent->>Local: Read file as bytes
    Agent->>Agent: Base64 encode
    Agent->>Bridge: upload(local_path, remote_path)
    Bridge->>Remote: Execute Python decode script
    Remote->>Remote: Write decoded bytes
    Remote-->>Bridge: Success response
    Bridge-->>Agent: Upload confirmation

    Note over Agent,Remote: DOWNLOAD (Remote -> Local)
    Agent->>Bridge: download(remote_path, local_path)
    Bridge->>Remote: CODEINTERPRETER_GET_FILE_CMD
    Remote-->>Bridge: File content OR auto-download URI
    alt Auto-download enabled
        Bridge->>Local: Copy from auto-downloaded path
    else Direct content
        Bridge->>Local: Write content bytes
    end
    Bridge-->>Agent: Download confirmation
```

**Code Reference**: `workbench_bridge.py:47-184`

### Path Conventions

| Type | Format | Accessible By |
|------|--------|---------------|
| Local path | `/home/kjdragan/...` or `relative/path` | `local_toolkit` tools |
| Remote path | `/home/user/...` | `COMPOSIO_REMOTE_WORKBENCH` only |
| Workspace path | `AGENT_RUN_WORKSPACES/session_XXX/` | Agent knows via `CURRENT_SESSION_WORKSPACE` |

**Important**: After `workbench_upload`, use the **REMOTE path** in workbench code, NOT the local path.

---

## Appendix: Code References

| Component | File | Lines | Description |
|-----------|------|-------|-------------|
| `DualWriter` | `main.py` | 23-46 | Dual stdout/stderr to file |
| `parse_relative_date()` | `main.py` | 137-159 | Convert "2 hours ago" to YYYY-MM-DD |
| `_update_domain_blacklist()` | `main.py` | 167-205 | Domain failure tracking |
| `observe_and_save_search_results()` | `main.py` | 218-414 | SERP artifact observer |
| `observe_and_save_workbench_activity()` | `main.py` | 416-476 | Workbench activity observer |
| `observe_and_enrich_corpus()` | `main.py` | 479-627 | Article extraction observer |
| `verify_subagent_compliance()` | `main.py` | 629-672 | Sub-agent compliance checker |
| `run_conversation()` | `main.py` | 691-914 | Main agent loop with tracing |
| Workspace initialization | `main.py` | 1006-1169 | Session setup |
| `WorkbenchBridge.download()` | `workbench_bridge.py` | 47-129 | Remote file download |
| `WorkbenchBridge.upload()` | `workbench_bridge.py` | 131-184 | Remote file upload |
| `save_corpus()` | `mcp_server.py` | 70-135 | Corpus saving tool |
| `write_local_file()` | `mcp_server.py` | 54-66 | Local file write tool |

---

*Document Version: 1.0*
*Last Updated: 2025-12-22*
*Universal Agent Architecture Documentation*
