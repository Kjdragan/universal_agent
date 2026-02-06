"""
Trace Catalog — Emits a structured trace catalog to stdout (→ run.log),
saves it to trace.json, and writes a standalone trace_catalog.md file.

This gives analysis agents a complete map of what traces exist,
what each one contains, and how to query it via the Logfire MCP.
"""

import json
import os
from typing import Any, Optional


_LOGFIRE_PROJECT_SLUG = "Kjdragan/composio-claudemultiagent"

_SPAN_TYPES_MAIN = [
    ("conversation_iteration_{N}", "Each LLM conversation turn"),
    ("llm_api_wait", "Time waiting for Claude API"),
    ("llm_response_stream", "Full response streaming"),
    ("assistant_message", "Each assistant message processing"),
    ("tool_use / tool_input", "Tool invocations + parameters"),
    ("tool_result / tool_output", "Tool results"),
    ("observer_*", "File/search/workbench observers"),
    ("skill_gated", "Skill routing decisions"),
    ("query_classification", "SIMPLE/COMPLEX routing"),
    ("token_usage_update", "Token accounting"),
    ("POST (HTTPX)", "Raw HTTP calls to LLM APIs"),
]


def _logfire_url(trace_id: str) -> str:
    slug = os.getenv("LOGFIRE_PROJECT_SLUG", _LOGFIRE_PROJECT_SLUG)
    return f"https://logfire.pydantic.dev/{slug}?q=trace_id%3D%27{trace_id}%27"


def emit_trace_catalog(
    *,
    trace_id: Optional[str],
    run_id: Optional[str],
    run_source: str = "user",
    local_toolkit_trace_ids: Optional[list[str]] = None,
    workspace_dir: Optional[str] = None,
) -> dict[str, Any]:
    """
    Print a trace catalog block to stdout and return the catalog dict.

    The printed block goes to stdout (which is tee'd to run.log), so analysis
    agents reading run.log can discover all trace IDs and understand what to query.
    """
    local_ids = local_toolkit_trace_ids or []
    catalog: dict[str, Any] = {
        "main_agent": {
            "trace_id": trace_id or "N/A",
            "logfire_url": _logfire_url(trace_id) if trace_id else "N/A",
            "description": "Primary execution trace with all agent spans",
            "span_types": [s[0] for s in _SPAN_TYPES_MAIN],
        },
        "local_toolkit": {
            "trace_ids": local_ids,
            "description": "MCP tool server traces (one span each)",
        },
        "run_id": run_id or "N/A",
        "run_source": run_source,
    }

    w = 66
    lines: list[str] = []
    lines.append(f"\n{'=' * w}")
    lines.append("         LOGFIRE TRACE CATALOG")
    lines.append(f"{'=' * w}")
    lines.append(f"  Service:    universal-agent")
    slug = os.getenv("LOGFIRE_PROJECT_SLUG", _LOGFIRE_PROJECT_SLUG)
    lines.append(f"  Project:    {slug}")
    lines.append(f"  Run ID:     {run_id or 'N/A'}")
    if run_source != "user":
        lines.append(f"  Source:     [{run_source.upper()}]")
    lines.append(f"{'-' * w}")

    # 1. Main Agent Trace
    lines.append("  1. MAIN AGENT TRACE")
    lines.append(f"     Trace ID: {trace_id or 'N/A'}")
    if trace_id:
        lines.append(f"     Logfire:  {_logfire_url(trace_id)}")
    lines.append("     Contains:")
    for span_name, desc in _SPAN_TYPES_MAIN:
        lines.append(f"       - {span_name}: {desc}")
    if trace_id:
        lines.append(f"     Query: WHERE trace_id = '{trace_id}'")
        lines.append(f"        OR: WHERE attributes->>'run_id' = '{run_id}'")

    # 2. Local Toolkit Traces
    lines.append("")
    lines.append("  2. LOCAL TOOLKIT TRACES (MCP tool server)")
    if local_ids:
        display = local_ids[:5]
        lines.append(f"     Trace IDs: {', '.join(display)}")
        if len(local_ids) > 5:
            lines.append(f"                (+{len(local_ids) - 5} more)")
        lines.append("     Contains: One span per MCP tool call (thin wrappers).")
        lines.append("     Useful for: checking tool-level exceptions only.")
    else:
        lines.append("     (no local tool calls)")

    # Analysis guide
    lines.append("")
    lines.append("  ANALYSIS GUIDE:")
    lines.append("   - Start with trace #1 (main) -- it has 99% of useful data")
    lines.append("   - Check exceptions: WHERE is_exception = true")
    lines.append("   - Find bottlenecks: ORDER BY duration DESC")
    lines.append("   - Tool timeline: WHERE span_name = 'tool_use'")
    lines.append("   - Token usage: WHERE span_name = 'token_usage_update'")

    if run_source == "heartbeat":
        lines.append("")
        lines.append("  [HEARTBEAT] This was a heartbeat run")
        lines.append("   - Source: autonomous -- not user-directed")
        lines.append("   - Filter: WHERE attributes->>'run_source' = 'heartbeat'")

    lines.append(f"{'=' * w}")

    print("\n".join(lines))
    return catalog


