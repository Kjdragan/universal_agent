"""
Constants for Universal Agent.
"""

# SDK-level disallowed tools: blocked for ALL agents (primary AND sub-agents).
# These are truly banned tools (hallucinated, deprecated, legacy aliases).
# IMPORTANT: Do NOT put tools here that sub-agents need. Use PRIMARY_ONLY_BLOCKED_TOOLS instead.
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
    # Deprecated local_toolkit aliases (replaced by in-process mcp__internal__ tools)
    "mcp__local_toolkit__run_research_pipeline",
    "mcp__local_toolkit__crawl_parallel",
    "mcp__local_toolkit__finalize_research",
    "mcp__local_toolkit__list_directory",
    "mcp__local_toolkit__append_to_file",
    "mcp__local_toolkit__upload_to_composio",
    "mcp__local_toolkit__generate_image",
    "mcp__local_toolkit__describe_image",
    "mcp__local_toolkit__preview_image",
    "mcp__local_toolkit__ask_user_questions",
    "mcp__local_toolkit__memory_search",
    "mcp__local_toolkit__memory_get",
    "mcp__local_toolkit__batch_tool_execute",
    # PRIMARY AGENT FORBIDDEN: NEVER use remote workbench directly.
    "mcp__composio__COMPOSIO_REMOTE_WORKBENCH",
    # Composio crawl/fetch tools are globally banned — ALL crawling goes through
    # Crawl4AI Cloud API via mcp__internal__run_research_phase / crawl_parallel.
    "mcp__composio__COMPOSIO_CRAWL_WEBPAGE",
    "mcp__composio__COMPOSIO_CRAWL_URL",
    "mcp__composio__COMPOSIO_CRAWL_WEBSITE",
    "mcp__composio__COMPOSIO_FETCH_URL",
    "mcp__composio__COMPOSIO_FETCH_WEBPAGE",
]

# Hook-level blocked tools: blocked for PRIMARY agent only; sub-agents are allowed.
# These are enforced by PreToolUse hooks (not the SDK disallowed_tools list).
# The hook checks subagent context and passes through for sub-agents.
PRIMARY_ONLY_BLOCKED_TOOLS = [
    # Research pipeline internals: Primary must delegate to research-specialist.
    "mcp__internal__run_research_pipeline",
    "mcp__internal__run_research_phase",
    # Search tools: Primary must delegate to research-specialist.
    "mcp__composio__COMPOSIO_SEARCH_NEWS",
    "mcp__composio__COMPOSIO_SEARCH_WEB",
]
