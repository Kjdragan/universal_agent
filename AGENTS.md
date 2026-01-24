# 🤖 Universal Agent System

> A multi-agent orchestration framework powered by Claude, MCP tools, and Composio integrations.

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Universal Agent System                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   ┌─────────────┐     ┌─────────────┐     ┌─────────────────────────┐  │
│   │    CLI      │     │   FastAPI   │     │    URW Harness          │  │
│   │  (main.py)  │     │  (api.py)   │     │  (HarnessOrchestrator)  │  │
│   └──────┬──────┘     └──────┬──────┘     └───────────┬─────────────┘  │
│          │                   │                        │                 │
│          └───────────────────┼────────────────────────┘                 │
│                              ▼                                          │
│                    ┌─────────────────┐                                  │
│                    │     Gateway     │                                  │
│                    │   (gateway.py)  │                                  │
│                    └────────┬────────┘                                  │
│                             │                                           │
│          ┌──────────────────┼──────────────────┐                        │
│          ▼                  ▼                  ▼                        │
│   ┌─────────────┐   ┌─────────────┐   ┌─────────────────┐              │
│   │InProcessGW  │   │ExternalGW   │   │  AgentBridge    │              │
│   │   (local)   │   │  (remote)   │   │  (session mgmt) │              │
│   └─────────────┘   └─────────────┘   └────────┬────────┘              │
│                                                │                        │
│                                      ┌─────────▼─────────┐              │
│                                      │  UniversalAgent   │              │
│                                      │   (agent_core)    │              │
│                                      └─────────┬─────────┘              │
│                                                │                        │
│          ┌─────────────────────────────────────┼────────────────────┐   │
│          ▼                                     ▼                    ▼   │
│   ┌─────────────┐                    ┌─────────────┐        ┌─────────┐ │
│   │   Claude    │                    │  MCP Tools  │        │  Task   │ │
│   │   (LLM)     │                    │  (Composio) │        │  Tool   │ │
│   └─────────────┘                    └─────────────┘        └────┬────┘ │
│                                                                  │      │
│                                                    ┌─────────────┼──────┤
│                                                    ▼             ▼      │
│                                              ┌───────────┐ ┌───────────┐│
│                                              │ Research  │ │  Report   ││
│                                              │ Specialist│ │  Writer   ││
│                                              └───────────┘ └───────────┘│
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 🎭 Agent Types

### 1. **Coordinator Agent** (Primary)

The main agent that receives user requests and orchestrates work.

| Property | Value |
|----------|-------|
| **Role** | Task analysis, delegation, coordination |
| **Model** | Claude (Anthropic) |
| **Entry Points** | CLI (`main.py`), API (`api.py`), URW Harness |

**Capabilities:**
- Analyzes incoming requests
- Delegates to specialist sub-agents via `Task` tool
- Handles simple queries directly
- Manages session context and workspace isolation

**System Prompt Highlights:**
```
You are a COORDINATOR Agent. Your job is to assess requests and DELEGATE to specialists.
🛑 PROHIBITED: Do NOT attempt to do work yourself if a specialist exists.
```

---

### 2. **Research Specialist** 🔬

Deep web research and data gathering expert.

| Property | Value |
|----------|-------|
| **Subagent Type** | `research-specialist` |
| **Invocation** | `Task(subagent_type='research-specialist', ...)` |
| **Output** | `tasks/{task_name}/refined_corpus.md` |

**Workflow:**
1. **Search & Discovery** — Execute parallel web/news searches via Composio
2. **Finalize Research** — Call `mcp__local_toolkit__finalize_research`
   - Reads all `search_results/*.json`
   - Crawls ALL URLs in parallel
   - Filters and deduplicates content
   - Creates refined corpus

**Tools Used:**
- `COMPOSIO_MULTI_EXECUTE_TOOL` — Parallel searches (max 4 tools per call)
- `finalize_research` — Automated crawl → filter → corpus pipeline

---

### 3. **Report Writer** 📝

Professional HTML report generation from research data.

| Property | Value |
|----------|-------|
| **Subagent Type** | `report-writer` or `report-creation-expert` |
| **Invocation** | `Task(subagent_type='report-creation-expert', ...)` |
| **Output** | `work_products/report.html` |

