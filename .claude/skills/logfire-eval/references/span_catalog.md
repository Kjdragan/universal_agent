# Universal Agent Span Catalog

Every span type emitted by the Universal Agent, organized by source.

## Root Spans

| Span Name | Source | Description | Key Attributes |
|-----------|--------|-------------|----------------|
| `ua_cli_session` | `main.py` | Root span for CLI-initiated runs | `run_id`, `workspace_dir` |
| `gateway_request` | `execution_engine.py` | Root span for gateway-initiated runs (web UI, heartbeat) | `session_id`, `run_id`, `run_source` |
| `heartbeat_run` | `heartbeat_service.py` | Parent span wrapping entire heartbeat execution | `session_id`, `run_source=heartbeat`, `wake_reason` |

## Conversation Spans

| Span Name | Source | Description | Key Attributes |
|-----------|--------|-------------|----------------|
| `conversation_iteration_{N}` | `main.py` | One LLM conversation turn (N = iteration number) | `iteration`, `run_id`, `step_id`, `session_id`, `workspace_dir` |
| `llm_api_wait` | `main.py` | Time waiting for Claude API to start responding | `query_length`, `run_id`, `step_id`, `iteration` |
| `llm_response_stream` | `main.py` | Full response stream processing duration | `run_id`, `step_id`, `iteration` |

## Message Spans

| Span Name | Source | Description | Key Attributes |
|-----------|--------|-------------|----------------|
| `assistant_message` | `main.py` | Each assistant message block | `model`, `run_id`, `step_id`, `iteration`, `parent_tool_use_id` |
| `text_block` | `main.py` | Text content block within a message | `length`, `text_preview`, `run_id`, `step_id`, `iteration`, optional `text_full*` |
| `result_message` | `main.py` | Final result message | `duration_ms`, `num_turns`, `run_id`, `step_id`, `iteration`, optional `result_full*` |

## Tool Spans

| Span Name | Source | Description | Key Attributes |
|-----------|--------|-------------|----------------|
| `tool_use` | `main.py` | Tool invocation | `tool_name`, `tool_id`, `run_id`, `step_id`, `iteration` |
| `tool_input` | `main.py` | Tool input parameters (child of tool_use) | `tool_name`, `tool_id`, `input_size`, `input_preview`, optional `input_full*` |
| `tool_result` | `main.py` | Tool result processing | `tool_use_id`, `run_id`, `step_id`, `iteration` |
| `tool_output` | `main.py` | Tool output content (child of tool_result) | `tool_use_id`, `content_size`, `content_preview`, optional `content_full*` |
| `tool_execution_completed` | `main.py` | Tool completion event | `tool_name`, `duration_seconds`, `status`, `source` (`post_tool_use_hook` or `stream_tool_result`) |

## Observer Spans

| Span Name | Source | Description | Key Attributes |
|-----------|--------|-------------|----------------|
| `observer_search_results` | observers | Search result monitoring | — |
| `observer_work_products` | observers | Work product file monitoring | — |
| `observer_workbench_activity` | observers | Code execution / workbench activity | — |
| `observer_video_outputs` | observers | Video output monitoring | — |
| `observer_artifact_saved` | observers | Artifact save event | — |

## Classification & Routing Spans

| Span Name | Source | Description | Key Attributes |
|-----------|--------|-------------|----------------|
| `query_started` | `main.py` | Run turn start marker and payload logging mode state | `run_id`, `step_id`, `payload_full_mode_enabled`, `payload_redact_sensitive`, `payload_redact_emails`, `payload_max_chars` |
| `query_classification` | `main.py` | SIMPLE/COMPLEX query routing decision | `decision`, `raw_response` |
| `skill_gated` | `main.py` | Skill routing decision | — |

## Token & Cost Spans

| Span Name | Source | Description | Key Attributes |
|-----------|--------|-------------|----------------|
| `token_usage_update` | `main.py` | Token accounting per iteration | `run_id`, `step_id`, `iteration` |

## Durable Execution Spans

| Span Name | Source | Description | Key Attributes |
|-----------|--------|-------------|----------------|
| `ledger_mark_succeeded` | `main.py` | Durable step completion | — |
| `durable_checkpoint_saved` | `main.py` | Recovery checkpoint saved | — |
| `checkpoint_save_failed` | `main.py` | Checkpoint save failure | — |

## Pipeline Spans

| Span Name | Source | Description | Key Attributes |
|-----------|--------|-------------|----------------|
| `corpus_refiner.refine_corpus` | `corpus_refiner.py` | Full corpus refinement pipeline | — |
| `corpus_refiner.extract_batch` | `corpus_refiner.py` | LLM extraction batch | — |

## Sub-Agent Spans

| Span Name | Source | Description | Key Attributes |
|-----------|--------|-------------|----------------|
| `subagent_output_persisted` | `main.py` | Sub-agent delegation output saved | — |

## Heartbeat Classification (Info Logs)

| Span Name | Source | Description | Key Attributes |
|-----------|--------|-------------|----------------|
| `heartbeat_significant` | `heartbeat_service.py` | Classification: heartbeat did actual work | `session_id`, `run_source`, `tools_used`, `artifacts_written`, `response_summary` |
| `heartbeat_ok` | `heartbeat_service.py` | Classification: heartbeat was a no-op check-in | `session_id`, `run_source` |

## Hook Spans (Info Logs)

| Span Name | Source | Description | Key Attributes |
|-----------|--------|-------------|----------------|
| `agent_stop_hook` | `hooks.py` | Agent stop signal detected | — |
| `workspace_guard_blocked` | `hooks.py` | Tool blocked by workspace guard | — |
| `skill_hint_injected` | `hooks.py` | Skill hint added to system prompt | — |

## Auto-Instrumented Spans

These are created by Logfire's auto-instrumentation libraries, not by UA code directly:

| Span Name Pattern | Source | Description |
|-------------------|--------|-------------|
| `POST` / `GET` | HTTPX auto-instrumentation | HTTP API calls (LLM endpoints, etc.) | 
| `Message with {model}` | Anthropic SDK auto-instrumentation | Claude API conversation turn |
| MCP tool spans | MCP auto-instrumentation | MCP server tool calls |

## Span Hierarchy (Typical CLI Run)

```
ua_cli_session
├── conversation_iteration_1
│   ├── llm_api_wait
│   ├── llm_response_stream
│   │   ├── assistant_message
│   │   │   ├── tool_use (tool_name, tool_id)
│   │   │   │   └── tool_input
│   │   │   └── text_block
│   │   ├── tool_result
│   │   │   ├── tool_output
│   │   │   ├── observer_search_results
│   │   │   ├── observer_work_products
│   │   │   └── observer_workbench_activity
│   │   └── skill_gated
│   └── Message with claude-sonnet-4-20250514 (Anthropic auto)
├── conversation_iteration_2
│   └── ...
├── query_classification
├── token_usage_update
└── POST https://api.anthropic.com/... (HTTPX auto)
```

## Span Hierarchy (Gateway/Heartbeat Run)

```
heartbeat_run (session_id, wake_reason, run_source=heartbeat)
├── gateway_request (session_id, run_id, run_source=heartbeat)
│   └── conversation_iteration_1
│       └── ... (same structure as CLI)
└── heartbeat_significant OR heartbeat_ok (info log)
```
