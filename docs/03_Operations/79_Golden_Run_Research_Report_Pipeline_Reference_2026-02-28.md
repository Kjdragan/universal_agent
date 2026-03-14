# 79 - Golden Run Reference: Research -> Report -> PDF -> Gmail (2026-02-28)

## Purpose
This document defines a canonical "golden run" for a high-value multi-step workflow:

1. User requests current-event research.
2. Simone delegates research to `research-specialist`.
3. Research pipeline executes deterministic crawl/refine.
4. Simone delegates report writing to `report-writer`.
5. Report is generated, converted to PDF, uploaded, and emailed.

This is a baseline for regression checks across delegation, hooks/guardrails, workspace artifact generation, and delivery.

## Reference Session
- Session: `session_20260227_195151_2927affc`
- Workspace: `/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260227_195151_2927affc`
- User prompt:

```text
Search for the latest news from the Russia-Ukraine war over the past four days.
Create a report, save that report as a PDF and Gmail it to me.
```

## Why This Is Golden
- Immediate specialist delegation by Simone (no primary-side research execution drift).
- Deterministic research backbone used (`run_research_phase`).
- Report writer delegation used before final delivery.
- End-to-end delivery completed in one turn.
- Workspace artifacts were created in the correct structure.
- No inefficient fallback bash scouting behavior in the primary path.

## Expected Tool Sequence (Observed)
From `run.log` and turn telemetry:

1. `Task(subagent_type='research-specialist', ...)`
2. `mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL` (search batch 1)
3. `mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL` (search batch 2)
4. `mcp__internal__run_research_phase`
5. `Read` (refined corpus check)
6. `Task(subagent_type='report-writer', ...)`
7. `mcp__internal__run_report_generation`
8. `Read` (report HTML check)
9. `mcp__internal__html_to_pdf`
10. `mcp__internal__upload_to_composio`
11. `mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL` (`GMAIL_SEND_EMAIL`)

## Log Evidence Snippets

### A) Immediate delegation by Simone
```text
[19:52:46] TOOL CALL: Task
  "subagent_type": "research-specialist"
```

### B) Deterministic research phase completion
```text
[19:53:34] TOOL CALL: mcp__internal__run_research_phase
...
{
  "status": "success",
  "message": "Research Phase Complete! Refined corpus created.",
  "outputs": {
    "refined_corpus": ".../tasks/russia_ukraine_news/refined_corpus.md"
  }
}
```

### C) Report generation completion
```text
[19:57:32] TOOL CALL: mcp__internal__run_report_generation
...
{
  "status": "success",
  "message": "Report Generation Phase Complete!",
  "outputs": {
    "report_html": ".../work_products/report.html"
  }
}
```

### D) Delivery completion
```text
[19:59:46] TOOL CALL: mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL
  "tool_slug": "GMAIL_SEND_EMAIL"
...
"successful": true
```

### E) Turn-level adherence signal
`turns/turn_1772243529699_78b9d30c.jsonl` includes:
```json
"research_pipeline_adherence": {
  "passed": true,
  "pre_phase_workspace_scouting_calls": 0,
  "required": true,
  "run_research_phase_called": true,
  "search_collection_detected": true
}
```

## Required Workspace Artifacts
For this golden workflow, the following must exist:

- `search_results/` with search outputs.
- `tasks/<task_name>/refined_corpus.md`
- `work_products/report.html`
- `work_products/*.pdf`
- `run.log`
- `turns/turn_*.jsonl`

Reference files from this run:
- `tasks/russia_ukraine_news/refined_corpus.md`
- `work_products/report.html`
- `work_products/russia_ukraine_war_report_feb2026.pdf`

## Delegation Interpretation Note
Simone can say "I'll handle this end-to-end" in user-facing language while still delegating correctly.

For debugging, authoritative truth is tool sequence + turn telemetry, not wording style.

## Golden Run Validation Checklist
When validating future runs of this pattern:

1. First action after classification is `Task -> research-specialist`.
2. Search collection is done by specialist, then `run_research_phase` executes.
3. `Task -> report-writer` is present before report finalization.
4. `run_report_generation` succeeds and writes `work_products/report.html`.
5. PDF conversion and Gmail send both succeed.
6. Turn metadata shows research pipeline adherence passed.
7. No repo-root workspace leakage artifacts are produced.

## Regression Tests To Run
Use this set for this workflow family:

```bash
uv run pytest -q \
  tests/unit/test_hooks_vp_tool_enforcement.py \
  tests/unit/test_prompt_assets_capabilities.py \
  tests/unit/test_agent_definition_tooling.py \
  tests/unit/test_research_pipeline_drift.py
```

## Operational Intent
This reference is not intended to constrain unrelated workflows. It is a strict baseline for this specific high-value research/report pipeline because it exercises:

- intent classification,
- specialist routing,
- deterministic internal pipeline execution,
- report generation,
- artifact production,
- external delivery.

