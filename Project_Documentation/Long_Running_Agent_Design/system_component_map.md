# Universal Agent System Component Map

This document maps repository files to system components and describes purpose, wiring, and persistence.

## (A) Repo Structure Overview

Top-level folders and purpose (focus on src, memory, tools, bot/IO, scripts):

- `src/` - primary runtime code (agent core, bot, MCP server, web server).
- `src/universal_agent/` - main agent logic, observers, websocket API, Telegram bot, Agent College integration.
- `src/mcp_server.py` - local MCP tool server (file IO, crawl, memory, uploads).
- `src/tools/` - Composio workbench bridge wrappers for file transfer.
- `src/web/` - web UI backend that spawns the CLI agent and parses stdout.
- `Memory_System/` - memory subsystem code (SQLite + Chroma, tools, models).
- `Memory_System_Data/` - persistent memory store (SQLite `agent_core.db`, Chroma `chroma_db/`).
- `AGENT_RUN_WORKSPACES/` - per-session workspaces (run.log, trace.json, search_results/, work_products/).
- `external_mcps/` - bundled external MCP servers (video/audio MCP).
- `AgentCollege/` - LogfireFetch sidecar (polling/webhook service).
- `Project_Documentation/`, `AI_DOCS/` - architecture docs and runbooks.
- `static/`, `universal_agent_ui.html` - web UI assets.
- Scripts: `start.sh`, `start_local.sh`, `run_local.sh`, `start_telegram_bot.sh`, `register_webhook.py`.

## (B) Orchestrator / Main Agent Loop

### CLI agent loop
- `src/universal_agent/main.py`
  - Key functions: `main()`, `setup_session()`, `process_turn()`, `run_conversation()`, `classify_query()`, `handle_simple_query()`.
  - Wiring/call chain:
    - `main()` -> `setup_session()` -> create Composio session -> build `ClaudeAgentOptions` -> `ClaudeSDKClient`.
    - Loop: prompt -> `process_turn()` -> `classify_query()` -> `handle_simple_query()` or `run_conversation()`.
    - `run_conversation()` consumes `ClaudeSDKClient.receive_response()` and captures `ToolUseBlock` / `ToolResultBlock`.
  - Persistence/state:
    - Writes `AGENT_RUN_WORKSPACES/session_*/run.log`, `trace.json`, `session_summary.txt`, `transcript.md`.

### WebSocket API agent loop
- `src/universal_agent/agent_core.py`
  - Key classes/functions: `UniversalAgent`, `AgentEvent`, `configure_logfire()`, `UniversalAgent.run_query()`, `UniversalAgent._run_conversation()`.
  - Wiring/call chain: `run_query()` -> `_run_conversation()` -> `ClaudeSDKClient` tool loop -> emit events.
  - Persistence/state: saves `AGENT_RUN_WORKSPACES/session_*/trace.json`; observers save artifacts.

### WebSocket server
- `src/universal_agent/server.py`
  - WebSocket handler `websocket_endpoint()` creates `UniversalAgent`, calls `initialize()` and `run_query()`.
  - Also exposes workspace browsing endpoints.

### Telegram bot loop
- `src/universal_agent/bot/main.py`
  - `lifespan()` initializes `AgentAdapter`, registers webhook, starts worker loop.
- `src/universal_agent/bot/task_manager.py`
  - `TaskManager.worker()` drains `asyncio.Queue`, calls `AgentAdapter.execute()`.
- `src/universal_agent/bot/agent_adapter.py`
  - `_client_actor_loop()` keeps `ClaudeSDKClient` open; `execute()` pushes requests and waits for results.

### Web UI subprocess loop
- `src/web/server.py`
  - `AgentBridge.execute_query()` spawns `uv run src/universal_agent/main.py` and parses stdout.

## (C) Tool Execution Gateway / Wrappers

### Tool loading
- `src/universal_agent/main.py` and `src/universal_agent/agent_core.py`
  - `ClaudeAgentOptions.mcp_servers` includes:
    - `composio` (HTTP endpoint `session.mcp.url`).
    - `local_toolkit` (stdio, `src/mcp_server.py`).
    - External MCPs (`edgartools`, `video_audio`, `youtube`, `zai_vision`).

### Tool execution and results
- `run_conversation()` / `_run_conversation()` reads `ToolUseBlock` and `ToolResultBlock` from SDK responses.
- Tool calls/results are recorded into `trace.json` and trigger observers.