def save_trace_catalog_md(
    catalog: dict[str, Any],
    workspace_dir: str,
) -> str:
    """Write a standalone trace_catalog.md to the workspace directory."""
    main = catalog.get("main_agent", {})
    local = catalog.get("local_toolkit", {})
    run_id = catalog.get("run_id", "N/A")
    run_source = catalog.get("run_source", "user")
    trace_id = main.get("trace_id", "N/A")

    md_lines: list[str] = []
    md_lines.append("# Trace Catalog")
    md_lines.append("")
    md_lines.append(f"| Field | Value |")
    md_lines.append(f"|-------|-------|")
    md_lines.append(f"| **Run ID** | `{run_id}` |")
    md_lines.append(f"| **Run Source** | `{run_source}` |")
    md_lines.append(f"| **Service** | `universal-agent` |")
    slug = os.getenv("LOGFIRE_PROJECT_SLUG", _LOGFIRE_PROJECT_SLUG)
    md_lines.append(f"| **Logfire Project** | `{slug}` |")
    md_lines.append("")

    md_lines.append("## 1. Main Agent Trace")
    md_lines.append(f"- **Trace ID**: `{trace_id}`")
    if trace_id and trace_id != "N/A":
        md_lines.append(f"- **Logfire URL**: [{trace_id}]({main.get('logfire_url', '')})")
    md_lines.append(f"- **Description**: {main.get('description', '')}")
    md_lines.append("")
    md_lines.append("### Span Types")
    for span_name, desc in _SPAN_TYPES_MAIN:
        md_lines.append(f"- `{span_name}` — {desc}")
    md_lines.append("")
    md_lines.append("### Queries")
    md_lines.append(f"```sql")
    md_lines.append(f"-- All spans for this run")
    md_lines.append(f"SELECT * FROM records WHERE trace_id = '{trace_id}'")
    md_lines.append(f"-- Or by run_id")
    md_lines.append(f"SELECT * FROM records WHERE attributes->>'run_id' = '{run_id}'")
    md_lines.append(f"```")
    md_lines.append("")

    md_lines.append("## 2. Local Toolkit Traces")
    local_ids = local.get("trace_ids", [])
    if local_ids:
        md_lines.append(f"- **Count**: {len(local_ids)}")
        for lid in local_ids[:10]:
            md_lines.append(f"  - `{lid}`")
        if len(local_ids) > 10:
            md_lines.append(f"  - ... and {len(local_ids) - 10} more")
    else:
        md_lines.append("No local toolkit traces recorded.")
    md_lines.append("")

    if run_source == "heartbeat":
        md_lines.append("## Heartbeat Run")
        md_lines.append("This trace is from an **autonomous heartbeat run** (not user-directed).")
        md_lines.append("```sql")
        md_lines.append("-- Find all heartbeat runs")
        md_lines.append("SELECT * FROM records WHERE attributes->>'run_source' = 'heartbeat'")
        md_lines.append("-- Find significant heartbeats only")
        md_lines.append("SELECT * FROM records WHERE span_name = 'heartbeat_significant'")
        md_lines.append("```")
        md_lines.append("")

    md_lines.append("## Analysis Guide")
    md_lines.append("1. Start with the **Main Agent Trace** — it has 99%+ of useful data")
    md_lines.append("2. Check for exceptions: `WHERE is_exception = true`")
    md_lines.append("3. Find bottlenecks: `ORDER BY duration DESC`")
    md_lines.append("4. Tool timeline: `WHERE span_name = 'tool_use'`")
    md_lines.append("5. Token usage: `WHERE span_name = 'token_usage_update'`")

    content = "\n".join(md_lines)
    path = os.path.join(workspace_dir, "trace_catalog.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def enrich_trace_json(trace: dict, catalog: dict[str, Any]) -> None:
    """Add the trace_catalog key to the trace dict before it's saved."""
    trace["trace_catalog"] = catalog
