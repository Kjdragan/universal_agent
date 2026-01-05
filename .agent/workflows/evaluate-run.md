---
description: Evaluate the latest run traces using LogFire MCP and agent workspace artifacts to report issues, performance, and bottlenecks.
---

## 1. Identify Latest Run

**For local runs**: List `AGENT_RUN_WORKSPACES` directory:
```bash
ls -lt AGENT_RUN_WORKSPACES/ | head -5
```

**For Docker/Telegram runs**:
```bash
docker exec universal_agent_bot ls -la /tmp/AGENT_RUN_WORKSPACES/
```

Set the most recent `session_YYYYMMDD_HHMMSS` as `TARGET_SESSION`.

---

## 2. Extract Run Metadata

Read the **FULL** `TARGET_SESSION/run.log` file.

> [!IMPORTANT]
> Read the ENTIRE log file. Critical errors appear mid-file (malformed tool calls, recovery loops, context overflow).

**Extract from header:**
- Main Trace ID (from `Trace ID:` line)
- Run ID
- Start/End times

**Scan for issues:**
- `tool_use_error`
- `Error:` or `❌`
- `Tool schema validation failed`
- Malformed tool names (e.g., `TOOLname</arg_key>`)

---

## 3. Analyze Logfire Traces (REQUIRED - use MCP tool)

> [!IMPORTANT]
> **Use the `mcp_logfire_arbitrary_query` MCP tool to query traces.** Do NOT just read trace.json - you must query Logfire for errors, warnings, and performance data.

> [!CAUTION]
> **Multi-Trace Architecture**: A single run produces MULTIPLE trace IDs:
> 1. **Main Agent** (`service_name='universal-agent'`): Claude SDK, Composio HTTP, observers
> 2. **Local Toolkit** (`service_name='local-toolkit'`): MCP tool calls - **may have MANY trace IDs**
> 
> You MUST query ALL trace IDs listed in the terminal output to get the full picture.

### 3a. Collect ALL Trace IDs from Terminal Output

Look for this block at the end of `run.log`:
```
=== TRACE IDS (for Logfire debugging) ===
  Main Agent:     019b8b264e07c7320bfb6a04b4c56432
  Local Toolkit:  019b8b03e2b7..., 019b8b0536c9..., ...
```

### 3b. Query Each Trace via Logfire MCP

For EACH trace ID, run these queries:

| Query | SQL |
|-------|-----|
| Errors | `SELECT span_name, message, level FROM records WHERE trace_id='<TRACE_ID>' AND level >= 'warning'` |
| Performance | `SELECT span_name, duration FROM records WHERE trace_id='<TRACE_ID>' ORDER BY start_timestamp` |

### 3c. Batch Query for Local Toolkit

```sql
SELECT trace_id, span_name, duration, message, level 
FROM records 
WHERE service_name='local-toolkit' 
  AND start_timestamp BETWEEN '<SESSION_START>' AND '<SESSION_END>'
  AND level >= 'warning'
ORDER BY start_timestamp
```

---

## 4. Evaluate Workspace Artifacts

Check these directories in `TARGET_SESSION/`:

| Directory | Purpose | Check For |
|-----------|---------|-----------|
| `search_results/` | Raw search JSON files | Files match expected tool calls |
| `search_results_filtered_best/` | Filtered crawl content | Quality of filtering |
| `work_products/` | Final deliverables | HTML reports, PDFs |
| `transcript.md` | Conversation transcript | Review for errors |
| `trace.json` | Full trace data | All metadata, trace IDs |

**Verify Observer Pattern**:
- Did `SAVED_REPORTS/` get a copy of work products?
- Did `search_results/*.json` get auto-saved?

---

## 5. Work Product Quality Review (CRITICAL)

> [!CAUTION]
> **NO SYCOPHANCY.** Provide a rigorous, university-level critique of the generated reports. The goal is to identify weaknesses so we can improve report generation. Be specific and critical.

### 5a. Read the Work Products

Read the full content of files in `TARGET_SESSION/work_products/`:
- HTML reports
- PDF files (if text-extractable)
- Any other deliverables

### 5b. Critique Criteria

Evaluate each work product against these standards:

