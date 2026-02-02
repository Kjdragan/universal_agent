
"""
Module for discovering an enumerating available tools.

This handles:
1. Dynamic discovery of remote toolkits via Composio SDK.
2. Definition of local MCP tools exposed by our local server.
"""

from typing import List, Dict, Any
import os

try:
    from composio import Composio
except ImportError:
    Composio = None

INPROCESS_MCP_TOOLS = [
    "mcp__internal__run_research_pipeline",
    "mcp__internal__run_research_phase",
    "mcp__internal__crawl_parallel",
    "mcp__internal__run_report_generation",
    "mcp__internal__generate_outline",
    "mcp__internal__draft_report_parallel",
    "mcp__internal__cleanup_report",
    "mcp__internal__compile_report",
    "mcp__internal__upload_to_composio",
    "mcp__internal__list_directory",
    "mcp__internal__append_to_file",
    "mcp__internal__finalize_research",
    "mcp__internal__generate_image",
    "mcp__internal__describe_image",
    "mcp__internal__preview_image",
    "mcp__internal__html_to_pdf",
    "mcp__internal__batch_tool_execute",
    "mcp__internal__core_memory_replace",
    "mcp__internal__core_memory_append",
    "mcp__internal__archival_memory_insert",
    "mcp__internal__archival_memory_search",
    "mcp__internal__get_core_memory_blocks",
    "mcp__internal__ask_user_questions",
]

def get_local_tools() -> List[str]:
    """Return the list of tools available in the in-process MCP Toolkit."""
    return INPROCESS_MCP_TOOLS


def discover_connected_toolkits(composio_client: Any, user_id: str) -> List[str]:
    """
    Discover toolkits that have an active connection for the specific user.
    Uses client.connected_accounts.list() which is more reliable for persistent connections
    than session.toolkits().
    
    Args:
        composio_client: Global Composio client.
        user_id: The user ID to check connections for.
        
    Returns:
        List[str]: A sorted list of slugs for toolkits with active connections.
    """
    connected_slugs = []
    try:
        # Use the global list filtered by user_id, exactly like the audit script that worked
        connections_response = composio_client.connected_accounts.list(user_ids=[user_id])
        
        if hasattr(connections_response, 'items'):
            for item in connections_response.items:
                 if hasattr(item, 'toolkit') and item.toolkit and hasattr(item.toolkit, 'slug'):
                     # Only add if status is active (audit script logic)
                     # The audit script showed 'status': 'ACTIVE' in the data
                     is_active = False
                     if hasattr(item, 'status') and item.status == 'ACTIVE':
                         is_active = True
                     elif hasattr(item, 'connection') and hasattr(item.connection, 'status') and item.connection.status == 'ACTIVE':
                         is_active = True
                     
                     if is_active:
                         connected_slugs.append(item.toolkit.slug)
        
        # Tools that are "always on" / utilities that don't need auth
        # We explicitly list them so the user sees the full capability set in the terminal
        defaults = ['codeinterpreter', 'composio_search', 'sqltool', 'filetool']
        for d in defaults:
            if d not in connected_slugs:
                connected_slugs.append(d)
            
        connected_slugs = list(set(connected_slugs))
        connected_slugs.sort()
        
    except Exception as e:
        print(f"⚠️ [Discovery] Failed to fetch user connections: {e}")
        return []

    return connected_slugs

def discover_composio_apps(composio_client: Any) -> List[str]:
    """
    DEPRECATED: Use discover_connected_toolkits(session) instead.
    Connect to Composio and discover all active toolkits (apps) via client.
    """
    if not composio_client:
        return []
        
    discovered_apps = []
    try:
        connections_response = composio_client.connected_accounts.list()
        
        if hasattr(connections_response, 'items'):
            for item in connections_response.items:
                 if hasattr(item, 'toolkit') and item.toolkit and hasattr(item.toolkit, 'slug'):
                     discovered_apps.append(item.toolkit.slug)
        
        discovered_apps = list(set(discovered_apps))
        discovered_apps.sort()
        
    except Exception as e:
        print(f"⚠️ [Discovery] Failed to fetch active Composio apps: {e}")
        return []
        
    return discovered_apps
