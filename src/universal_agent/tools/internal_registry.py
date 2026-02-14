"""
Central registry for internal MCP tools.

This module provides a single source of truth for:
1. The list of tool wrappers to register in the internal MCP server.
2. The list of tool names (slugs) for documentation and discovery.
"""

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
    core_memory_replace_wrapper,
    core_memory_append_wrapper,
    archival_memory_insert_wrapper,
    archival_memory_search_wrapper,
    get_core_memory_blocks_wrapper,
    ask_user_questions_wrapper,
    batch_tool_execute_wrapper,
)

# PDF Bridge Tools
from universal_agent.tools.pdf_bridge import html_to_pdf_wrapper

# Memory Tools
from universal_agent.tools.memory import ua_memory_get_wrapper, ua_memory_search_wrapper

# X Trends (xAI/Grok x_search) evidence fetch
from universal_agent.tools.x_trends_bridge import x_trends_posts_wrapper

# Reddit (Composio-backed) compact structured output
from universal_agent.tools.reddit_bridge import reddit_top_posts_wrapper


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
        core_memory_replace_wrapper,
        core_memory_append_wrapper,
        archival_memory_insert_wrapper,
        archival_memory_search_wrapper,
        get_core_memory_blocks_wrapper,
        ask_user_questions_wrapper,
        batch_tool_execute_wrapper,
        x_trends_posts_wrapper,
        reddit_top_posts_wrapper,
    ]

def get_memory_tools() -> List[Callable]:
    """
    Return the list of memory-specific tool wrappers.
    """
    return [ua_memory_get_wrapper, ua_memory_search_wrapper]

def get_all_internal_tools(enable_memory: bool = False) -> List[Callable]:
    """
    Return the complete list of internal tool wrappers based on configuration.
    """
    tools = get_core_internal_tools()
    if enable_memory:
        tools.extend(get_memory_tools())
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
