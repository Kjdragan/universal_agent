# Logfire Tracing & Observability

Distributed tracing for the Universal Agent using [Pydantic Logfire](https://logfire.pydantic.dev/) (OpenTelemetry-based). All runs emit structured spans that can be queried via the Logfire MCP or the Logfire web UI.

## Configuration

Logfire is configured in `main.py` (`setup_session`):

```python
logfire.configure(service_name="universal-agent")
logfire.instrument_mcp()
logfire.instrument_httpx()
logfire.instrument_anthropic()
```

**Required env vars:**
- `LOGFIRE_TOKEN` — Logfire write token (in `.env`)
- `LOGFIRE_WRITE_TOKEN` — Alternative write token name

**Optional:**
- `LOGFIRE_PROJECT_SLUG` — Defaults to `Kjdragan/composio-claudemultiagent`

## Span Hierarchy

### CLI Runs (`ua_cli_session`)

```
ua_cli_session (run_id, workspace_dir)
├── conversation_iteration_1 (iteration, run_id, step_id, session_id, workspace_dir)
│   ├── llm_api_wait (query_length)
│   ├── llm_response_stream
│   │   ├── assistant_message (model)
│   │   │   ├── tool_use (tool_name, tool_id)
│   │   │   │   └── tool_input
│   │   │   └── text_block
│   │   ├── tool_result
│   │   │   ├── tool_output
│   │   │   ├── observer_search_results
│   │   │   ├── observer_work_products
│   │   │   └── observer_workbench_activity
│   │   └── skill_gated
│   └── Message with claude-sonnet-4-20250514 (Anthropic auto-instrumented)
├── conversation_iteration_2
│   └── ...
├── query_classification (decision, raw_response)
├── token_usage_update
└── POST https://api.anthropic.com/... (HTTPX auto-instrumented)
```

### Gateway Runs (`gateway_request`)

Gateway runs (from Web UI or heartbeat service) are wrapped in a `gateway_request` span created by `ProcessTurnAdapter.execute()` in `execution_engine.py`. This fixes the trace ID N/A issue on the gateway path.

```
gateway_request (session_id, run_id, run_source)
└── conversation_iteration_1
    └── ... (same structure as CLI)
```

### Heartbeat Runs

Heartbeats are autonomous runs not directed by the user. They are wrapped in a `heartbeat_run` parent span and classified after execution:

```
heartbeat_run (session_id, run_source=heartbeat, wake_reason)
├── gateway_request (session_id, run_id, run_source=heartbeat)
│   └── conversation_iteration_1
│       └── ... (full auto-instrumented span tree)
└── heartbeat_significant OR heartbeat_ok (classification info log)
```

**Classification markers:**
- `heartbeat_significant` — The heartbeat did actual work (used tools, wrote files)
- `heartbeat_ok` — No-op check-in (responded with HEARTBEAT_OK)

**Key attribute:** `run_source=heartbeat` on all heartbeat spans.

## Trace Catalog

After each run, a **Trace Catalog** is emitted to:
1. **stdout / run.log** — Structured block with all trace IDs, descriptions, and query hints
2. **trace.json** — Under the `trace_catalog` key
3. **trace_catalog.md** — Standalone markdown file in the workspace

The catalog contains:
- Main agent trace ID + Logfire URL
- Local toolkit trace IDs (MCP tool server spans)
- Run ID and run source
- Analysis guide with SQL query suggestions
- `[HEARTBEAT]` flag if this was an autonomous heartbeat run

### Finding trace artifacts

```
{workspace}/trace.json          # Full trace data + trace_catalog
{workspace}/trace_catalog.md    # Standalone catalog for agent discovery
{workspace}/work_products/logfire-eval/trace_catalog.md    # Preferred skill input
{workspace}/work_products/logfire-eval/trace_catalog.json  # Machine-readable catalog
{workspace}/run.log             # Contains the printed trace catalog block
```

## Querying Traces

### By run_id (preferred)

```sql
SELECT * FROM records WHERE attributes->>'run_id' = '{RUN_ID}'
```

### By trace_id

```sql
SELECT * FROM records WHERE trace_id = '{TRACE_ID}'
```

### Find recent runs

```sql
SELECT DISTINCT attributes->>'run_id' as run_id, trace_id,
       MIN(start_timestamp) as started, COUNT(*) as spans
FROM records
WHERE attributes->>'run_id' IS NOT NULL
  AND start_timestamp > NOW() - INTERVAL '24 hours'
GROUP BY 1, 2
ORDER BY MIN(start_timestamp) DESC
LIMIT 5
```

### Find significant heartbeats

```sql
SELECT trace_id, attributes->>'session_id' as session,
       attributes->>'wake_reason' as reason,
       start_timestamp
FROM records
WHERE span_name = 'heartbeat_significant'
  AND start_timestamp > NOW() - INTERVAL '48 hours'
ORDER BY start_timestamp DESC
```

## Logfire Eval Skill

A dedicated skill at `.claude/skills/logfire-eval/` teaches agents how to analyze runs:

**Trigger phrases:** "analyze the run", "trace analysis", "logfire eval", "check the traces", "what happened in that heartbeat"

**Analysis phases:**
1. **Discovery** — Find run_id and trace_ids from workspace or Logfire
2. **Health Check** — Span summary, exception count, duration
3. **Exception Deep-Dive** — Classify and cross-reference exceptions
4. **Performance Bottlenecks** — Top slow spans (skip containers)
5. **Tool Execution** — Tool call inventory, success/failure rates
6. **Pipeline Analysis** — Corpus refinement, sub-agent patterns
7. **Token & Cost** — Token usage per iteration, model breakdown
8. **Heartbeat Analysis** — Find significant heartbeats, classify outcomes
9. **Report Generation** — Save `logfire_evaluation.md` to workspace

**Key rule:** Always query by `run_id` or `trace_id`, never by time period alone.

**Reference files:**
- `.claude/skills/logfire-eval/references/sql_queries.md` — Parameterized SQL queries
- `.claude/skills/logfire-eval/references/span_catalog.md` — Every span type documented

## Files Involved

| File | Role |
|------|------|
| `main.py` | Logfire configuration, root CLI span, iteration spans, trace catalog emission |
| `execution_engine.py` | `gateway_request` root span for gateway runs, trace_id extraction |
| `gateway.py` | Propagates `run_source` from request metadata to adapter |
| `gateway_server.py` | FastAPI auto-instrumentation via `logfire.instrument_fastapi()` |
| `heartbeat_service.py` | `heartbeat_run` parent span, `heartbeat_significant`/`heartbeat_ok` markers |
| `trace_catalog.py` | `emit_trace_catalog()`, `save_trace_catalog_md()`, `enrich_trace_json()` |
| `trace_utils.py` | `write_trace()` — saves trace.json |
| `hooks.py` | Various `logfire.info()` / `logfire.warning()` calls for hook events |
