from typing import Any
from pathlib import Path
from claude_agent_sdk import tool
import sys
import os

from universal_agent.execution_context import get_current_workspace as _ctx_get_workspace
from universal_agent.utils.session_workspace import resolve_current_run_workspace

# Backward-compatible alias for older tests and call sites that still patch the
# legacy helper symbol on this module.
resolve_current_session_workspace = resolve_current_run_workspace

# Import the original function
# We need to ensure the python path can find src/mcp_server.py if it's not a package
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

try:
    from mcp_server import _run_research_pipeline_legacy as original_pipeline
    from mcp_server import _crawl_core
    from mcp_server import _run_research_phase_legacy as research_phase_core
    from mcp_server import _run_report_generation_legacy as report_gen_core
except ImportError:
    # Fallback for when running from different contexts
    from src.mcp_server import _run_research_pipeline_legacy as original_pipeline
    from src.mcp_server import _crawl_core
    from src.mcp_server import _run_research_phase_legacy as research_phase_core
    from src.mcp_server import _run_report_generation_legacy as report_gen_core

# Import Task Guardrails
from universal_agent.utils.task_guardrails import resolve_best_task_match
from universal_agent.hooks import StdoutToEventStream


def _is_session_workspace(path_value: str) -> bool:
    try:
        candidate = Path(path_value).resolve()
    except Exception:
        return False
    if (
        candidate.name.startswith(("run_", "session_"))
        and candidate.parent.name == "AGENT_RUN_WORKSPACES"
    ):
        return True
    return (
        (
            (candidate / "session_policy.json").exists()
            or (candidate / "run_manifest.json").exists()
            or (candidate / "run_checkpoint.json").exists()
            or (candidate / "session_checkpoint.json").exists()
        )
        and (candidate / "work_products").exists()
    )


