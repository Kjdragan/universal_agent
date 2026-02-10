# Trace Catalog

| Field | Value |
|-------|-------|
| **Run ID** | `e30af97a-d9f1-47ac-9cc3-f9cd34850f33` |
| **Run Source** | `user` |
| **Service** | `universal-agent` |
| **Catalog Scope** | `main trace + local toolkit markers` |
| **Logfire Project** | `Kjdragan/composio-claudemultiagent` |

## 1. Main Agent Trace
- **Trace ID**: `019c4818b530118a891c32d959094eb1`
- **Logfire URL**: [019c4818b530118a891c32d959094eb1](https://logfire.pydantic.dev/Kjdragan/composio-claudemultiagent?q=trace_id%3D%27019c4818b530118a891c32d959094eb1%27)
- **Description**: Primary execution trace with all agent spans

### Span Types
- `conversation_iteration_{N}` — Each LLM conversation turn
- `llm_api_wait` — Time waiting for Claude API
- `llm_response_stream` — Full response streaming
- `assistant_message` — Each assistant message processing
- `tool_use / tool_input` — Tool invocations + parameters
- `tool_result / tool_output` — Tool results
- `observer_*` — File/search/workbench observers
- `skill_gated` — Skill routing decisions
- `query_classification` — SIMPLE/COMPLEX routing
- `token_usage_update` — Token accounting
- `POST (HTTPX)` — Raw HTTP calls to LLM APIs

### Queries
```sql
-- All spans for this run
SELECT * FROM records WHERE trace_id = '019c4818b530118a891c32d959094eb1'
-- Or by run_id
SELECT * FROM records WHERE attributes->>'run_id' = 'e30af97a-d9f1-47ac-9cc3-f9cd34850f33'
```

## 2. Local Toolkit Trace IDs
- **Distinct Local Toolkit Trace Count**: 1
  - `019c48186f30d642d2ab956c629a02ff`

## Analysis Guide
1. Start with the **Main Agent Trace** — it has 99%+ of useful data
2. Check for exceptions: `WHERE is_exception = true`
3. Find bottlenecks: `ORDER BY duration DESC`
4. Tool timeline: `WHERE span_name = 'tool_use'`
5. Token usage: `WHERE span_name = 'token_usage_update'`

## Coverage Notes
- This catalog guarantees main trace coverage and local toolkit marker coverage.
- External tool traces without emitted trace markers may not be discoverable.