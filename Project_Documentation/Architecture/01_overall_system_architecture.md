# Overall System Architecture

**Document Version**: 1.0
**Last Updated**: 2025-12-22
**Component**: Universal Agent
**Primary Files**: `src/universal_agent/main.py`, `src/mcp_server.py`, `src/tools/workbench_bridge.py`

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [High-Level System Overview](#high-level-system-overview)
3. [Core Components](#core-components)
4. [Component Relationships](#component-relationships)
5. [Key Subsystems](#key-subsystems)
6. [Data Flow Architecture](#data-flow-architecture)
7. [Deployment Architecture](#deployment-architecture)
8. [Technology Stack](#technology-stack)

---

## Executive Summary

The Universal Agent is a **standalone AI agent system** built on the Claude Agent SDK with Composio Tool Router integration. It implements a dual-path execution model (Fast Path for simple queries, Complex Path for tool-enabled workflows) with comprehensive observability via Logfire distributed tracing.

### Key Characteristics

| Aspect | Description |
|--------|-------------|
| **Architecture Pattern** | Event-driven, Observer pattern, Sub-agent delegation |
| **Execution Model** | Dual-path: Simple (direct) vs Complex (tool loop) |
| **Tool Integration** | MCP (Model Context Protocol) servers |
| **Observability** | Logfire distributed tracing with MCP instrumentation |
| **State Management** | Per-session workspace with artifact persistence |
| **Communication** | HTTP (remote MCP) + stdio (local MCP) |

---

## High-Level System Overview

### System Context Diagram

```mermaid
flowchart TB
    subgraph External["External Services"]
        ClaudeAPI["Claude API<br/>(Anthropic/Z.AI)"]
        ComposioAPI["Composio Tool Router<br/>(500+ tools)"]
        LogfireAPI["Logfire Tracing<br/>(Observability)"]
    end

    subgraph UniversalAgent["Universal Agent System"]
        direction TB

        subgraph Core["Core Components"]
            Main["main.py<br/>(Event Loop & Orchestrator)"]
            Client["ClaudeSDKClient<br/>(Claude Agent SDK)"]
            Options["ClaudeAgentOptions<br/>(Configuration)"]
        end

        subgraph SubAgents["Sub-Agents"]
            ReportExpert["report-creation-expert<br/>(Report Generation)"]
        end

        subgraph Observers["Observer Pattern"]
            SearchObs["Search Results Observer"]
            CorpusObs["Corpus Enrichment Observer"]
            WorkbenchObs["Workbench Activity Observer"]
        end

        subgraph MCPLayer["MCP Server Layer"]
            LocalMCP["Local Toolkit MCP<br/>(stdio)"]
            ComposioMCP["Composio MCP<br/>(HTTP)"]
        end

        subgraph Bridges["Bridge Layer"]
            WorkbenchBridge["WorkbenchBridge<br/>(Local-Remote File Transfer)"]
        end
    end

    subgraph Workspace["Session Workspace<br/>(AGENT_RUN_WORKSPACES/)"]
        SearchResults["search_results/"]
        ExtractedArticles["extracted_articles/"]
        WorkProducts["work_products/"]
        RunLog["run.log"]
        TraceJSON["trace.json"]
    end

    User(("User")) -->|"Enter Query"| Main

    Main --> Client
    Client --> Options
    Options --> LocalMCP
    Options --> ComposioMCP
    Options --> WebReaderMCP

    Client -->|"Delegate Task"| ReportExpert

    Main -->|"Trigger"| SearchObs
    Main -->|"Trigger"| CorpusObs
    Main -->|"Trigger"| WorkbenchObs

    LocalMCP --> WorkbenchBridge

    Client -->|"API Calls"| ClaudeAPI
    ComposioMCP -->|"Tool Execution"| ComposioAPI

    ReportExpert -->|"Generate Reports"| WorkProducts
    Main -->|"Log Output"| RunLog
    Main -->|"Save Trace"| TraceJSON

    style UniversalAgent fill:#e3f2fd
    style Core fill:#bbdefb
    style MCPLayer fill:#c8e6c9
    style Observers fill:#fff9c4
    style SubAgents fill:#ffccbc
    style Workspace fill:#f3e5f5
    style External fill:#ffe0b2
```

### Discussion: System Context

This diagram illustrates the complete system boundaries and external dependencies:

1. **User Interaction**: The user enters queries via the terminal interface provided by `prompt_toolkit`

2. **External Services**: The system depends on four key external APIs:
   - **Claude API**: For LLM inference (via Z.AI endpoint)
   - **Composio API**: For 500+ tool integrations (Gmail, SERP, Slack, etc.)
   - **Logfire API**: For distributed tracing and observability

3. **Core Components**: The main agent logic, SDK client, and configuration

4. **MCP Server Layer**: Three MCP servers provide different capabilities:
   - Local Toolkit (stdio): Custom local tools
   - Composio (HTTP): Remote tool router

5. **Observer Pattern**: Async observers that save artifacts without blocking the agent loop

6. **Workspace**: Per-session storage for artifacts, logs, and traces

---

## Core Components

### 1. ClaudeSDKClient (Main Agent Brain)

**Location**: `src/universal_agent/main.py:112-122`

**Purpose**: The primary interface for Claude Agent SDK, managing conversation state and tool execution.

```mermaid
classDiagram
    class ClaudeSDKClient {
        +ClaudeAgentOptions options
        +query(message: str) Promise
        +receive_response() AsyncIterator
    }

    class ClaudeAgentOptions {
        +str system_prompt
        +dict mcp_servers
        +list allowed_tools
        +dict agents
        +str permission_mode
    }

    class AgentDefinition {
        +str description
        +str prompt
        +list tools
        +str model
    }

    class MCPServerConfig {
        +str type
        +str url
        +dict headers
        +str command
        +list args
    }

    ClaudeSDKClient --> ClaudeAgentOptions
    ClaudeAgentOptions --> MCPServerConfig
    ClaudeAgentOptions --> AgentDefinition
```

**Configuration Structure** (`main.py:1025-1146`):

```python
options = ClaudeAgentOptions(
    system_prompt=(...),  # Lines 1026-1074
    mcp_servers={
        "composio": {
            "type": "http",
            "url": session.mcp.url,
            "headers": {"x-api-key": os.environ["COMPOSIO_API_KEY"]},
        },
        "local_toolkit": {
            "type": "stdio",
            "command": sys.executable,
            "args": ["src/mcp_server.py"],
        },
        },
    },
    allowed_tools=["Task"],
    agents={
        "report-creation-expert": AgentDefinition(
            description="...",
            prompt="...",
            tools=[
                "mcp__local_toolkit__crawl_parallel",
                "mcp__local_toolkit__save_corpus",
                "mcp__local_toolkit__write_local_file",
                "mcp__local_toolkit__workbench_download",
                "mcp__local_toolkit__workbench_upload",
            ],
            model="inherit",
        ),
    },
    permission_mode="bypassPermissions",
)
```

---

### 2. Query Classifier

**Location**: `src/universal_agent/main.py:917-956`

**Purpose**: Determines whether a query requires tools (COMPLEX) or can be answered directly (SIMPLE).

```mermaid
flowchart LR
    Input[User Query] --> Classify["classify_query()"]

    Classify --> Prompt["Build Classification<br/>Prompt"]
    Prompt --> LLM["Claude API"]

    LLM --> Parse["Parse Response"]
    Parse --> Decision{Contains<br/>SIMPLE?}

    Decision -->|Yes| Simple["SIMPLE"]
    Decision -->|No| Complex["COMPLEX"]

    Classify -.->|"Default Fallback"| Complex

    Simple --> Log["Log to Logfire"]
    Complex --> Log

    style Simple fill:#90EE90
    style Complex fill:#87CEEB
```

**Classification Criteria**:

| Type | Definition | Examples |
|------|------------|----------|
| **SIMPLE** | Answerable from foundational knowledge, no tools needed | "Capital of France", "Explain recursion" |
| **COMPLEX** | Requires external tools, real-time data, or multi-step workflows | "Search for news", "Send email", "Execute code" |

---

### 3. Dual-Path Execution Router

**Location**: `src/universal_agent/main.py:959-998`, `main.py:691-914`

**Purpose**: Routes queries to either Fast Path (direct answer) or Complex Path (tool loop).

```mermaid
flowchart TB
    Start([User Query]) --> Classify["classify_query()"]

    Classify --> Route{Decision}

    Route -->|SIMPLE| FastPath["handle_simple_query()"]
    Route -->|COMPLEX| ToolLoop["run_conversation()"]

    FastPath --> Check{Tool Use<br/>Detected?}

    Check -->|No| Direct["Direct Answer<br/>(Fast Path)"]
    Check -->|Yes| Fallback["Fallback to<br/>Complex Path"]

    Fallback --> ToolLoop

    ToolLoop --> IterLoop{More Tools<br/>Needed?}

    IterLoop -->|Yes| ExecuteTools["Execute Tools"]
    ExecuteTools --> IterLoop

    IterLoop -->|No| Complete(["Complete"])

    Direct --> End(["Session Continues"])
    Complete --> End

    style FastPath fill:#90EE90
    style Fallback fill:#FFB6C1
    style ToolLoop fill:#87CEEB
    style Direct fill:#32CD32
```

---

## Component Relationships

### Component Interaction Diagram

```mermaid
flowchart TB
    subgraph MainLoop["Main Event Loop (main.py:1001-1348)"]
        direction TB
        Session["Session Setup"]
        InputLoop["User Input Loop"]
        Classify["classify_query()"]
        Router["Path Router"]
    end

    subgraph Paths["Execution Paths"]
        Fast["handle_simple_query()"]
        Complex["run_conversation()"]
    end

    subgraph Observers["Observer Pattern"]
        SearchObs["observe_and_save_search_results()"]
        WorkbenchObs["observe_and_save_workbench_activity()"]
    end

    subgraph Validation["Compliance Check"]
        Verify["verify_subagent_compliance()"]
    end

    subgraph Output["Session Management"]
        Summary["Execution Summary"]
        Trace["Save Trace"]
    end

    Session --> InputLoop
    InputLoop --> Classify
    Classify --> Router

    Router -->|SIMPLE| Fast
    Router -->|COMPLEX| Complex

    Complex -->|"On Tool Result"| SearchObs
    Complex -->|"On Tool Result"| WorkbenchObs

    Complex -->|"After Task Tool"| Verify

    InputLoop -->|"On Quit"| Summary
    Summary --> Trace

    style MainLoop fill:#e1f5fe
    style Paths fill:#c8e6c9
    style Observers fill:#fff9c4
    style Validation fill:#ffccbc
```

### Detailed Flow: Main Event Loop

```mermaid
sequenceDiagram
    participant User
    participant Main as main()
    participant Classify as classify_query()
    participant Fast as handle_simple_query()
    participant Complex as run_conversation()
    participant Observers as Observers
    participant Verify as verify_subagent_compliance()
    participant Workspace as File System

    User->>Main: Enter request
    Main->>Main: Create session workspace
    Main->>Main: Initialize trace dict
    Main->>Main: Setup log redirection

    loop User Input Loop
        Main->>Classify: Classify query
        Classify-->>Main: SIMPLE or COMPLEX

        alt SIMPLE
            Main->>Fast: Handle directly
            Fast->>Fast: Query LLM
            Fast-->>Main: Success or Fallback

            alt Fallback needed
                Main->>Main: Set to COMPLEX
            end
        end

        alt COMPLEX or Fallback
            Main->>Complex: Run conversation

            loop Tool Loop
                Complex->>Complex: Query LLM with tools
                Complex->>Complex: Process response

                loop For each tool result
                    Complex->>Observers: Create observer task
                    Observers->>Workspace: Save artifact
                end

                alt Task tool result
                    Complex->>Verify: Check compliance
                    Verify->>Workspace: Check for artifacts
                    Verify-->>Complex: Error message if non-compliant
                end

                Complex-->>Main: Continue or Done
            end
        end

        User->>Main: Next query or quit
    end

    Main->>Main: Generate summary
    Main->>Workspace: Save trace.json
    Main->>User: Display final statistics
```

---

## Key Subsystems

### 1. Sub-Agent Delegation System

**Location**: `.claude/agents/report-creation-expert.md`, `main.py:1095-1143`

**Purpose**: Delegate specialized tasks to expert sub-agents with focused tool access.

```mermaid
flowchart TB
    subgraph MainAgent["Main Agent"]
        UserQuery["User Query:<br/>'Research X and<br/>email report'"]
        Decision{"Is Report<br/>Task?"}
        Search["Composio Search<br/>(Get URLs)"]
    end

    subgraph SubAgent["report-creation-expert Sub-Agent"]
        direction TB
        Extract["crawl_parallel<br/>(Batch of 10)"]
        Count{"10 successful<br/>OR 2 batches?"}
        SaveCorpus["save_corpus()"]
        Synthesize["Synthesize Report<br/>(HTML)"]
        WriteFile["write_local_file()"]
    end

    subgraph Output["Workspace"]
        Corpus["expanded_corpus.json"]
        Report["work_products/report.html"]
    end

    UserQuery --> Decision
    Decision -->|Yes| Search
    Search --> Delegate["Delegate to<br/>sub-agent"]

    Delegate --> Extract
    Delegate --> Extract
    Extract --> Count

    SaveCorpus --> Corpus
    Corpus --> Synthesize
    Synthesize --> WriteFile
    WriteFile --> Report

    Report --> Return["Return Report<br/>to Main Agent"]

    style MainAgent fill:#e1f5fe
    style SubAgent fill:#ffccbc
    style Output fill:#c8e6c9
```

**AgentDefinition Configuration**:

```python
# main.py:1095-1143
"report-creation-expert": AgentDefinition(
    description=(
        "MANDATORY DELEGATION TARGET for ALL report generation tasks. "
        "WHEN TO DELEGATE: User asks for 'report', 'comprehensive', 'detailed', "
        "'in-depth', 'analysis', or 'summary' of search results."
    ),
    prompt=f"""
    Result Date: {datetime.now().strftime('%A, %B %d, %Y')}
    CURRENT_SESSION_WORKSPACE: {workspace_dir}

    You are a **Report Creation Expert**.

    ## WORKFLOW
    ### Step 1: Check Request Type
    - If 'comprehensive', 'detailed', 'in-depth' → Extract articles
    - Otherwise → Skip to Step 4 (use search snippets)

    ### Step 2: Extract Articles (OPTIMIZED)
    - Use crawl_parallel
    - Scrape 10 URLs in parallel

    ### Step 3: Synthesis
    - Read markdown files from search_results/
    - Synthesize content

    ### Step 4: Synthesize Report
    - Structure: Exec Summary → ToC → Thematic Sections → Sources
    - Include: numbers, dates, direct quotes, citations
    - Modern HTML with gradients, info boxes

    ### Step 5: Save Report
    - Save as .html to {workspace_dir}/work_products/
    - Use write_local_file
    """,
    tools=[
        "mcp__local_toolkit__crawl_parallel",
        "mcp__local_toolkit__save_corpus",
        "mcp__local_toolkit__write_local_file",
        "mcp__local_toolkit__workbench_download",
        "mcp__local_toolkit__workbench_upload",
    ],
    model="inherit",
)
```

---

### 2. Observer Pattern System

**Location**: `src/universal_agent/main.py:218-627`

**Purpose**: Asynchronously process and save tool results without blocking the agent loop.

**Why Observer Pattern?**

Composio hooks (`@after_execute`) don't fire in MCP mode because execution happens on the remote server. The observer pattern processes results after they return to the client.

```mermaid
flowchart TB
    subgraph AgentLoop["Agent Loop (main.py:691-914)"]
        ToolCall["Tool Use Block"]
        ToolExecute["Execute Tool via MCP"]
        ToolResult["Tool Result Block"]
    end

    subgraph Observers["Async Observers"]
        direction LR
        SearchObs["observe_and_save_<br/>search_results()"]
        WorkbenchObs["observe_and_save_<br/>workbench_activity()"]
    end

    subgraph Processing["Observer Processing"]
        ExtractJSON["Extract JSON<br/>from TextBlock"]
        ParseData["Parse SERP Schema"]
        CleanData["Clean &<br/>Normalize"]
        SaveFile["Save to<br/>Workspace"]
    end

    subgraph Workspace["Workspace Artifacts"]
        SERP["search_results/*.json"]
        Activity["workbench_activity/*.json"]
    end

    ToolCall --> ToolExecute
    ToolExecute --> ToolResult

    ToolResult -->|"asyncio.create_task"| Observers

    SearchObs --> ExtractJSON
    CorpusObs --> ExtractJSON
    WorkbenchObs --> ExtractJSON

    ExtractJSON --> ParseData
    ParseData --> CleanData
    CleanData --> SaveFile

    SearchObs --> SaveFile
    SaveFile --> SERP
    WorkbenchObs --> SaveFile
    SaveFile --> Activity

    style AgentLoop fill:#e1f5fe
    style Observers fill:#fff9c4
    style Processing fill:#c8e6c9
    style Workspace fill:#f3e5f5
```

**Observer Functions**:

| Observer | Location | Triggers On | Saves To |
|----------|----------|-------------|----------|
| `observe_and_save_search_results()` | `main.py:218-413` | SERP tools | `search_results/*.json` |

| `observe_and_save_workbench_activity()` | `main.py:416-477` | Workbench tools | `workbench_activity/*.json` |
| `verify_subagent_compliance()` | `main.py:629-673` | Task results | Injects error if non-compliant |

**Fire-and-Forget Pattern**:

```python
# main.py:867-890
if tool_name and OBSERVER_WORKSPACE_DIR:
    # Create tasks that run in background without blocking
    asyncio.create_task(
        observe_and_save_search_results(
            tool_name, block_content, OBSERVER_WORKSPACE_DIR
        )
    )
    asyncio.create_task(
        observe_and_save_workbench_activity(
            tool_name, tool_input or {}, content_str, OBSERVER_WORKSPACE_DIR
        )
    )
```

---

### 3. MCP Server Integration

**Location**: `src/universal_agent/main.py:1075-1093`, `src/mcp_server.py`

**Purpose**: Provide tools through MCP protocol for both local and remote capabilities.

```mermaid
flowchart TB
    subgraph SDKClient["ClaudeSDKClient"]
        Options["ClaudeAgentOptions"]
        MCPConfig["mcp_servers dict"]
    end

    subgraph MCPServers["MCP Servers"]
        direction LR

        subgraph LocalMCP["Local Toolkit MCP"]
            type1["Type: stdio"]
            cmd1["python src/mcp_server.py"]
            tools1["save_corpus<br/>write_local_file<br/>workbench_download<br/>workbench_upload"]
        end

        subgraph ComposioMCP["Composio MCP"]
            type2["Type: http"]
            url2["session.mcp.url"]
            auth2["x-api-key header"]
            tools2["COMPOSIO_SEARCH_*<br/>GMAIL_SEND_EMAIL<br/>COMPOSIO_REMOTE_WORKBENCH"]
        end


    end

    subgraph Naming["Tool Naming Convention"]
        Pattern["mcp__<server>__<tool>"]

        Ex1["mcp__local_toolkit__save_corpus"]
        Ex2["mcp__composio__GMAIL_SEND_EMAIL"]
        Ex3["mcp__web_reader__webReader"]
    end

    Options --> MCPConfig
    MCPConfig --> LocalMCP
    MCPConfig --> ComposioMCP

    tools1 --> Naming
    tools2 --> Naming

    style SDKClient fill:#e1f5fe
    style MCPServers fill:#c8e6c9
    style LocalMCP fill:#a5d6a7
    style ComposioMCP fill:#ffccbc
    style WebReaderMCP fill:#d1c4e9
```

---

### 4. Workspace Management System

**Location**: `src/universal_agent/main.py:1006-1168`

**Purpose**: Create per-session workspaces for artifact isolation and traceability.

```mermaid
flowchart TB
    subgraph Initialization["Session Initialization (main.py:1006-1168)"]
        Timestamp["datetime.now().strftime()"]
        WorkspaceDir["AGENT_RUN_WORKSPACES/<br/>session_YYYYMMDD_HHMMSS"]
        CreateDir["os.makedirs()"]
    end

    subgraph ComposioInit["Composio Initialization"]
        DownloadsDir["downloads/<br/>subdirectory"]
        ComposioClient["Composio(<br/>api_key, file_download_dir)"]
        Session["composio.create(user_id)"]
    end

    subgraph ObserverSetup["Observer Setup"]
        GlobalVar["OBSERVER_WORKSPACE_DIR"]
        SetWorkspace["Set to workspace_dir"]
    end

    subgraph OutputRedirection["Output Redirection"]
        RunLog["run.log"]
        DualWriter["DualWriter class<br/>(file + stdout)"]
        SysRedirect["sys.stdout = DualWriter"]
    end

    subgraph ContextInjection["Context Injection"]
        AbsPath["os.path.abspath(workspace_dir)"]
        SystemPrompt["options.system_prompt +=<br/>CURRENT_SESSION_WORKSPACE"]
    end

    subgraph DirectoryStructure["Directory Structure"]
        Search["search_results/"]
        Articles["extracted_articles/"]
        Products["work_products/"]
        Activity["workbench_activity/"]
        Downloads["downloads/"]
    end

    Timestamp --> WorkspaceDir
    WorkspaceDir --> CreateDir

    CreateDir --> DownloadsDir
    DownloadsDir --> ComposioClient
    ComposioClient --> Session

    CreateDir --> GlobalVar
    GlobalVar --> SetWorkspace

    CreateDir --> RunLog
    RunLog --> DualWriter
    DualWriter --> SysRedirect

    CreateDir --> AbsPath
    AbsPath --> SystemPrompt

    CreateDir --> DirectoryStructure

    style Initialization fill:#e1f5fe
    style ComposioInit fill:#ffccbc
    style ObserverSetup fill:#fff9c4
    style OutputRedirection fill:#c8e6c9
    style ContextInjection fill:#d1c4e9
    style DirectoryStructure fill:#f3e5f5
```

**Workspace Structure**:

```
AGENT_RUN_WORKSPACES/
└── session_20251222_143022/
    ├── run.log                          # Full console output
    ├── summary.txt                      # Brief execution summary
    ├── trace.json                       # Tool call/result trace
    ├── search_results/                  # Cleaned SERP artifacts
    │   ├── COMPOSIO_SEARCH_NEWS_143025.json
    │   └── COMPOSIO_SEARCH_WEB_143027.json
    ├── extracted_articles/              # Individual article extractions
    │   ├── example_com_143030.json
    │   └── news_site_143032.json
    ├── workbench_activity/              # Remote execution logs
    │   └── workbench_143035.json
    ├── work_products/                   # Final outputs
    │   └── ai_research_report.html
    ├── expanded_corpus.json             # Aggregated extraction data
    └── downloads/                       # Composio auto-downloads
        └── [temp files from remote tools]
```

---

### 5. Distributed Tracing System

**Location**: `src/universal_agent/main.py:49-111`

**Purpose**: Comprehensive observability via Logfire with MCP and HTTPX instrumentation.

```mermaid
flowchart TB
    subgraph Init["Logfire Initialization (main.py:49-111)"]
        Env["LOGFIRE_TOKEN"]
        Config["logfire.configure()"]
        Scrubber["Custom Scrubbing<br/>Callback"]
        InstrumentMCP["logfire.instrument_mcp()"]
        InstrumentHTTPX["logfire.instrument_httpx()"]
    end

    subgraph Spans["Span Hierarchy"]
        Root["standalone_composio_test"]

        subgraph QuerySpans["Per-Query Spans"]
            QueryStart["query_started"]
            Classify["query_classification"]

            subgraph Iteration["conversation_iteration_N"]
                ToolCall["tool_call"]
                MCPReq["mcp.request"]
                HTTP["httpx.request"]
                ToolResult["tool_result"]
                Reasoning["reasoning"]
                Thinking["thinking"]
            end

            Fallback["fast_path_fallback"]
        end

        Session["session_complete"]
    end

    subgraph Events["Logged Events"]
        QueryClass["query_classification<br/>(decision, raw_response)"]
        ToolCallEvent["tool_call<br/>(tool_name, tool_id, input_preview)"]
        ToolResultEvent["tool_result<br/>(content_size, is_error)"]
        ObserverEvent["observer_artifact_saved<br/>(path, type, size)"]
        Compliance["subagent_compliance_*"]
    end

    Env --> Config
    Config --> Scrubber
    Config --> InstrumentMCP
    Config --> InstrumentHTTPX

    Root --> QueryStart
    QueryStart --> Classify
    Classify --> Iteration
    Iteration --> Fallback
    QueryStart --> Session

    Iteration --> ToolCall
    ToolCall --> MCPReq
    MCPReq --> HTTP
    HTTP --> ToolResult

    Iteration --> Reasoning
    Iteration --> Thinking

    ToolCall -.->|"Logs"| ToolCallEvent
    ToolResult -.->|"Logs"| ToolResultEvent
    Observer -.->|"Logs"| ObserverEvent
    Classify -.->|"Logs"| QueryClass

    style Init fill:#e1f5fe
    style Spans fill:#c8e6c9
    style Events fill:#fff9c4
```

**Trace JSON Structure**:

```python
# main.py:1148-1164
trace = {
    "session_info": {
        "url": session.mcp.url,
        "user_id": user_id,
        "timestamp": datetime.now().isoformat(),
    },
    "query": None,
    "start_time": None,
    "end_time": None,
    "total_duration_seconds": None,
    "tool_calls": [
        {
            "iteration": 1,
            "name": "mcp__composio__COMPOSIO_SEARCH_NEWS",
            "id": "toolu_xxx",
            "time_offset_seconds": 1.234,
            "input_size_bytes": 123,
            "input_preview": "{...}",
        }
    ],
    "tool_results": [
        {
            "tool_use_id": "toolu_xxx",
            "time_offset_seconds": 2.345,
            "is_error": False,
            "content_size_bytes": 4567,
            "content_preview": "{...}",
        }
    ],
    "iterations": [
        {
            "iteration": 1,
            "query": "Search for AI news",
            "duration_seconds": 3.456,
            "tool_calls": 5,
            "needs_user_input": False,
            "auth_link": None,
        }
    ],
    "logfire_enabled": True,
    "trace_id": "1234567890abcdef...",
}
```

---

## Data Flow Architecture

### Request-Response Flow

```mermaid
sequenceDiagram
    participant User
    participant Main as main()
    participant Client as ClaudeSDKClient
    participant Classifier as classify_query()
    participant LLM as Claude API
    participant MCP as MCP Servers
    participant Tools as External Tools
    participant Observers as Observers
    participant Workspace as Workspace

    User->>Main: Enter query
    Main->>Main: trace["query"] = query
    Main->>Classifier: classify_query(client, query)
    Classifier->>LLM: Classification prompt
    LLM-->>Classifier: "SIMPLE" or "COMPLEX"
    Classifier-->>Main: decision

    alt SIMPLE
        Main->>Client: handle_simple_query(query)
        Client->>LLM: query()
        LLM-->>Client: AssistantMessage

        alt ToolUseBlock detected
            Client-->>Main: False (fallback)
            Main->>Main: is_simple = False
        else TextBlock only
            Client-->>User: Direct answer
            Client-->>Main: True
        end
    end

    alt COMPLEX or Fallback
        Main->>Client: run_conversation(query, iteration)
        Client->>LLM: query(query)

        loop Conversation iteration
            LLM-->>Client: AssistantMessage stream

            loop Content blocks
                alt ToolUseBlock
                    Client->>MCP: Execute tool
                    MCP->>Tools: Call external API
                    Tools-->>MCP: Result
                    MCP-->>Client: ToolResultBlock

                    Client->>Observers: asyncio.create_task()
                    Observers->>Workspace: Save artifact

                    Client->>Workspace: Update trace
                else TextBlock
                    Client->>User: Display text
                end
            end

            Client-->>Main: (needs_input, auth_link)

            alt needs_input
                Main->>User: Display auth link
                User->>Main: Press Enter
                Main->>Client: Continue with auth query
            end
        end
    end

    Main->>Workspace: Save trace.json
    Main->>User: Display summary
```

### Local-First Data Flow

Per the **Local-First Architecture** (`012_LOCAL_VS_WORKBENCH_ARCHITECTURE.md`):

```mermaid
flowchart TB
    subgraph Agent["Agent Decision"]
        Question{"Data Size<br/>> 5MB?"}
    end

    subgraph LocalPreferred["Local-First (Preferred)"]
        Direct["Direct Return<br/>sync=False"]
        InMemory["In-Memory Processing"]
        LocalWrite["Local File Write"]
    end

    subgraph RemoteFallback["Remote Workbench (Exception)"]
        SyncFile["sync_response_to_workbench=True"]
        RemoteSave["Save to Remote File"]
        Download["workbench_download()"]
    end

    subgraph Final["Final Output"]
        Report["Local Report<br/>(work_products/)"]
        Email["Email Attachment<br/>(via workbench_upload)"]
    end

    Question -->|No, <5MB| LocalPreferred
    Question -->|Yes, >5MB| RemoteFallback

    Direct --> InMemory
    InMemory --> LocalWrite
    LocalWrite --> Report

    SyncFile --> RemoteSave
    RemoteSave --> Download
    Download --> Report

    Report -->|"Email required"| Email

    style Agent fill:#e1f5fe
    style LocalPreferred fill:#c8e6c9
    style RemoteFallback fill:#ffccbc
```

---

## Deployment Architecture

### Physical Architecture

```mermaid
flowchart TB
    subgraph LocalMachine["User's Local Machine"]
        direction LR

        subgraph AgentProcess["Universal Agent Process"]
            Main["main.py<br/>(uv run)"]
            MCPLocal["Local MCP Server<br/>(subprocess)"]
        end

        subgraph WorkspaceStorage["Workspace Storage"]
            Sessions["AGENT_RUN_WORKSPACES/"]
        end
    end

    subgraph RemoteServices["Cloud Services"]
        direction LR

        subgraph Claude["Claude API<br/>(via Z.AI)"]
            Anthropic["api.z.ai"]
        end

        subgraph Composio["Composio Cloud"]
            MCPRouter["MCP Router"]
            ToolRouter["Tool Router<br/>(500+ APIs)"]
            CodeSandbox["Code Interpreter<br/>Sandbox"]
        end

        subgraph ZAI["Z.AI Services"]
            WebReaderAPI["webReader API"]
        end

        subgraph Logfire["Logfire"]
            LogfireAPI["logfire.pydantic.dev"]
        end
    end

    Main -->|"HTTP"| Anthropic
    Main -->|"HTTP"| MCPRouter
    MCPRouter --> ToolRouter
    ToolRouter --> CodeSandbox
    Main -->|"HTTP"| WebReaderAPI
    Main -->|"HTTPS"| LogfireAPI

    Main -->|"stdio"| MCPLocal
    MCPLocal -.->|"local file ops"| Sessions

    Main -->|"file write"| Sessions

    style LocalMachine fill:#e3f2fd
    style AgentProcess fill:#bbdefb
    style WorkspaceStorage fill:#c8e6c9
    style RemoteServices fill:#ffe0b2
```

### Process Architecture

```mermaid
flowchart TB
    subgraph Parent["Parent Process (uv run)"]
        direction TB

        Main["main.py"]

        subgraph Children["Child Processes"]
            MCPServer["python src/mcp_server.py<br/>(stdio MCP)"]
        end
    end

    subgraph AsyncTasks["Async Tasks (main loop)"]
        Obs1["observe_and_save_search_results"]
        Obs2["observe_and_enrich_corpus"]
        Obs3["observe_and_save_workbench_activity"]
    end

    subgraph Resources["Resources"]
        LogFile["run.log<br/>(DualWriter redirect)"]
        Workspace["session_*/<br/>(workspace files)"]
    end

    Main -->|"spawns"| MCPServer
    Main -->|"create_task"| AsyncTasks

    Main -->|"writes"| LogFile
    Main -->|"writes"| Workspace
    AsyncTasks -->|"writes"| Workspace

    style Parent fill:#e1f5fe
    style Children fill:#c8e6c9
    style AsyncTasks fill:#fff9c4
    style Resources fill:#f3e5f5
```

---

## Technology Stack

### Core Technologies

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Agent Framework** | Claude Agent SDK | Agentic workflows, tool orchestration |
| **Language** | Python 3.12+ | Core implementation |
| **Package Manager** | uv | Fast dependency management |
| **Async Runtime** | asyncio | Concurrent execution |
| **Terminal UI** | prompt_toolkit | Enhanced terminal input |
| **Protocol** | MCP (Model Context Protocol) | Tool integration |

### Dependencies

**Key Libraries** (`pyproject.toml`):

```python
# Agent SDK
claude-agent-sdk      # Claude agentic framework

# Tool Router
composio              # Composio tool router SDK

# Observability
logfire               # Distributed tracing
httpx                 # HTTP client (instrumented)

# Terminal/CLI
prompt-toolkit        # Better terminal input
readline              # Input history/editing

# Utilities
python-dotenv         # Environment variables
```

### MCP Servers

| Server | Type | URL | Purpose |
|--------|------|-----|---------|
| **local_toolkit** | stdio | `python src/mcp_server.py` | Local file operations, corpus saving |
| **composio** | http | Dynamic from session | 500+ external tools |
| **web_reader** | http | `api.z.ai/api/mcp/web_reader/mcp` | Article extraction |

---

## References

### Related Documentation

1. **[02_query_classification_flow.md](./02_query_classification_flow.md)** - Detailed query routing
2. **[03_subagent_delegation.md](./03_subagent_delegation.md)** - Sub-agent system
3. **[05_mcp_servers.md](./05_mcp_servers.md)** - MCP server details
4. **[07_workspace_data_flow.md](./07_workspace_data_flow.md)** - Workspace management
5. **[012_LOCAL_VS_WORKBENCH_ARCHITECTURE.md](../012_LOCAL_VS_WORKBENCH_ARCHITECTURE.md)** - Local-first strategy

### Code References

| File | Lines | Component |
|------|-------|-----------|
| `src/universal_agent/main.py` | 1-1379 | Main agent implementation |
| `src/mcp_server.py` | 1-140 | Local MCP server tools |
| `src/tools/workbench_bridge.py` | 1-185 | Workbench file transfer |
| `.claude/agents/report-creation-expert.md` | 1-137 | Sub-agent definition |

---

**Document Status**: ✅ Complete
**Next Review**: After major architecture changes
**Maintainer**: Universal Agent Team
