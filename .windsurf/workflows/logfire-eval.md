---
description: Evaluate a project run by analyzing Logfire traces. Use this to generate a comprehensive report of issues, errors, bottlenecks, and improvement opportunities from trace data.
---

# Logfire Evaluation Workflow

> **NOTE**: This workflow has been superseded by the **logfire-eval skill** at `.claude/skills/logfire-eval/SKILL.md`. The skill provides a more comprehensive, structured analysis protocol with run_id-based querying, heartbeat analysis, and reference documentation. Use the skill for new evaluations.

This workflow analyzes Universal Agent session traces to produce a comprehensive evaluation report.

## Key Insight: Focus on the MAIN TRACE

A session generates multiple trace IDs, but **99%+ of useful data is in ONE trace** (the main agent trace). Other traces are thin MCP wrappers with 1 span each.

| Trace Type | Span Count | Use |
|------------|------------|-----|
| Main Agent (`standalone_composio_test`) | 500-600 spans | **Primary analysis** |
| Local Toolkit (MCP wrappers) | 1 span each | Only check for exceptions |
| HTTP overhead | 1 span each | Ignore |

## Output Location
Save to: `AGENT_RUN_WORKSPACES/{session_id}/logfire_evaluation.md`

---

## Step 1: Find the Main Trace

The main trace has the most spans. Find it:

```sql
SELECT trace_id, COUNT(*) as span_count, 
       MIN(start_timestamp) as session_start,
       MAX(end_timestamp) as session_end
FROM records 
WHERE start_timestamp > NOW() - INTERVAL '{AGE_MINUTES} minutes'
GROUP BY trace_id
ORDER BY span_count DESC
LIMIT 1
```

This returns the main trace ID (e.g., `019bf1957d9364de9f2f02d38153924a` with 586 spans).

---

## Step 2: Get Session Summary (Single Query)

Extract key metrics from the main trace:

```sql
SELECT 
  span_name,
  COUNT(*) as count,
  ROUND(AVG(duration)::numeric, 3) as avg_duration_sec,
  ROUND(MAX(duration)::numeric, 3) as max_duration_sec
FROM records 
WHERE trace_id = '{MAIN_TRACE_ID}'
GROUP BY span_name
ORDER BY count DESC
LIMIT 25
```

**Key span types to look for:**
| Span Name | Meaning |
|-----------|---------|
| `tool_use` | Tool calls made |
| `tool_execution_completed` | Successful tool completions |
| `ledger_mark_succeeded` | Durable step completions |
| `corpus_refiner.*` | Research pipeline work |
| `POST api.z.ai/...` | LLM API calls |
| `durable_checkpoint_saved` | Recovery checkpoints |

---

## Step 3: Check for Exceptions (Quick)

```sql
SELECT span_name, exception_type, exception_message, start_timestamp
FROM records 
WHERE trace_id = '{MAIN_TRACE_ID}'
  AND is_exception = true
LIMIT 10
```

If empty → ✅ No exceptions.

---

## Step 4: Find Real Bottlenecks

**Skip parent spans** (`llm_response_stream`, `conversation_iteration_*`) - they're containers.

```sql
SELECT span_name, message, ROUND(duration::numeric, 2) as duration_sec
FROM records 
WHERE trace_id = '{MAIN_TRACE_ID}'
  AND duration > 1.0
  AND span_name NOT IN ('llm_response_stream', 'conversation_iteration_1', 'standalone_composio_test')
ORDER BY duration DESC
LIMIT 15
```

**Expected bottlenecks (normal):**
- `corpus_refiner.refine_corpus` (10-60s) - Corpus processing
- `corpus_refiner.extract_batch` (10-20s) - LLM extraction batches
- `Message with 'glm-4.7'` (5-20s) - Report section generation
- `POST api.z.ai/...` (5-15s) - LLM API calls

---

## Step 5: Tool Execution Timeline

```sql
SELECT 
  attributes->>'tool_name' as tool,
  attributes->>'duration_seconds' as duration,
  attributes->>'status' as status,
  start_timestamp
FROM records 
WHERE trace_id = '{MAIN_TRACE_ID}'
  AND span_name = 'tool_execution_completed'
ORDER BY start_timestamp
```

---

## Step 6: Pipeline Phase Summary

```sql
SELECT span_name, message, ROUND(duration::numeric, 2) as duration_sec, start_timestamp
FROM records 
WHERE trace_id = '{MAIN_TRACE_ID}'
  AND (span_name LIKE 'corpus_refiner%' OR message LIKE '%pipeline%' OR message LIKE '%crawl%')
ORDER BY start_timestamp
```

---

## Step 7: Generate Report

Create a markdown report with the following sections:

### Report Template

```markdown
# Logfire Evaluation Report — {SESSION_ID}

## Quick Stats
| Metric | Value |
|--------|-------|
| Main Trace | `{MAIN_TRACE_ID}` |
| Total Spans | {SPAN_COUNT} |
| Duration | {DURATION}s |
| Tool Calls | {TOOL_COUNT} |
| Exceptions | {EXCEPTION_COUNT} |

## Health: {✅ SUCCESS / ⚠️ PARTIAL / ❌ FAILED}

{One-line summary}

## Exceptions
{List or "✅ None"}

## Top 5 Time Consumers
| Span | Duration | Type |
|------|----------|------|
| {span_name} | {duration}s | {category} |

## Pipeline Phases
| Phase | Duration | Status |
|-------|----------|--------|
| Corpus Refinement | {X}s | ✅ |
| LLM Extraction | {X}s | ✅ |
| Report Drafting | {X}s | ✅ |

## Recommendations
1. {Only if issues found}
```

## Quick Reference

**Find main trace:** Query with `ORDER BY span_count DESC LIMIT 1`

**Normal durations:**
- Full run: 60-600s
- Corpus refinement: 10-60s
- LLM batch extraction: 10-20s per batch
- Report section drafting: 5-20s per section

**Ignore these (parent spans):**
- `llm_response_stream`
- `conversation_iteration_*`
- `standalone_composio_test`