#### Content Quality
| Criterion | Question | Rating (1-5) |
|-----------|----------|--------------|
| **Accuracy** | Are facts correct? Any hallucinations or unsupported claims? | |
| **Completeness** | Does it cover all aspects of the user's query? Any gaps? | |
| **Source Attribution** | Are sources cited properly? Can claims be traced back? | |
| **Depth vs Breadth** | Is it shallow summary or substantive analysis? | |
| **Currency** | Is information current and timely? | |

#### Structure & Organization
| Criterion | Question | Rating (1-5) |
|-----------|----------|--------------|
| **Logical Flow** | Does the narrative progress logically? | |
| **Section Organization** | Are sections well-organized and appropriately sized? | |
| **Executive Summary** | Does it capture key points effectively? | |
| **Headings/Subheadings** | Are they descriptive and helpful? | |

#### Writing Quality
| Criterion | Question | Rating (1-5) |
|-----------|----------|--------------|
| **Clarity** | Is the writing clear and understandable? | |
| **Conciseness** | Is it appropriately concise or overly verbose? | |
| **Professional Tone** | Is the tone appropriate for the content? | |
| **Grammar/Spelling** | Any errors? | |

#### Presentation (HTML/PDF)
| Criterion | Question | Rating (1-5) |
|-----------|----------|--------------|
| **Visual Design** | Is it visually appealing and professional? | |
| **Tables/Charts** | Are data visualizations effective? | |
| **Images** | Are images relevant and properly embedded? | |
| **Responsive Layout** | Does it render well at different sizes? | |

### 5c. Specific Weaknesses to Identify

Be explicit about:
1. **Missing information** the report should have included
2. **Unsupported claims** that lack source attribution
3. **Structural problems** (buried lede, weak intro/conclusion)
4. **Missed opportunities** for charts, tables, or visuals
5. **Redundancy** or filler content
6. **Bias or perspective gaps** in coverage

### 5d. Recommendations for Improvement

For each weakness, suggest:
- What should have been done differently
- Specific prompt/instruction changes that could help
- Whether this is a systemic issue vs one-off

## 6. Identify Deviations (Happy Path Analysis)

### Classification & Routing
- [ ] Did it classify as SIMPLE vs COMPLEX correctly?
- [ ] Did Fast Path fallback to Complex Path?

### Execution Flow
- [ ] Did it loop excessively (>3 iterations)?
- [ ] Were there `is_error=True` in tool results?
- [ ] Did the agent recover from errors?

### Sub-Agent Delegation
- [ ] Was `report-creation-expert` used for research reports?
- [ ] Did sub-agent receive correct workspace injection?

### Tool Schema Issues
- [ ] Any `Tool schema validation failed` errors?
- [ ] 0-byte writes or empty content?
- [ ] Wrong parameter names (e.g., `recipient` vs `recipient_email`)?

### Email/Identity Resolution
- [ ] Did "email to me" resolve correctly?
- [ ] Was email actually sent or just uploaded?

### PDF Generation
- [ ] D-Bus errors (cosmetic but noisy)?
- [ ] Correct Chrome headless flags used?
- [ ] PDF file actually created?

---

## 7. Generate Performance Report

Create `TARGET_SESSION/evaluation_report.md` OR save to `Project_Documentation/` with numbered prefix.

### Required Sections:

**Executive Summary Table:**
| Metric | Value | Status |
|--------|-------|--------|
| Overall Outcome | Pass/Fail | ✅/🔴 |
| Execution Time | X.X seconds | |
| Tool Calls | N | |
| Iterations | N | |
| Critical Errors | N | |

**Phase Performance Table:**
| Phase | Duration | Tools Used | Bottleneck? |
|-------|----------|------------|-------------|
| Planning (Classification) | | | |
| Search (COMPOSIO_SEARCH_*) | | | |
| Crawling (finalize_research) | | | |
| Report Generation | | | |
| PDF Conversion | | | |
| Email/Upload | | | |

**Issues Found:**
For each issue:
- Severity: 🔴 HIGH / 🟡 MEDIUM / 🟢 LOW
- Location in run.log
- Root cause analysis
- Recommendation

**What Worked Well:**
- Research pipeline efficiency (crawl success rate)
- Report quality
- Recovery from errors

---

## 8. Summary

Output a concise summary to the user including:
1. Overall pass/fail status
2. Key issues found (if any)
3. Performance bottlenecks
4. Recommended fixes
