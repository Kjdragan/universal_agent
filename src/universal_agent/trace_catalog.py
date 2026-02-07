"""
Trace Catalog — Emits a structured trace catalog to stdout (→ run.log),
saves it to trace.json, writes a standalone trace_catalog.md file, and
persists work-product copies under work_products/logfire-eval/.

This gives analysis agents a complete map of what traces exist,
what each one contains, and how to query it via the Logfire MCP.
"""

import json
import os
import re
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

_LOCAL_TRACE_ID_PATTERN = re.compile(r"\[local-toolkit-trace-id: ([0-9a-f]{32})\]")


def _logfire_url(trace_id: str) -> str:
    slug = os.getenv("LOGFIRE_PROJECT_SLUG", _LOGFIRE_PROJECT_SLUG)
    return f"https://logfire.pydantic.dev/{slug}?q=trace_id%3D%27{trace_id}%27"


def _normalize_trace_ids(trace_ids: list[str]) -> list[str]:
    cleaned: set[str] = set()
    for trace_id in trace_ids:
        if isinstance(trace_id, str) and len(trace_id) == 32:
            lowered = trace_id.lower()
            if all(ch in "0123456789abcdef" for ch in lowered):
                cleaned.add(lowered)
    return sorted(cleaned)


def extract_local_tool_trace_ids_from_trace(trace: dict[str, Any]) -> list[str]:
    """Extract local toolkit trace IDs from in-memory tool result previews."""
    trace_ids: set[str] = set()
    for result in trace.get("tool_results", []):
        if not isinstance(result, dict):
            continue
        preview = result.get("content_preview")
        if not isinstance(preview, str):
            continue
        for match in _LOCAL_TRACE_ID_PATTERN.finditer(preview):
            trace_ids.add(match.group(1))
    return sorted(trace_ids)


def _build_trace_catalog_markdown(catalog: dict[str, Any]) -> str:
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
    md_lines.append(f"| **Catalog Scope** | `main trace + local toolkit markers` |")
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

    md_lines.append("## 2. Local Toolkit Trace IDs")
    local_ids = local.get("trace_ids", [])
    distinct_local_ids = local.get("distinct_trace_ids", [])
    mode = local.get("mode", "none")
    if mode == "embedded_in_main":
        md_lines.append("- Local toolkit spans are embedded under the **main trace** (no separate trace IDs).")
        md_lines.append(f"- Shared Trace ID: `{trace_id}`")
    elif distinct_local_ids:
        md_lines.append(f"- **Distinct Local Toolkit Trace Count**: {len(distinct_local_ids)}")
        for lid in distinct_local_ids[:10]:
            md_lines.append(f"  - `{lid}`")
        if len(distinct_local_ids) > 10:
            md_lines.append(f"  - ... and {len(distinct_local_ids) - 10} more")
        overlap = local.get("overlap_with_main", False)
        if overlap:
            md_lines.append(f"- Note: Main trace ID also appeared in local toolkit markers (`{trace_id}`)")
    elif local_ids:
        md_lines.append("- Local toolkit markers found, but none had distinct trace IDs.")
    else:
        md_lines.append("No local toolkit trace IDs discovered.")
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
    md_lines.append("")
    md_lines.append("## Coverage Notes")
    md_lines.append("- This catalog guarantees main trace coverage and local toolkit marker coverage.")
    md_lines.append("- External tool traces without emitted trace markers may not be discoverable.")

    return "\n".join(md_lines)


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
    local_ids = _normalize_trace_ids(local_toolkit_trace_ids or [])
    main_trace_id = trace_id.lower() if isinstance(trace_id, str) else None
    overlap_with_main = bool(main_trace_id and main_trace_id in local_ids)
    distinct_local_ids = [lid for lid in local_ids if lid != main_trace_id]
    mode = (
        "embedded_in_main"
        if overlap_with_main and not distinct_local_ids
        else ("distinct_traces" if distinct_local_ids else "none")
    )
    catalog: dict[str, Any] = {
        "main_agent": {
            "trace_id": trace_id or "N/A",
            "logfire_url": _logfire_url(trace_id) if trace_id else "N/A",
            "description": "Primary execution trace with all agent spans",
            "span_types": [s[0] for s in _SPAN_TYPES_MAIN],
        },
        "local_toolkit": {
            "trace_ids": local_ids,
            "distinct_trace_ids": distinct_local_ids,
            "overlap_with_main": overlap_with_main,
            "mode": mode,
            "description": (
                "In-process local toolkit spans share the main trace context"
                if mode == "embedded_in_main"
                else "MCP tool server traces discovered from tool output markers"
            ),
        },
        "all_trace_ids": sorted(
            {
                *local_ids,
                *([main_trace_id] if main_trace_id else []),
            }
        ),
        "coverage": {
            "sources": [
                "main_trace_id",
                "local_toolkit_trace_markers",
            ],
            "note": (
                "Catalog guarantees main trace coverage and local toolkit marker coverage. "
                "External tool traces without emitted trace markers may not be discoverable."
            ),
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
    lines.append("  Scope:      main trace + local toolkit markers")
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
    if mode == "embedded_in_main":
        lines.append("     Distinct Trace IDs: none (in-process spans are embedded in main trace)")
        lines.append(f"     Shared Trace ID: {trace_id or 'N/A'}")
    elif distinct_local_ids:
        display = distinct_local_ids[:5]
        lines.append(f"     Trace IDs: {', '.join(display)}")
        if len(distinct_local_ids) > 5:
            lines.append(f"                (+{len(distinct_local_ids) - 5} more)")
        if overlap_with_main:
            lines.append(f"     Note: main trace ID also appeared in local markers ({trace_id})")
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
    content = _build_trace_catalog_markdown(catalog)
    path = os.path.join(workspace_dir, "trace_catalog.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def save_trace_catalog_work_product(catalog: dict[str, Any], workspace_dir: str) -> dict[str, str]:
    """
    Write trace catalog artifacts to session work_products/logfire-eval/.
    Returns both Markdown and JSON paths for deterministic skill discovery.
    """
    out_dir = os.path.join(workspace_dir, "work_products", "logfire-eval")
    os.makedirs(out_dir, exist_ok=True)

    md_path = os.path.join(out_dir, "trace_catalog.md")
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(_build_trace_catalog_markdown(catalog))

    json_path = os.path.join(out_dir, "trace_catalog.json")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(catalog, handle, indent=2, default=str)

    return {"md_path": md_path, "json_path": json_path}


def enrich_trace_json(trace: dict, catalog: dict[str, Any]) -> None:
    """Add the trace_catalog key to the trace dict before it's saved."""
    trace["trace_catalog"] = catalog
