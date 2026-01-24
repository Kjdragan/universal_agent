# Sequence Diagrams

This document contains comprehensive Mermaid sequence diagrams for all major flows in the UA Gateway system.

---

## Table of Contents

1. [CLI Query Flow](#1-cli-query-flow)
2. [In-Process Gateway Execution](#2-in-process-gateway-execution)
3. [External Gateway Execution](#3-external-gateway-execution)
4. [Tool Execution Chain](#4-tool-execution-chain)
5. [URW Phase Execution](#5-urw-phase-execution)
6. [Worker Pool Lifecycle](#6-worker-pool-lifecycle)
7. [Lease Acquisition and Failover](#7-lease-acquisition-and-failover)
8. [Session Management](#8-session-management)

---

## 1. CLI Query Flow

How a query flows from CLI through the gateway to execution.

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant CLI as main.py
    participant GW as Gateway
    participant Agent as UniversalAgent
    participant LLM as Claude API
    participant Tools
    
    User->>CLI: python -m universal_agent "query"
    CLI->>CLI: Parse args, check UA_USE_GATEWAY
    
    alt Gateway Enabled
        CLI->>GW: create_session(user_id, workspace)
        GW-->>CLI: GatewaySession
        CLI->>GW: execute(session, request)
        GW->>Agent: run_query(prompt)
    else Direct Mode
        CLI->>Agent: run_query(prompt)
    end
    
    Agent->>LLM: API call with prompt
    LLM-->>Agent: Response + tool calls
    
    loop Tool Execution
        Agent->>Tools: Execute tool
        Tools-->>Agent: Result
        Agent->>LLM: Tool result
        LLM-->>Agent: Next response
    end
    
    Agent-->>GW: Event stream
    GW-->>CLI: Event stream
    CLI-->>User: Rendered output
```

---

## 2. In-Process Gateway Execution

Detailed flow for `InProcessGateway.execute()`.

```mermaid
sequenceDiagram
    autonumber
    participant Client
    participant IPG as InProcessGateway
    participant Setup as AgentSetup
    participant Agent as UniversalAgent
    participant TC as ToolCoordinator
    
    Client->>IPG: execute(session, request)
    
    IPG->>IPG: Get/create AgentSetup for session
    
    alt New Session
        IPG->>Setup: AgentSetup(workspace_dir)
        Setup->>Setup: Initialize MCP servers
        Setup->>Setup: Load tools
        Setup-->>IPG: Setup ready
    else Existing Session
        IPG->>Setup: Rebind workspace if changed
    end
    
    IPG->>Agent: UniversalAgent.from_setup(setup)
    Agent-->>IPG: Agent instance
    
    IPG->>Agent: run_query(user_input)
    
    loop Agent Loop
        Agent-->>IPG: TEXT event
        IPG-->>Client: TEXT event
        
        opt Tool Call
            Agent-->>IPG: TOOL_CALL event
            IPG-->>Client: TOOL_CALL event
            
            Agent->>TC: Execute tool
            TC-->>Agent: Tool result
            
            Agent-->>IPG: TOOL_RESULT event
            IPG-->>Client: TOOL_RESULT event
        end
        
        Agent-->>IPG: ITERATION_END event
        IPG-->>Client: ITERATION_END event
    end
    
    IPG-->>Client: Stream complete
```

---

## 3. External Gateway Execution

Flow for `ExternalGateway` connecting to remote server.

```mermaid
sequenceDiagram
    autonumber
    participant Client
    participant EXG as ExternalGateway
    participant HTTP as HTTP Client
    participant WS as WebSocket
    participant Server as Gateway Server
    participant IPG as InProcessGateway
    
    Client->>EXG: create_session(user_id, workspace)
    EXG->>HTTP: POST /sessions
    HTTP->>Server: HTTP Request
    Server->>IPG: create_session()
    IPG-->>Server: GatewaySession
    Server-->>HTTP: JSON Response
    HTTP-->>EXG: Session data
    EXG-->>Client: GatewaySession
    
    Client->>EXG: execute(session, request)
    EXG->>WS: Connect /sessions/{id}/execute
    WS->>Server: WebSocket upgrade
    Server-->>WS: Connection accepted
    
    EXG->>WS: {"action": "execute", "user_input": "..."}
    WS->>Server: Message
    Server->>IPG: execute(session, request)
    
    loop Event Stream
        IPG-->>Server: AgentEvent
        Server-->>WS: JSON event
        WS-->>EXG: Event data
        EXG-->>Client: AgentEvent
    end
    
    Server-->>WS: Stream complete
    WS-->>EXG: Close
    EXG-->>Client: Iteration complete
```

---

## 4. Tool Execution Chain

Detailed tool execution with ledger recording.

```mermaid
sequenceDiagram
    autonumber
    participant Agent as UniversalAgent
    participant TC as ToolCoordinator
    participant Ledger as ToolCallLedger
    participant DB as Runtime DB
    participant Tool as Tool Implementation
    
    Agent->>TC: Execute tool(name, input)
    
    TC->>Ledger: prepare_tool_call(tool_call_id, ...)
    Ledger->>Ledger: Compute idempotency key
    Ledger->>DB: Check for existing receipt
    
    alt Already Executed (Idempotent)
        DB-->>Ledger: Existing receipt
        Ledger-->>TC: LedgerReceipt (cached)
        TC-->>Agent: Cached result
    else New Execution
        Ledger->>DB: INSERT tool_call record
        Ledger-->>TC: (None, idempotency_key)
        
        TC->>Ledger: mark_running(tool_call_id)
        TC->>Tool: Execute
        
        alt Success
            Tool-->>TC: Result
            TC->>Ledger: mark_succeeded(tool_call_id, result)
            Ledger->>DB: UPDATE status=succeeded
        else Failure
            Tool-->>TC: Error
            TC->>Ledger: mark_failed(tool_call_id, error)
            Ledger->>DB: UPDATE status=failed
        end
        
        TC-->>Agent: Tool result/error
    end
```

---

## 5. URW Phase Execution

URW harness executing phases through gateway.

```mermaid
sequenceDiagram
    autonumber
    participant HO as HarnessOrchestrator
    participant PM as PlanManager
    participant GW as Gateway
    participant Agent as UniversalAgent
    participant Eval as PhaseEvaluator
    
    HO->>PM: Generate plan from request
    PM-->>HO: Plan with phases
    
    loop Each Phase
        HO->>HO: Emit URW_PHASE_START event
        HO->>HO: Build phase prompt
        HO->>HO: Setup phase workspace
        
        alt use_gateway=true
            HO->>GW: _gateway_process_turn(prompt, workspace)
            GW->>Agent: execute(session, request)
            
            loop Agent work
                Agent-->>GW: Events
                GW-->>HO: Events
            end
            
            GW-->>HO: Result dict
        else use_gateway=false
            HO->>Agent: process_turn(prompt, workspace)
            Agent-->>HO: Result
        end
        
        HO->>Eval: _evaluate_phase(phase, result)
        Eval->>Eval: Check artifacts
        Eval->>Eval: Verify requirements
        Eval-->>HO: EvaluationResult
        
        HO->>HO: Emit URW_EVALUATION event
        
        alt Evaluation Passed
            HO->>HO: Emit URW_PHASE_COMPLETE event
            HO->>PM: Mark phase complete
        else Evaluation Failed
            alt Retries remaining
                HO->>HO: Build repair prompt
                Note over HO: Loop back to execute
            else No retries
                HO->>HO: Emit URW_PHASE_FAILED event
                HO->>PM: Mark phase failed
            end
        end
    end
    
    HO->>HO: Generate summary
    HO-->>HO: Return final result
```

---

## 6. Worker Pool Lifecycle

Worker pool startup, scaling, and shutdown.

```mermaid
sequenceDiagram
    autonumber
    participant Main
    participant WPM as WorkerPoolManager
    participant Mon as Monitor Task
    participant W1 as Worker 1
    participant W2 as Worker 2
    participant DB as Database
    
    Main->>WPM: start()
    WPM->>DB: connect_runtime_db()
    
    loop Spawn min_workers
        WPM->>W1: Worker(config)
        W1->>W1: start()
        W1-->>WPM: Ready
    end
    
    WPM->>Mon: Start monitor loop
    
    par Monitor Loop
        loop Every 10s
            Mon->>DB: Check queue depth
            DB-->>Mon: queue_depth
            
            alt Scale up needed
                Mon->>WPM: _spawn_worker()
                WPM->>W2: Worker(config)
                W2-->>WPM: Ready
            else Scale down needed
                Mon->>WPM: _remove_worker(idle_worker)
                WPM->>W1: stop(drain=True)
                W1-->>WPM: Stopped
            end
        end
    and Worker 1 Loop
        loop Poll for work
            W1->>DB: list_runs(status=queued)
            W1->>DB: acquire_run_lease()
            W1->>W1: Process run
            W1->>DB: release_run_lease()
        end
    end
    
    Main->>WPM: stop(drain=True)
    WPM->>Mon: Cancel
    WPM->>W1: stop(drain=True)
    WPM->>W2: stop(drain=True)
    W1-->>WPM: Stopped
    W2-->>WPM: Stopped
    WPM-->>Main: Pool stopped
```

---

## 7. Lease Acquisition and Failover

How leases enable work to transfer between workers.

```mermaid
sequenceDiagram
    autonumber
    participant W1 as Worker 1
    participant W2 as Worker 2
    participant DB as Database
    participant GW as Gateway
    
    Note over DB: Run queued: job_123
    
    W1->>DB: acquire_run_lease(job_123, worker_1, 60s)
    DB-->>W1: True (acquired)
    
    W2->>DB: acquire_run_lease(job_123, worker_2, 60s)
    DB-->>W2: False (held by worker_1)
    
    W1->>GW: Execute job_123
    
    par Heartbeat Loop
        loop Every 15s
            W1->>DB: heartbeat_run_lease(job_123, worker_1, 60s)
            DB-->>W1: True
        end
    and Execution
        GW-->>W1: Processing...
    end
    
    Note over W1: Worker 1 crashes!
    
    Note over DB: 60s pass, lease expires
    
    W2->>DB: acquire_run_lease(job_123, worker_2, 60s)
    DB-->>W2: True (lease expired)
    
    W2->>DB: Get run checkpoint
    DB-->>W2: Last known state
    
    W2->>GW: Resume job_123 from checkpoint
    GW-->>W2: Execution complete
    
    W2->>DB: Update status=completed
    W2->>DB: release_run_lease(job_123, worker_2)
```

---

## 8. Session Management

Session creation, reuse, and cleanup.

```mermaid
sequenceDiagram
    autonumber
    participant C1 as Client 1
    participant C2 as Client 2
    participant GW as Gateway
    participant Store as Session Store
    participant Setup as AgentSetup
    
    C1->>GW: create_session(user_1, /workspace_a)
    GW->>Store: Generate session_id
    GW->>Setup: AgentSetup(/workspace_a)
    Setup-->>GW: Setup ready
    GW->>Store: Store session + setup
    GW-->>C1: GatewaySession(sess_abc)
    
    C2->>GW: create_session(user_2, /workspace_b)
    GW->>Store: Generate session_id
    GW->>Setup: AgentSetup(/workspace_b)
    Setup-->>GW: Setup ready
    GW->>Store: Store session + setup
    GW-->>C2: GatewaySession(sess_def)
    
    C1->>GW: execute(sess_abc, request_1)
    GW->>Store: Get setup for sess_abc
    Store-->>GW: Cached setup
    GW->>GW: Execute with cached setup
    GW-->>C1: Result
    
    C1->>GW: execute(sess_abc, request_2)
    Note over GW: Reuses same setup (session state preserved)
    GW-->>C1: Result
    
    Note over Store: Session timeout (e.g., 1 hour)
    
    C1->>GW: execute(sess_abc, request_3)
    GW->>Store: Get session
    Store-->>GW: Session expired
    GW-->>C1: SessionExpiredError
    
    C1->>GW: create_session(user_1, /workspace_a)
    GW-->>C1: New GatewaySession(sess_xyz)
```

---

## Component Interaction Overview

High-level component relationships.

```mermaid
graph TB
    subgraph "Entry Points"
        CLI[CLI main.py]
        API[API Server]
        URW[URW Harness]
        WP[Worker Pool]
    end
    
    subgraph "Gateway Layer"
        GWI{Gateway Interface}
        IPG[InProcessGateway]
        EXG[ExternalGateway]
        GWS[Gateway Server :8002]
    end
    
    subgraph "Agent Layer"
        UA[UniversalAgent]
        TC[ToolCoordinator]
        AS[AgentSetup]
    end
    
    subgraph "External Services"
        LLM[Claude API]
        MCP[MCP Servers]
        COMP[Composio]
    end
    
    subgraph "Persistence"
        DB[(Runtime DB)]
        FS[File System]
    end
    
    CLI --> GWI
    API --> GWI
    URW --> GWI
    WP --> GWI
    
    GWI --> IPG
    GWI --> EXG
    EXG -.->|HTTP/WS| GWS
    GWS --> IPG
    
    IPG --> AS
    AS --> UA
    UA --> TC
    TC --> MCP
    TC --> COMP
    UA --> LLM
    
    WP --> DB
    TC --> DB
    UA --> FS
```
