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
except ImportError:
    # Fallback for when running from different contexts
    from src.mcp_server import _run_research_pipeline_legacy as original_pipeline
    from src.mcp_server import _crawl_core

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
    task_name = args.get("task_name", "default")
    
    # Execute the original function directly
    # Since it runs in this process, its print/stderr writes will go to our console
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
    
    result_str = await _crawl_core(urls, session_dir)
    
    return {
        "content": [{
            "type": "text",
            "text": result_str
        }]
    }
