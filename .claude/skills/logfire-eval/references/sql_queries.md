# Logfire SQL Query Reference

All queries use the Logfire MCP `arbitrary_query` tool. The database engine is Apache DataFusion (Postgres-like SQL syntax).

## Key Tables

- `records` — Contains all spans and logs (the only table you need)

## Key Columns

| Column | Type | Description |
|--------|------|-------------|
| `trace_id` | TEXT | Unique trace identifier |
| `span_name` | TEXT | Name of the span |
| `message` | TEXT | Human-readable message |
| `start_timestamp` | TIMESTAMP | When the span started |
| `end_timestamp` | TIMESTAMP | When the span ended |
| `duration` | FLOAT | Duration in seconds |
| `is_exception` | BOOLEAN | Whether this span recorded an exception |
| `exception_type` | TEXT | Exception class name |
| `exception_message` | TEXT | Exception message |
| `attributes` | JSON | Key-value attributes (use `->>'key'` to access) |
| `service_name` | TEXT | Service that emitted the span |

## Attribute Access

Use `->>'key'` for string values, `->'key'` for JSON:
```sql
attributes->>'run_id'        -- UUID string
attributes->>'session_id'    -- session directory name
attributes->>'run_source'    -- "user" or "heartbeat"
attributes->>'tool_name'     -- tool name in tool_use spans
attributes->>'wake_reason'   -- heartbeat trigger reason
(attributes->'cost')::float  -- numeric values need casting
```

## Efficient Filters

These columns have indexes and should be used when possible:
- `start_timestamp` — Always include a time bound
- `service_name` — Use `'universal-agent'`
- `span_name` — Filter by span type
- `trace_id` — Filter by specific trace
- `attributes->>'run_id'` — Filter by run

## Query Catalog by Phase

### Discovery — Find Recent Runs
```sql
SELECT DISTINCT attributes->>'run_id' as run_id, trace_id,
       MIN(start_timestamp) as started, MAX(end_timestamp) as ended,
       COUNT(*) as span_count
FROM records
WHERE attributes->>'run_id' IS NOT NULL
  AND start_timestamp > NOW() - INTERVAL '24 hours'
GROUP BY 1, 2
ORDER BY MIN(start_timestamp) DESC
LIMIT 5
```
**Age parameter**: 1440 (24 hours)

### Health Check — Span Summary
```sql
SELECT span_name, COUNT(*) as count,
       ROUND(AVG(duration)::numeric, 3) as avg_dur_sec,
       ROUND(MAX(duration)::numeric, 3) as max_dur_sec,
       BOOL_OR(is_exception) as has_exceptions
FROM records
WHERE attributes->>'run_id' = '{RUN_ID}'
GROUP BY span_name
ORDER BY count DESC
LIMIT 25
```

### Exceptions
```sql
SELECT span_name, exception_type, exception_message, start_timestamp
FROM records
WHERE attributes->>'run_id' = '{RUN_ID}'
  AND is_exception = true
LIMIT 20
```

### Bottlenecks (skip container spans)
```sql
SELECT span_name, message, ROUND(duration::numeric, 2) as duration_sec
FROM records
WHERE attributes->>'run_id' = '{RUN_ID}'
  AND duration > 1.0
  AND span_name NOT IN ('llm_response_stream', 'ua_cli_session', 'gateway_request')
  AND span_name NOT LIKE 'conversation_iteration_%'
ORDER BY duration DESC
LIMIT 15
```

### Tool Execution
```sql
SELECT attributes->>'tool_name' as tool,
       COUNT(*) as calls,
       ROUND(AVG(duration)::numeric, 3) as avg_dur,
       BOOL_OR(is_exception) as has_errors
FROM records
WHERE attributes->>'run_id' = '{RUN_ID}'
  AND span_name = 'tool_use'
GROUP BY 1
ORDER BY calls DESC
```

### Pipeline Phases
```sql
SELECT span_name, message, ROUND(duration::numeric, 2) as dur_sec, start_timestamp
FROM records
WHERE attributes->>'run_id' = '{RUN_ID}'
  AND (span_name LIKE 'corpus_refiner%' OR message LIKE '%pipeline%')
ORDER BY start_timestamp
```

### Token Usage
```sql
SELECT span_name, message, attributes
FROM records
WHERE attributes->>'run_id' = '{RUN_ID}'
  AND span_name = 'token_usage_update'
ORDER BY start_timestamp
```

### Significant Heartbeats (48h)
```sql
SELECT trace_id, attributes->>'session_id' as session_id,
       attributes->>'wake_reason' as wake_reason,
       attributes->>'tools_used' as tools_used,
       start_timestamp, ROUND(duration::numeric, 2) as dur_sec
FROM records
WHERE span_name = 'heartbeat_significant'
  AND start_timestamp > NOW() - INTERVAL '48 hours'
ORDER BY start_timestamp DESC
LIMIT 20
```
**Age parameter**: 2880 (48 hours)

### Heartbeat Deep-Dive
```sql
SELECT span_name, message, ROUND(duration::numeric, 2) as dur_sec,
       is_exception, start_timestamp
FROM records
WHERE trace_id = '{HEARTBEAT_TRACE_ID}'
ORDER BY start_timestamp
```

## Normal Duration Ranges

| Span/Operation | Expected Range | Notes |
|---------------|---------------|-------|
| Full CLI session | 60-600s | Depends on task complexity |
| Corpus refinement | 10-60s | Multiple LLM calls |
| LLM batch extraction | 10-20s per batch | Parallelizable |
| Report section drafting | 5-20s per section | Sequential |
| LLM API call (POST) | 5-15s | Depends on model/load |
| Tool execution | 0.1-30s | Varies by tool type |
| Heartbeat OK-only | 3-15s | Single LLM turn |
| Heartbeat significant | 30-300s | Multiple tool calls |
