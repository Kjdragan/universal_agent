from typing import Any
from claude_agent_sdk import tool
import sys
import os

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

@tool(
    name="run_research_pipeline", 
    description="Execute the Post-Search Research Pipeline: Crawl -> Refine -> Outline -> Draft -> Cleanup -> Compile.",
    input_schema={
        "query": str, 
        "task_name": str
    }
)
async def run_research_pipeline_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    """
    Wrapper for the research pipeline to run in-process.
    """
    query = args.get("query")
    raw_task_name = args.get("task_name", "default")
    
    # Apply Guardrail
    task_name = resolve_best_task_match(raw_task_name)
    
    # Execute the original function directly
    # Since it runs in this process, its print/stderr writes will go to our console
    # [ENHANCED] Capture stdout and bridge to Web UI events
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = await original_pipeline(query, task_name)
    
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
    description="Execute Phase 1 of Research: Crawl & Refine. Produces refined_corpus.md.",
    input_schema={
        "query": str, 
        "task_name": str
    }
)
async def run_research_phase_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    """
    Wrapper for research phase (Crawl -> Refine) to run in-process.
    """
    query = args.get("query")
    raw_task_name = args.get("task_name", "default")
    
    # Apply Guardrail
    task_name = resolve_best_task_match(raw_task_name)
    
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = await research_phase_core(query, task_name)
    return {"content": [{"type": "text", "text": result_str}]}

@tool(
    name="run_report_generation",
    description="Execute Phase 2 of Research: Outline -> Draft -> Cleanup -> Compile Report.",
    input_schema={
        "query": str, 
        "task_name": str,
        "corpus_data": str  # Option to provide corpus directly (for non-search tasks)
    }
)
async def run_report_generation_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    """
    Wrapper for report generation (Outline -> Compile) to run in-process.
    """
    query = args.get("query")
    raw_task_name = args.get("task_name", "default")
    corpus_data = args.get("corpus_data")
    
    # Apply Guardrail
    task_name = resolve_best_task_match(raw_task_name)
    
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = await report_gen_core(query, task_name, corpus_data=corpus_data)
    return {"content": [{"type": "text", "text": result_str}]}

@tool(
    name="generate_outline",
    description="Generate a report outline from the refined corpus.",
    input_schema={
        "topic": str,
        "task_name": str
    }
)
async def generate_outline_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    from mcp_server import generate_outline
    topic = args.get("topic")
    task_name = resolve_best_task_match(args.get("task_name", "default"))
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result = await generate_outline(topic, task_name)
    return {"content": [{"type": "text", "text": result}]}

@tool(
    name="draft_report_parallel",
    description="Execute the parallel drafting system to generate report sections.",
    input_schema={
        "task_name": str
    }
)
async def draft_report_parallel_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    from mcp_server import draft_report_parallel
    task_name = resolve_best_task_match(args.get("task_name", "default"))
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