**5-Phase Workflow:**

| Phase | Action | Tool |
|-------|--------|------|
| 1. Planning | Create outline from corpus | `Write` (outline.json) |
| 2. Drafting | Generate all sections | `draft_report_parallel` |
| 3. Cleanup | Selective edits, dedup | `cleanup_report` |
| 4. Assembly | Compile final HTML | `compile_report` |
| 5. Completion | Return success message | — |

**Expected Skills:** `pdf`, `image-generation`

---

### 4. **Image Expert** 🎨

AI-powered image generation and manipulation.

| Property | Value |
|----------|-------|
| **Subagent Type** | `image-expert` |
| **Expected Skills** | `image-generation` |

---

### 5. **Video Creation Expert** 🎬

Video generation using MCP tools.

| Property | Value |
|----------|-------|
| **Subagent Type** | `video-creation-expert` |
| **Tools** | Composio MCP tools |

---

### 6. **Video Remotion Expert** 🎥

Programmatic video creation via Remotion framework.

| Property | Value |
|----------|-------|
| **Subagent Type** | `video-remotion-expert` |
| **Expected Skills** | `video-remotion` |

---

### 7. **Browserbase Agent** 🌐

Web automation and browser-based interactions.

| Property | Value |
|----------|-------|
| **Subagent Type** | `browserbase` |
| **Tools** | Composio MCP browser tools |

---

## 🚪 Gateway API

The Gateway provides a unified interface for agent execution across different deployment modes.

### Gateway Types

| Type | Class | Use Case |
|------|-------|----------|
| **In-Process** | `InProcessGateway` | Local CLI, testing |
| **External** | `ExternalGateway` | Remote server, distributed |

### Core Data Types

```python
@dataclass
class GatewaySession:
    session_id: str
    user_id: str
    workspace_dir: str
    metadata: dict[str, Any]

@dataclass
class GatewayRequest:
    user_input: str
    force_complex: bool = False
    metadata: dict[str, Any]

@dataclass
class GatewayResult:
    response_text: str
    tool_calls: int
    trace_id: Optional[str]
    metadata: dict[str, Any]
```

### Gateway Methods

```python
class Gateway:
    async def create_session(user_id, workspace_dir) -> GatewaySession
    async def resume_session(session_id) -> GatewaySession
    async def execute(session, request) -> AsyncIterator[AgentEvent]
    async def run_query(session, request) -> GatewayResult
    def list_sessions() -> list[GatewaySessionSummary]
```

---

## 📡 Event Stream

Agents emit events during execution for real-time UI updates.

### Event Types

| Event | Description |
|-------|-------------|
| `SESSION_INFO` | Session metadata on start |
| `STATUS` | Processing status updates |
| `TEXT` | LLM text output chunks |
| `THINKING` | Claude's reasoning (first 500 chars) |
| `TOOL_CALL` | Tool invocation with name/input |
| `TOOL_RESULT` | Tool execution result |
| `WORK_PRODUCT` | File/artifact produced |
| `AUTH_REQUIRED` | OAuth/auth link needed |
| `ITERATION_END` | Marks end of agent loop iteration |
| `ERROR` | Error information |

### URW-Specific Events

| Event | Description |
|-------|-------------|
| `URW_PHASE_START` | Phase execution beginning |
| `URW_PHASE_COMPLETE` | Phase finished successfully |
| `URW_PHASE_FAILED` | Phase failed |
| `URW_EVALUATION` | Evaluation result |

---

## 🔧 Tool Namespaces

| Namespace | Description |
|-----------|-------------|
| `mcp__composio__*` | Composio tools (Gmail, Slack, Search, etc.) |
| `mcp__local_toolkit__*` | Local tools (file I/O, research, image gen) |
| Native SDK | `Read`, `Write`, `Bash`, `Task`, `TodoWrite` |

### Key Local Tools

| Tool | Purpose |
|------|---------|
| `finalize_research` | Search → Crawl → Filter → Corpus |
| `draft_report_parallel` | Generate report sections in parallel |
| `cleanup_report` | Selective edits and deduplication |
| `compile_report` | Assemble final HTML report |
| `crawl_parallel` | Parallel URL crawling |
| `batch_tool_execute` | Batch multiple tool calls (up to 20) |

