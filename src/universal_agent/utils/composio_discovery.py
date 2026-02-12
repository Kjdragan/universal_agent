
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

try:
    from universal_agent.tools.internal_registry import get_internal_tool_slugs
    # Build list dynamically. We default to including memory tools for documentation completeness,
    # or we could make it conditional if we had access to config here.
    # For now, listing all possible internal tools is safer for discovery.
    INPROCESS_MCP_TOOLS = get_internal_tool_slugs(enable_memory=True)
except ImportError:
    # Fallback if registry is missing (e.g. during partial refactors)
    INPROCESS_MCP_TOOLS = [
        "run_research_pipeline",
        "run_research_phase",
        "crawl_parallel",
        "run_report_generation",
        "generate_outline",
        "draft_report_parallel",
        "cleanup_report",
        "compile_report",
        "upload_to_composio",
        "list_directory",
        "inspect_session_workspace",
        "append_to_file",
        "finalize_research",
        "generate_image",
        "describe_image",
        "preview_image",
        "html_to_pdf",
        "batch_tool_execute",
        "core_memory_replace",
        "core_memory_append",
        "archival_memory_insert",
        "archival_memory_search",
        "get_core_memory_blocks",
        "ask_user_questions",
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
        # We increase the limit to ensure we see all connections for the user
        connections_response = composio_client.connected_accounts.list(user_ids=[user_id], limit=50)
        
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

def fetch_toolkit_meta(composio_client: Any, slug: str) -> Dict[str, Any]:
    """
    Fetch metadata for a specific toolkit slug.
    """
    meta = {"slug": slug, "description": "", "name": slug.title()}
    try:
        tk = composio_client.toolkits.get(slug)
        if hasattr(tk, 'description') and tk.description:
            meta['description'] = tk.description
        if hasattr(tk, 'name') and tk.name:
            meta['name'] = tk.name
        
        # Additional metadata if available
        if hasattr(tk, 'meta') and hasattr(tk.meta, 'categories'):
            meta['categories'] = [c.name for c in tk.meta.categories]
            
    except Exception as e:
        # print(f"⚠️ [Discovery] Failed to fetch metadata for {slug}: {e}")
        pass

    # Fallbacks if description is still empty
    if not meta['description']:
        if slug == 'codeinterpreter':
            meta['description'] = "Executes Python code in a sandboxed environment for calculation, data analysis, and logic."
        elif slug == 'composio_search':
            meta['description'] = "Search engine for finding appropriate tools and actions within the Composio ecosystem."
        elif slug == 'sqltool':
            meta['description'] = "Execute SQL queries against connected databases."
        elif slug == 'filetool':
            meta['description'] = "Read, write, and manage files in the local workspace."
        elif slug == 'browserbase':
            meta['description'] = "Headless browser for web scraping and interaction."
        elif slug == 'gmail':
            meta['description'] = "Google's email service."
        elif slug == 'github':
            meta['description'] = "Code hosting and collaboration platform."
            
    return meta

def discover_connected_toolkits_with_meta(composio_client: Any, user_id: str) -> List[Dict[str, Any]]:
    """
    Discover toolkits with metadata (description, category) for active connections.
    """
    slugs = discover_connected_toolkits(composio_client, user_id)
    results = []
    
    for slug in slugs:
        results.append(fetch_toolkit_meta(composio_client, slug))
        
    return results

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
