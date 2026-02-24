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

# Hook-level blocked tools: INTENTIONALLY EMPTY.
# Subagent detection in PreToolUse hooks is UNRELIABLE for foreground Task calls.
# transcript_path may not differ; parent_tool_use_id is NOT in PreToolUseHookInput.
# See docs/002_SDK_PERMISSIONS_HOOKS_SUBAGENTS.md for details.
# Rely on prompt-level delegation (prompt_builder.py) to steer the primary agent.
PRIMARY_ONLY_BLOCKED_TOOLS: list[str] = []