### Local MCP tool server
- `src/mcp_server.py`
  - Defines `@mcp.tool()` functions for local file IO, crawl pipeline, memory tools, and uploads.
  - Runs via `mcp.run(transport="stdio")`.

### Workbench wrappers
- `src/tools/workbench_bridge.py`
  - `download()` uses `CODEINTERPRETER_GET_FILE_CMD`.
  - `upload()` uses `COMPOSIO_REMOTE_WORKBENCH` with a generated Python script.

### Error handling / retry / dedupe
- `_crawl_core()` uses HTTP timeout for crawl calls.
- `finalize_research()` dedupes URLs with a `set`.
- `AgentAdapter.execute()` enforces a 300s timeout and reinitializes dead workers.
- `bot/main.py` retries webhook registration and notification sends.
- No explicit rate limiting found.

### Persistence/state
- Tool calls/results saved to `AGENT_RUN_WORKSPACES/session_*/trace.json`.
- Crawl outputs saved under `AGENT_RUN_WORKSPACES/session_*/search_results/`.

## (D) Memory System Implementation

### Storage and schema
- `Memory_System/storage.py`
  - SQLite tables in `agent_core.db`: `core_blocks` and `processed_traces`.
  - ChromaDB persistent collection `archival_memory` in `chroma_db/`.

### Manager and retrieval
- `Memory_System/manager.py`
  - `get_system_prompt_addition()` formats core memory for prompt injection.
  - `archival_memory_search()` uses Chroma semantic search.

### Tool exposure
- `Memory_System/tools.py` provides tool mapping.
- `src/mcp_server.py` exposes memory tools: `core_memory_replace`, `core_memory_append`, `archival_memory_insert`, `archival_memory_search`, `get_core_memory_blocks`.

### Context injection
- `src/universal_agent/main.py` `setup_session()` creates `MemoryManager` and injects `memory_context_str` into the system prompt.
- `src/universal_agent/agent_college/integration.py` ensures the Agent College sandbox memory block exists.

### Persistence/state
- Default: `Memory_System/data/` and `Memory_System_Data/` (configured in `main.py`).
- SQLite `agent_core.db` and Chroma `chroma_db/` are long-term state.

## (E) Scheduling / Triggers / Webhooks

### Telegram webhooks
- `src/universal_agent/bot/main.py`
  - `@app.post("/webhook")` processes Telegram updates.
  - Webhook registration in `lifespan()` with retries.
- `register_webhook.py` and `start_telegram_bot.sh` are helper scripts.

### Background worker loops
- `src/universal_agent/bot/task_manager.py` `worker()` loop processes tasks from an `asyncio.Queue`.
- `src/universal_agent/bot/agent_adapter.py` maintains a background actor loop.

### Agent College polling and webhook
- `src/universal_agent/agent_college/runner.py` polls Logfire for failures every 60s.
- `AgentCollege/logfire_fetch/main.py` exposes `/webhook/alert` for Logfire alert pushes.

### Persistence/state
- Queue/task state is in memory; processed trace IDs stored in SQLite (`processed_traces`).
- Telegram bot writes per-task logs into session workspaces.

## (F) Observability / Tracing

### Logfire setup
- `src/universal_agent/main.py` and `src/universal_agent/agent_core.py`
  - `logfire.configure()` and `logfire.instrument_*()`.
- `src/mcp_server.py` instruments MCP tool server (separate trace IDs).

### Span creation and propagation
- `src/universal_agent/main.py` wraps iterations and tool calls in spans:
  - `conversation_iteration_*`, `assistant_message`, `tool_use`, `tool_result`.
  - Uses Logfire baggage fields (`agent`, `is_subagent`, `loop`, `step`).
- `src/universal_agent/agent_core.py` records tool calls/results into `trace.json`.

### Trace consumption
- `src/universal_agent/transcript_builder.py` generates `transcript.md` from `trace.json` and embeds Logfire URL.
- `src/universal_agent/bot/telegram_formatter.py` includes a Logfire trace link.
- Logfire queries in `src/universal_agent/agent_college/logfire_reader.py` and `AgentCollege/logfire_fetch/logfire_reader.py`.

### Persistence/state
- `AGENT_RUN_WORKSPACES/session_*/trace.json` and `transcript.md`.
- Logfire trace IDs stored in `trace.json`; local MCP traces are separate due to stdio transport.