def _infer_latest_session_workspace() -> str | None:
    root = (Path(__file__).resolve().parents[3] / "AGENT_RUN_WORKSPACES").resolve()
    if not root.exists():
        return None
    candidates = sorted(
        (
            p
            for p in root.iterdir()
            if p.is_dir() and p.name.startswith(("run_", "session_"))
        ),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        if _is_session_workspace(str(candidate)):
            return str(candidate.resolve())
    return None


def _resolve_workspace_hint(args: dict[str, Any]) -> str | None:
    """Best-effort workspace resolution for research bridge tools."""
    import sys

    explicit = str(args.get("workspace_dir", "") or "").strip()
    if explicit and Path(explicit).exists() and _is_session_workspace(explicit):
        sys.stderr.write(f"[research_bridge] workspace resolved from explicit arg: {explicit}\n")
        return str(Path(explicit).resolve())

    ctx_ws = str(_ctx_get_workspace() or "").strip()
    if ctx_ws and Path(ctx_ws).exists() and _is_session_workspace(ctx_ws):
        sys.stderr.write(f"[research_bridge] workspace resolved from context var: {ctx_ws}\n")
        return str(Path(ctx_ws).resolve())

    marker_ws = resolve_current_session_workspace(
        repo_root=str(Path(__file__).resolve().parents[3])
    )
    if marker_ws and Path(marker_ws).exists() and _is_session_workspace(marker_ws):
        sys.stderr.write(f"[research_bridge] workspace resolved from marker file: {marker_ws}\n")
        return str(Path(marker_ws).resolve())

    try:
        import universal_agent.main as ua_main

        observer_ws = str(getattr(ua_main, "OBSERVER_WORKSPACE_DIR", "") or "").strip()
        if observer_ws and Path(observer_ws).exists() and _is_session_workspace(observer_ws):
            sys.stderr.write(f"[research_bridge] workspace resolved from observer: {observer_ws}\n")
            return str(Path(observer_ws).resolve())
    except Exception:
        pass

    inferred = _infer_latest_session_workspace()
    if inferred:
        sys.stderr.write(f"[research_bridge] workspace resolved from latest workspace inference: {inferred}\n")
    else:
        sys.stderr.write(f"[research_bridge] WARNING: could not resolve workspace. explicit={explicit!r}, ctx={ctx_ws!r}\n")
    return inferred


@tool(
    name="run_research_pipeline", 
    description="Execute the UNIFIED Research & Reporting Pipeline. Handles Search -> Crawl -> Refine -> Outline -> Draft -> Compile in one Turn. EFFICIENCY: Use this to avoid fragmented tool calls. TRUST the JSON success receipt; DO NOT call Bash/ls after this.",
    input_schema={
        "query": str, 
        "context_path": str,
        "workspace_dir": str,
    }
)
async def run_research_pipeline_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    """
    Wrapper for the research pipeline to run in-process.
    """
    query = args.get("query")
    raw_task_name = args.get("context_path", "default")
    workspace_hint = _resolve_workspace_hint(args)
    
    # Apply Guardrail
    task_name = resolve_best_task_match(raw_task_name)
    
    # Execute the original function directly
    # Since it runs in this process, its print/stderr writes will go to our console
    # [ENHANCED] Capture stdout and bridge to Web UI events
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = await original_pipeline(query, task_name, workspace_dir=workspace_hint)
    
    return {
        "content": [{
            "type": "text",
            "text": result_str
        }]
    }

@tool(
    name="crawl_parallel",
    description="High-speed parallel web scraping using crawl4ai. Scrapes multiple URLs concurrently and saves results to 'search_results' directory.",
    input_schema={
        "urls": list,
        "session_dir": str
    }
)
async def crawl_parallel_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    """
    Wrapper for crawl_parallel to run in-process.
    """
    urls = args.get("urls", [])
    session_dir = args.get("session_dir")
    
    if not session_dir:
        return {
            "content": [{
                "type": "text",
                "text": "❌ Error: session_dir is required."
            }]
        }
    
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = await _crawl_core(urls, session_dir)
    
    return {
        "content": [{
            "type": "text",
            "text": result_str
        }]
    }

@tool(
    name="run_research_phase",
    description="Execute Phase 1 of Research: Crawl & Refine. Produces refined_corpus.md. EFFICIENCY: Trust the output path in the JSON response. Do NOT call 'ls' to verify.",
    input_schema={
        "query": str, 
        "context_path": str,
    }
)
async def run_research_phase_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    """
    Wrapper for research phase (Crawl -> Refine) to run in-process.
    """
    query = args.get("query")
    raw_task_name = args.get("context_path", "default")
    workspace_hint = _resolve_workspace_hint(args)
    
    # Apply Guardrail
    task_name = resolve_best_task_match(raw_task_name)
    
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = await research_phase_core(query, task_name, workspace_dir=workspace_hint)
    return {"content": [{"type": "text", "text": result_str}]}

@tool(
    name="run_report_generation",
    description="Execute Phase 2 of Research: Outline -> Draft -> Compile. Produces report.html. EFFICIENCY: Unified turn. Do NOT use Bash to check for the report file after this.",
    input_schema={
        "query": str, 
        "context_path": str,
        "corpus_data": str,  # Option to provide corpus directly (for non-search tasks)
    }
)
async def run_report_generation_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    """
    Wrapper for report generation (Outline -> Compile) to run in-process.
    """
    query = args.get("query")
    raw_task_name = args.get("context_path", "default")
    corpus_data = args.get("corpus_data")
    workspace_hint = _resolve_workspace_hint(args)

    # If corpus_data is an existing refined_corpus.md path, align task_name to it
    if isinstance(corpus_data, str) and corpus_data.strip():
        candidate = Path(corpus_data.strip())
        if not candidate.is_absolute():
            workspace = _ctx_get_workspace()
            if workspace:
                candidate = Path(workspace) / candidate
        if candidate.exists():
            parts = candidate.parts
            if "tasks" in parts and candidate.name == "refined_corpus.md":
                task_index = parts.index("tasks") + 1
                if task_index < len(parts):
                    raw_task_name = parts[task_index]
                    corpus_data = None
    
    # Apply Guardrail
    task_name = resolve_best_task_match(raw_task_name)
    
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = await report_gen_core(
            query,
            task_name,
            corpus_data=corpus_data,
            workspace_dir=workspace_hint,
        )
    return {"content": [{"type": "text", "text": result_str}]}

@tool(
    name="generate_outline",
    description="Generate a report outline from the refined corpus.",
    input_schema={
        "topic": str,
        "context_path": str
    }
)
async def generate_outline_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    from mcp_server import generate_outline
    topic = args.get("topic")
    task_name = resolve_best_task_match(args.get("context_path", "default"))
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result = await generate_outline(topic, task_name)
    return {"content": [{"type": "text", "text": result}]}

@tool(
    name="draft_report_parallel",
    description="Execute the parallel drafting system to generate report sections.",
    input_schema={
        "context_path": str
    }
)
async def draft_report_parallel_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    from mcp_server import draft_report_parallel
    task_name = resolve_best_task_match(args.get("context_path", "default"))
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result = await draft_report_parallel(task_name=task_name)
    return {"content": [{"type": "text", "text": result}]}

@tool(
    name="cleanup_report",
    description="Run a cleanup pass over drafted report sections.",
    input_schema={}
)
async def cleanup_report_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    from mcp_server import cleanup_report
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result = await cleanup_report()
    return {"content": [{"type": "text", "text": result}]}

@tool(
    name="compile_report",
    description="Compile all section markdown files into a single HTML report.",
    input_schema={
        "theme": str
    }
)
async def compile_report_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    from mcp_server import compile_report
    theme = args.get("theme", "modern")
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result = await compile_report(theme=theme)
    return {"content": [{"type": "text", "text": result}]}
