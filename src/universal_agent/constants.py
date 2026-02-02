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
    "mcp__local_toolkit__finalize_research",
    "mcp__local_toolkit__list_directory",
    "mcp__local_toolkit__append_to_file",
    "mcp__local_toolkit__upload_to_composio",
    "mcp__local_toolkit__generate_image",
    "mcp__local_toolkit__describe_image",
    "mcp__local_toolkit__preview_image",
    "mcp__local_toolkit__ask_user_questions",
    "mcp__local_toolkit__core_memory_replace",
    "mcp__local_toolkit__core_memory_append",
    "mcp__local_toolkit__archival_memory_insert",
    "mcp__local_toolkit__archival_memory_search",
    "mcp__local_toolkit__get_core_memory_blocks",
    "mcp__local_toolkit__batch_tool_execute",
    # Force delegation to research-specialist for search/research tasks
    "mcp__composio__COMPOSIO_SEARCH_TOOLS",
    "mcp__composio__COMPOSIO_SEARCH_NEWS",
    "mcp__composio__COMPOSIO_SEARCH_WEB",
    # 🚫 PRIMARY AGENT FORBIDDEN: NEVER use remote workbench directly. Delegate instead.
    "mcp__composio__COMPOSIO_REMOTE_WORKBENCH",
]
