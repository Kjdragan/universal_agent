---
name: logfire-eval
description: Evaluate Universal Agent runs by analyzing Logfire traces via the Logfire MCP. Use when the user asks to analyze a run, debug issues, review heartbeat activity, identify bottlenecks, or generate a post-run evaluation report. Triggers on phrases like "analyze the run", "trace analysis", "logfire eval", "what happened in that heartbeat", "check the traces".
---

# Logfire Evaluation Skill

Analyze Universal Agent session traces to produce comprehensive evaluation reports. Uses the Logfire MCP `arbitrary_query` tool to query the OpenTelemetry records database.

## Critical Rules

1. **Always query by `run_id` or `trace_id`** — NEVER by time period alone (risks mixing multiple runs)
2. **Read `trace_catalog.md` first** if a workspace path is available — it has all trace IDs and query hints
3. **The main trace has 99%+ of useful data** — local toolkit traces are thin MCP wrappers (1 span each)
4. **Use `references/sql_queries.md`** for pre-built parameterized queries
5. **Use `references/span_catalog.md`** to understand what each span type means

## Workflow

### Phase A — Discovery

**If workspace path is known:**
1. Read `{workspace}/trace_catalog.md` or `{workspace}/trace.json` to get `run_id`, `trace_id`, and catalog
2. Use the `trace_id` from `trace_catalog.main_agent.trace_id`

**If "latest run" / "last run" requested:**
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

**If no run_id available, find by span count:**
```sql
SELECT trace_id, COUNT(*) as span_count,
       MIN(start_timestamp) as session_start
FROM records
WHERE start_timestamp > NOW() - INTERVAL '{AGE} minutes'
GROUP BY trace_id
ORDER BY span_count DESC
LIMIT 1
```

### Phase B — Health Check

Single query to get an overview:
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

Assign health verdict:
- **No exceptions + normal durations** → 
- **Some exceptions but completed** → 
- **Critical exceptions or incomplete** → 

### Phase C — Exception Deep-Dive

```sql
SELECT span_name, exception_type, exception_message, start_timestamp
FROM records
WHERE attributes->>'run_id' = '{RUN_ID}'
  AND is_exception = true
LIMIT 20
```

Classify each: transient vs systematic, tool error vs infrastructure.

### Phase D — Performance Bottleneck Analysis

Skip container spans (`llm_response_stream`, `conversation_iteration_*`, `ua_cli_session`, `gateway_request`).

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

**Normal durations:** corpus_refiner (10-60s), LLM extraction batches (10-20s), report drafting (5-20s), API calls (5-15s).

### Phase E — Tool Execution Analysis

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

### Phase F — Pipeline & Sub-Agent Analysis

```sql
SELECT span_name, message, ROUND(duration::numeric, 2) as dur_sec, start_timestamp
FROM records
WHERE attributes->>'run_id' = '{RUN_ID}'
  AND (span_name LIKE 'corpus_refiner%' OR message LIKE '%pipeline%')
ORDER BY start_timestamp
```

### Phase G — Token & Cost Analysis

```sql
SELECT span_name, message, attributes
FROM records
WHERE attributes->>'run_id' = '{RUN_ID}'
  AND span_name = 'token_usage_update'
ORDER BY start_timestamp
```

### Phase H — Heartbeat Analysis

**Triggered when user asks about heartbeat activity.** Heartbeats are autonomous runs not directed by the user.

**Find significant heartbeats (last 48h):**
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

If multiple found: present list to user, auto-analyze the most recent significant one.

To drill into a specific heartbeat, use its `trace_id` to get the full span tree:
```sql
SELECT span_name, message, ROUND(duration::numeric, 2) as dur_sec,
       BOOL_OR(is_exception) as has_error
FROM records
WHERE trace_id = '{HEARTBEAT_TRACE_ID}'
GROUP BY span_name, message, duration
ORDER BY start_timestamp
```

### Phase I — Report Generation

Save to `{workspace}/logfire_evaluation.md` with this structure:

```markdown
# Logfire Evaluation Report

## Quick Stats
| Metric | Value |
|--------|-------|
| Run ID | `{RUN_ID}` |
| Main Trace | `{TRACE_ID}` |
| Total Spans | {N} |
| Duration | {N}s |
| Tool Calls | {N} |
| Exceptions | {N} |

## Health: {verdict}
{One-line summary}

## Exceptions
{List or "None"}

## Top 5 Time Consumers
| Span | Duration | Type |
|------|----------|------|

## Tool Execution Summary
| Tool | Calls | Avg Duration | Errors |
|------|-------|--------------|--------|

## Heartbeat Summary (if applicable)
| Time | Session | Outcome | Tools | Duration |
|------|---------|---------|-------|----------|

## Recommendations
1. {Only if issues found}
```

## References

- `references/sql_queries.md` — All SQL queries organized by phase with interpretation guidance
- `references/span_catalog.md` — Every span type the Universal Agent emits with descriptions and key attributes