---

## 🎭 URW Integration

The **Universal Ralph Wrapper (URW)** enables complex multi-phase task execution.

### Adapters

| Adapter | Class | Description |
|---------|-------|-------------|
| Universal Agent | `UniversalAgentAdapter` | Direct agent execution |
| Gateway | `GatewayURWAdapter` | Routes through Gateway API |
| Mock | `MockAgentAdapter` | Testing without real agent |

### Factory Usage

```python
from universal_agent.urw.integration import create_adapter_for_system

# Options: "universal_agent", "gateway", "mock"
adapter = create_adapter_for_system("gateway", {
    "gateway_url": "http://localhost:8000",  # Optional for external
    "verbose": True,
})
```

### HarnessOrchestrator

Orchestrates multi-phase execution with:
- Phase-based task decomposition
- Context accumulation across phases
- Evaluation and replanning
- Workspace isolation per phase

---

## 🧠 Memory Systems

### Letta Memory (Optional)

Long-term memory for sub-agents:
- Captures sub-agent prompts and results
- Injects relevant context into future runs
- Per-subagent memory isolation

### Context Summarizer

Manages context across phases:
- Tracks tool calls and results
- Preserves sub-agent outputs
- Accumulates learnings and failed approaches

---

## 🚀 Quick Start

### CLI Mode

```bash
./start_terminal.sh
```

This starts:
1. Agent College sidecar (port 8001)
2. Universal Agent CLI (interactive)

### Direct CLI

```bash
PYTHONPATH=src uv run python -m universal_agent.main
```

### API Mode

```bash
PYTHONPATH=src uv run uvicorn universal_agent.api:app --port 8000
```

### Gateway Usage

```python
from universal_agent.gateway import InProcessGateway, GatewayRequest

async def main():
    gateway = InProcessGateway()
    session = await gateway.create_session(user_id="user_123")
    
    request = GatewayRequest(user_input="Research AI trends")
    
    async for event in gateway.execute(session, request):
        print(f"{event.type}: {event.data}")
```

---

## 📊 Execution Flow

```
User Request
     │
     ▼
┌─────────────┐
│ Coordinator │
└──────┬──────┘
       │
       ├── Simple Query? ──────────▶ Direct Response
       │
       ├── Research Needed? ───────▶ Task(subagent_type='research-specialist')
       │                                    │
       │                                    ▼
       │                            Search → Crawl → Corpus
       │                                    │
       │                                    ▼
       │                            Return to Coordinator
       │
       ├── Report Needed? ─────────▶ Task(subagent_type='report-creation-expert')
       │                                    │
       │                                    ▼
       │                            Plan → Draft → Cleanup → Compile
       │                                    │
       │                                    ▼
       │                            Return report.html
       │
       └── Specialized Task? ──────▶ Task(subagent_type='[specialist]')
```

---

## 🧪 Testing

```bash
# Run all gateway tests
pytest tests/test_gateway*.py -v

# Run with markers
pytest -m "not slow" tests/
pytest -m integration tests/
```

See [`tests/README.md`](tests/README.md) for full testing documentation.

---

## 📁 Key Files

| File | Purpose |
|------|---------|
| `src/universal_agent/agent_core.py` | Core `UniversalAgent` class |
| `src/universal_agent/gateway.py` | Gateway API implementation |
| `src/universal_agent/agent_setup.py` | Unified agent initialization |
| `src/universal_agent/main.py` | CLI entry point and hooks |
| `src/universal_agent/urw/integration.py` | URW adapters |
| `src/universal_agent/urw/harness_orchestrator.py` | Multi-phase orchestration |

---

## 🔐 Environment Variables

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude API access |
| `COMPOSIO_API_KEY` | Composio tool access |
| `COMPOSIO_USER_ID` | User identification |
| `USER_TIMEZONE` | Temporal context (default: America/Chicago) |
| `UA_DISABLE_LOGFIRE` | Disable Logfire telemetry |
| `UA_BATCH_MAX_WORDS` | Max words for batch reading |

---

<p align="center">
  <strong>Built with 🧠 Claude • 🔧 MCP • ⚡ Composio</strong>
</p>
