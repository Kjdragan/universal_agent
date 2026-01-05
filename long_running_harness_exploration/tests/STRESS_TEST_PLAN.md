# Stress Test Plan: Report Generation Context Limits

**Created**: 2026-01-05  
**Objective**: Identify context exhaustion failure points in `report-creation-expert` sub-agent

---

## Test Strategy

### Progressive Complexity Levels

| Level | Search Sources | Expected Crawl | Test ID |
|-------|---------------|----------------|---------|
| **L1** | 5 sources | ~5 articles | `stress_L1_*` |
| **L2** | 10 sources | ~8-10 articles | `stress_L2_*` |
| **L3** | 15 sources | ~12-14 articles | `stress_L3_*` |
| **L4** | 20 sources | ~15-18 articles | `stress_L4_*` |
| **L5** | 25+ sources | ~20+ articles | `stress_L5_*` |

### Query Templates

**L1 (5 sources)**:
```
Research the latest AI industry news from today. Find 5 recent news articles about AI developments, create a brief 2-page summary report, and save it to work_products.
```

**L2 (10 sources)**:
```
Research the latest AI industry developments. Find 10 recent news articles about AI companies (OpenAI, Anthropic, Google, Microsoft, Meta), create a comprehensive 5-page report with sections for each company, and save it to work_products.
```

**L3 (15 sources)**:
```
Conduct deep research on AI industry trends for Q4 2025 and early 2026. Find 15+ news articles covering: major AI announcements, funding rounds, regulatory developments, and technical breakthroughs. Create a detailed 10-page report with executive summary, trend analysis, and company-by-company breakdown. Save to work_products.
```

**L4 (20 sources)**:
```
Create a comprehensive AI industry research report covering the past month. Find 20+ sources including: mainstream tech news, AI research publications, startup funding announcements, regulatory news (EU AI Act, US executive orders), and enterprise AI adoption stories. Produce a 15-page professional report with charts/data where available, executive summary, market analysis, regulatory landscape, and company profiles. Save to work_products.
```

**L5 (25+ sources)**:
```
Create an exhaustive quarterly AI industry report for institutional investors. Research 25+ diverse sources covering: all major AI labs (OpenAI, Anthropic, Google DeepMind, Meta AI, Mistral, xAI), chip manufacturers (NVIDIA, AMD, Intel), cloud providers, AI startups, regulatory bodies, and academic research. Produce a 25-page professional report with executive summary, market sizing, competitive landscape, regulatory analysis, technology trends, investment thesis, and risk factors. Include data visualizations where possible. Save to work_products.
```

---

## Metrics to Capture

For each test run:

### From Session Workspace

| Metric | Source | Location |
|--------|--------|----------|
| Total tool calls | `run.log` or `trace.json` | Parse tool invocations |
| Search results count | `search_results/` | Count `*.json` files |
| Crawled articles count | `search_results/` | Count `crawl_*.md` files |
| Filtered articles count | `search_results_filtered_best/` | Count files |
| Report generated | `work_products/` | Check for `.html` file |
| Report size | `work_products/*.html` | File size in bytes |
| Run duration | `run.log` | First to last timestamp |
| Errors | `run.log` | Search for ERROR, Exception |

### From Logfire (via MCP)

| Metric | Query |
|--------|-------|
| Span count | Total spans in trace |
| Exception traces | Any exception_type populated |
| Token usage | If captured in attributes |
| Sub-agent spans | Spans with `subagent` in name |

### Success Criteria

| Outcome | Definition |
|---------|------------|
| **SUCCESS** | HTML report generated, saved to work_products, no errors |
| **PARTIAL** | Some content generated but incomplete or truncated |
| **FAILURE** | Error encountered, no usable output |
| **CONTEXT_EXHAUSTED** | Specific error about context/token limits |

---

## Test Execution

### Manual Execution (Recommended for Initial Tests)

1. Start agent: `./local_dev.sh`
2. Enter query when prompted
3. Monitor output for errors
4. After completion, record session directory
5. Analyze workspace artifacts

### Recording Results

After each run, create:
```
tests/results/stress_LX_YYYYMMDD_HHMMSS.md
```

With:
- Session ID (directory name)
- Query used
- Outcome (SUCCESS/PARTIAL/FAILURE/CONTEXT_EXHAUSTED)
- Metrics captured
- Error messages (if any)
- Observations

---

## Baseline Establishment

Before stress testing, establish baseline with a known-working query:

**Baseline Query**:
```
Research the latest AI news today. Find 3-5 articles, create a brief summary report, and save it to work_products.
```

Expected: Should complete successfully. If this fails, investigate before proceeding.

---

## Hypothesis

Based on code review and prior observations:

1. **L1-L2**: Should succeed (5-10 sources within normal context)
2. **L3**: May show strain (12-15 sources approaching limits)
3. **L4**: Likely to fail or produce truncated output (20+ sources)
4. **L5**: Expected failure (25+ sources definitely exceeds limits)

The specific failure point will inform design of continuation mechanism.

---

## Post-Test Analysis

After all levels tested, document:

1. **Failure threshold**: At what source count does failure begin?
2. **Failure mode**: What error/behavior indicates failure?
3. **Progress before failure**: How much useful work was done?
4. **Recovery potential**: Could the work be continued if we had file-based state?

Update `KNOWLEDGE_BASE.md` with findings.
