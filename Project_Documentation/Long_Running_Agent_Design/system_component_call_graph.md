# Universal Agent System Call Graphs

This document provides code-level call graphs per component group.

## (A) Repo Structure Overview (Entry Points)

Script and service entrypoints:

```
start.sh
  -> uvicorn AgentCollege.logfire_fetch.main:app
  -> uvicorn universal_agent.bot.main:app

start_local.sh (cli)
  -> python -m universal_agent.main

start_local.sh (bot)
  -> uvicorn universal_agent.bot.main:app

start_local.sh (worker)
  -> uvicorn AgentCollege.logfire_fetch.main:app
```

Runtime roots:

- `src/universal_agent/main.py` (CLI)
- `src/universal_agent/server.py` (WebSocket API)
- `src/universal_agent/bot/main.py` (Telegram webhook server)
- `src/web/server.py` (Web UI subprocess bridge)
- `src/mcp_server.py` (local MCP tool server)
- `AgentCollege/logfire_fetch/main.py` (Logfire webhook service)

## (B) Orchestrator / Main Agent Loop

### CLI (Claude Agent SDK loop)

```
main()
  -> setup_session()
     -> Composio.create()
     -> ClaudeAgentOptions(...)
     -> mcp_servers wiring
  -> ClaudeSDKClient(options)
  -> prompt loop
     -> process_turn()
        -> classify_query()
        -> handle_simple_query() OR run_conversation()
           -> client.query()
           -> client.receive_response()
              -> ToolUseBlock / ToolResultBlock
              -> observers (search/workbench/work_products/video)
```

### WebSocket API (agent class)

```
websocket_endpoint() [src/universal_agent/server.py]
  -> UniversalAgent.initialize()
  -> UniversalAgent.run_query()
     -> UniversalAgent._run_conversation()
        -> ClaudeSDKClient.query()
        -> ClaudeSDKClient.receive_response()
        -> AgentEvent streaming
```

### Telegram bot actor/queue

```
lifespan() [src/universal_agent/bot/main.py]
  -> AgentAdapter.initialize()
  -> TaskManager.worker()
     -> AgentAdapter.execute()
        -> AgentAdapter._client_actor_loop()
           -> ClaudeSDKClient(options)
           -> process_turn()
```

### Web UI subprocess bridge

```
AgentBridge.execute_query() [src/web/server.py]
  -> asyncio.create_subprocess_exec("PYTHONPATH=src uv run python -m universal_agent.main")
  -> write query to stdin
  -> parse stdout for tool calls/results
```

## (C) Tool Execution Gateway / Wrappers

### Tool loading and session wiring

```
setup_session() [src/universal_agent/main.py]
  -> Composio.create()
  -> ClaudeAgentOptions.mcp_servers = {composio, local_toolkit, external_mcps}

UniversalAgent.initialize() [src/universal_agent/agent_core.py]
  -> Composio.create()
  -> ClaudeAgentOptions.mcp_servers = {composio, local_toolkit}
```

### Tool execution and result flow

```
run_conversation() / _run_conversation()
  -> ClaudeSDKClient.receive_response()
     -> ToolUseBlock -> trace["tool_calls"]
     -> ToolResultBlock -> trace["tool_results"]
     -> observers (save artifacts)
```

### Local MCP tool server

```
__main__ [src/mcp_server.py]
  -> mcp.run(transport="stdio")
     -> @mcp.tool() functions
        - read_local_file
        - write_local_file
        - crawl_parallel / finalize_research
        - upload_to_composio
        - memory tools
```

### Workbench wrappers

```
WorkbenchBridge.download()
  -> Composio.tools.execute("CODEINTERPRETER_GET_FILE_CMD")

WorkbenchBridge.upload()
  -> Composio.tools.execute("COMPOSIO_REMOTE_WORKBENCH")
```

## (D) Memory System Implementation

### Prompt injection path

```
setup_session() [src/universal_agent/main.py]
  -> MemoryManager(storage_dir=Memory_System_Data)
     -> StorageManager.__init__()
        -> _init_sqlite()
        -> chromadb.PersistentClient()
  -> MemoryManager.get_system_prompt_addition()
  -> ClaudeAgentOptions.system_prompt += memory context
```

### Tool exposure (MCP)

```
core_memory_replace() [src/mcp_server.py]
  -> MemoryManager.core_memory_replace()
     -> StorageManager.save_block()

archival_memory_search()
  -> MemoryManager.archival_memory_search()
     -> StorageManager.search_archival() -> ChromaDB query
```

### Agent College memory usage

```
runner.py
  -> LogfireReader.get_failures()
  -> MemoryManager.has_trace_been_processed()
  -> CriticAgent.propose_correction()
  -> MemoryManager.mark_trace_processed()
```

## (E) Scheduling / Triggers / Webhooks

### Telegram webhook

```
/app.post("/webhook") [src/universal_agent/bot/main.py]
  -> ptb_app.process_update()
     -> telegram_handlers.agent_command()
        -> TaskManager.add_task()
```

### Task queue worker

```
TaskManager.worker()
  -> AgentAdapter.execute()
     -> AgentAdapter._client_actor_loop()
        -> process_turn()
```

### Logfire polling worker

```
agent_college/runner.py
  -> LogfireReader.get_failures()
  -> CriticAgent.propose_correction()
  -> MemoryManager.mark_trace_processed()
```

### Logfire webhook handler

```
/app.post("/webhook/alert") [AgentCollege/logfire_fetch/main.py]
  -> CriticAgent.propose_correction()
```

## (F) Observability / Tracing

### Logfire setup and spans (CLI path)

```
configure_logfire() [src/universal_agent/main.py]
  -> logfire.configure()
  -> logfire.instrument_mcp()
  -> logfire.instrument_httpx()
  -> logfire.instrument_anthropic()

run_conversation()
  -> logfire.span("conversation_iteration_*" )
  -> logfire.span("assistant_message")
  -> logfire.span("tool_use")
  -> logfire.span("tool_result")
```

### Logfire setup (API path)

```
configure_logfire() [src/universal_agent/agent_core.py]
  -> logfire.configure()
  -> logfire.instrument_mcp()
  -> logfire.instrument_httpx()
```

### Trace consumption and output

```
transcript_builder.generate_transcript()
  -> reads trace.json
  -> writes transcript.md

telegram_formatter.format_telegram_response()
  -> builds Logfire URL from trace_id
```

### MCP server tracing

```
logfire.configure() [src/mcp_server.py]
  -> logfire.instrument_mcp()
```
