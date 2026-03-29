"""
Central registry for internal MCP tools.

This module provides a single source of truth for:
1. The list of tool wrappers to register in the internal MCP server.
2. The list of tool names (slugs) for documentation and discovery.
"""

import os
from typing import List, Callable, Any

# Research Bridge Tools
from universal_agent.tools.research_bridge import (
    run_research_pipeline_wrapper,
    crawl_parallel_wrapper,
    run_report_generation_wrapper,
    run_research_phase_wrapper,
    generate_outline_wrapper,
    draft_report_parallel_wrapper,
    cleanup_report_wrapper,
    compile_report_wrapper,
)

# Local Toolkit Bridge Tools
from universal_agent.tools.local_toolkit_bridge import (
    upload_to_composio_wrapper,
    list_directory_wrapper,
    inspect_session_workspace_wrapper,
    append_to_file_wrapper,
    write_text_file_wrapper,
    finalize_research_wrapper,
    generate_image_wrapper,
    generate_image_with_review_wrapper,
    describe_image_wrapper,
    preview_image_wrapper,
    ask_user_questions_wrapper,
    batch_tool_execute_wrapper,
)

# PDF Bridge Tools
from universal_agent.tools.pdf_bridge import html_to_pdf_wrapper
# Mermaid Bridge Tools
from universal_agent.tools.mermaid_bridge import mermaid_to_image

# Memory Tools
from universal_agent.tools.memory import memory_get_wrapper, memory_search_wrapper

# X Trends (xAI/Grok x_search) evidence fetch
from universal_agent.tools.x_trends_bridge import x_trends_posts_wrapper

# Reddit (Composio-backed) compact structured output
from universal_agent.tools.reddit_bridge import reddit_top_posts_wrapper

# CSI bridge tools (read-only CSI reports/opportunities/health/watchlist snapshots)
from universal_agent.tools.csi_bridge import (
    csi_recent_reports_wrapper,
    csi_opportunity_bundles_wrapper,
    csi_source_health_wrapper,
    csi_watchlist_snapshot_wrapper,
)

from universal_agent.tools.task_hub_bridge import task_hub_task_action_wrapper
from universal_agent.tools.vp_orchestration import (
    vp_cancel_mission_wrapper,
    vp_dispatch_mission_wrapper,
    vp_get_mission_wrapper,
    vp_list_missions_wrapper,
    vp_read_result_artifacts_wrapper,
    vp_wait_mission_wrapper,
)

# Live Chrome Bridge (CDP session attachment) — feature-gated
from universal_agent.tools.live_chrome_bridge import LIVE_CHROME_TOOLS

# AgentMail Tool
from universal_agent.tools.agentmail_bridge import mcp__internal__send_agentmail


def get_core_internal_tools() -> List[Callable]:
    """
    Return the list of core internal tool wrappers that are always enabled.
    """
    return [
        run_report_generation_wrapper, 
        run_research_pipeline_wrapper, 
        crawl_parallel_wrapper, 
        run_research_phase_wrapper,
        generate_outline_wrapper,
        draft_report_parallel_wrapper,
        cleanup_report_wrapper,
        compile_report_wrapper,
        upload_to_composio_wrapper,
        list_directory_wrapper,
        inspect_session_workspace_wrapper,
        append_to_file_wrapper,
        write_text_file_wrapper,
        finalize_research_wrapper,
        generate_image_wrapper,
        generate_image_with_review_wrapper,
        describe_image_wrapper,
        preview_image_wrapper,
        html_to_pdf_wrapper,
        mermaid_to_image,
        ask_user_questions_wrapper,
        batch_tool_execute_wrapper,
        x_trends_posts_wrapper,
        reddit_top_posts_wrapper,
        csi_recent_reports_wrapper,
        csi_opportunity_bundles_wrapper,
        csi_source_health_wrapper,
        csi_watchlist_snapshot_wrapper,
        task_hub_task_action_wrapper,
        mcp__internal__send_agentmail,
        vp_dispatch_mission_wrapper,
        vp_get_mission_wrapper,
        vp_list_missions_wrapper,
        vp_wait_mission_wrapper,
        vp_cancel_mission_wrapper,
        vp_read_result_artifacts_wrapper,
    ]

def get_memory_tools() -> List[Callable]:
    """
    Return the list of memory-specific tool wrappers.
    """
    return [memory_get_wrapper, memory_search_wrapper]

def get_live_chrome_tools() -> List[Callable]:
    """
    Return the Live Chrome CDP tools if the feature flag is enabled.
    """
    if os.getenv("UA_ENABLE_LIVE_CHROME", "0") == "1":
        return list(LIVE_CHROME_TOOLS)
    return []


def get_all_internal_tools(enable_memory: bool = False) -> List[Callable]:
    """
    Return the complete list of internal tool wrappers based on configuration.
    """
    tools = get_core_internal_tools()
    if enable_memory:
        tools.extend(get_memory_tools())
    tools.extend(get_live_chrome_tools())
    return tools

def get_internal_tool_slugs(enable_memory: bool = False) -> List[str]:
    """
    Return a list of tool slugs (names) for all configured internal tools.
    This is used for documentation and capability discovery.
    """
    # We assume the wrapper function name (or __name__) isn't the final slug,
    # but the SDK registers them. Usually the tool name is derived from the function name
    # or the @tool decorator.
    # For documentation purposes, we can get the 'name' attribute if it exists,
    # or use a convention.
    # The 'claude_agent_sdk' register_tool usually uses the function name if not specified.
    # However, create_sdk_mcp_server inspects them.
    
    # Actually, we can just instantiate them or peek at their metadata if possible.
    # But for now, let's rely on the fact that we know their mcp names are usually `mcp_internal_{name}`
    # OR, better yet, we just return the list of function names and let the consumer format them,
    # or we construct the likely MCP tool name.
    
    # In composio_discovery.py, the list was hardcoded as "mcp__internal__..."
    # We should stick to that convention if that's what the agent sees.
    
    tools = get_all_internal_tools(enable_memory)
    slugs = []
    for tool in tools:
        if hasattr(tool, 'name'):
            name = tool.name
        else:
            name = getattr(tool, "__name__", str(tool))
            
        # Remove _wrapper suffix if present for cleaner names, or keep it?
        # The tool name usually matches the function name.
        if name.endswith("_wrapper"):
            name = name[:-8]
            
        slugs.append(name)
        
    return slugs
