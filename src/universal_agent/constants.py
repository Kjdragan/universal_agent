"""
Constants for Universal Agent.
"""

# Tools that should be blocked (legacy/problematic tools)
DISALLOWED_TOOLS = [
    "TaskOutput",
    "TaskResult",
    "taskoutput",
    "taskresult",
    "mcp__composio__TaskOutput",
    "mcp__composio__TaskResult",
    "WebSearch",
    "web_search",
    "mcp__composio__WebSearch",
    "mcp__local_toolkit__run_research_pipeline",  # REPLACED by in-process mcp__internal__run_research_pipeline
    "mcp__local_toolkit__crawl_parallel",  # REPLACED by in-process mcp__internal__crawl_parallel
    # Force delegation to research-specialist for search/research tasks
    "mcp__composio__COMPOSIO_SEARCH_TOOLS",
    "mcp__composio__COMPOSIO_SEARCH_NEWS",
    "mcp__composio__COMPOSIO_SEARCH_WEB",
]
