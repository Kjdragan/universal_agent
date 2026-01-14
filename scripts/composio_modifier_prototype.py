#!/usr/bin/env python
"""
Composio Session Modifiers Prototype

This file demonstrates how to use Composio's session modifiers to clean and format
tool execution responses BEFORE the LLM sees them.

Key Finding: The `after_execute_meta` modifier can transform ToolExecutionResponse.data
which is a Dict. This means we can:
1. Remove verbose/redundant fields
2. Format data into clean markdown
3. Aggregate multiple results
4. Normalize response formats across different tools

Usage:
    cd /home/kjdragan/lrepos/universal_agent
    uv run python scripts/composio_modifier_prototype.py
"""

import json
from typing import Dict, Any, List

# Import the modifier decorator and types
from composio.core.models._modifiers import (
    after_execute_meta,
    AfterExecuteMeta,
    Modifier,
)

# For type checking (ToolExecutionResponse structure):
# - data: Dict[str, Any]  <-- THE KEY FIELD WE CAN MODIFY
# - error: Optional[str]
# - successful: bool


def clean_search_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Clean search results to remove bloat and keep only essential info.
    
    Input: Raw Composio search response with nested results
    Output: Clean, LLM-friendly format
    """
    cleaned = {"type": "search_results", "items": []}
    
    # Handle different search result formats
    results = data.get("data", data)
    
    # News results
    if "news_results" in results:
        for item in results.get("news_results", [])[:10]:  # Limit to 10
            cleaned["items"].append({
                "title": item.get("title"),
                "source": item.get("source", {}).get("name") if isinstance(item.get("source"), dict) else item.get("source"),
                "url": item.get("link"),
                "snippet": item.get("snippet", "")[:300],  # Truncate
                "date": item.get("date"),
            })
    
    # Web results
    elif "organic_results" in results:
        for item in results.get("organic_results", [])[:10]:
            cleaned["items"].append({
                "title": item.get("title"),
                "url": item.get("link"),
                "snippet": item.get("snippet", "")[:300],
                "position": item.get("position"),
            })
    
    # Default: return trimmed version
    else:
        # Just trim long strings
        cleaned["items"] = [_trim_dict(results)]
    
    return cleaned


def _trim_dict(d: Any, max_str_len: int = 500, max_list_len: int = 10) -> Any:
    """Recursively trim dict values to reasonable lengths."""
    if isinstance(d, dict):
        return {k: _trim_dict(v, max_str_len, max_list_len) for k, v in d.items()}
    elif isinstance(d, list):
        return [_trim_dict(item, max_str_len, max_list_len) for item in d[:max_list_len]]
    elif isinstance(d, str) and len(d) > max_str_len:
        return d[:max_str_len] + "..."
    return d


# =============================================================================
# MODIFIER DEFINITIONS
# =============================================================================

# NOTE: These modifiers use the decorator syntax from Composio SDK v0.10.6

# Modifier 1: Clean search results for all SERP-related tools
@after_execute_meta(toolkits=["composio_search", "serpapi"])
def clean_search_modifier(
    tool: str,
    toolkit: str,
    session_id: str,
    response: Any,  # ToolExecutionResponse
) -> Any:
    """
    Cleans search results before LLM sees them.
    
    This modifier:
    1. Removes verbose metadata
    2. Limits results to top 10
    3. Truncates long snippets
    4. Standardizes format
    """
    if not response.successful:
        return response
    
    try:
        response.data = clean_search_response(response.data)
    except Exception:
        pass  # Fail silently, return original
    
    return response


# Modifier 2: Add metadata to all responses for debugging
@after_execute_meta(tools=[], toolkits=[])  # Apply to ALL tools
def add_metadata_modifier(
    tool: str,
    toolkit: str, 
    session_id: str,
    response: Any,
) -> Any:
    """
    Adds processing metadata to responses.
    """
    if response.successful and isinstance(response.data, dict):
        response.data["_meta"] = {
            "tool": tool,
            "toolkit": toolkit,
            "session_id": session_id[:8] + "...",
            "processed": True,
        }
    return response


# Modifier 3: Format email results
@after_execute_meta(toolkits=["gmail"])
def clean_email_modifier(
    tool: str,
    toolkit: str,
    session_id: str,
    response: Any,
) -> Any:
    """
    Cleans email responses to show only relevant fields.
    """
    if not response.successful:
        return response
    
    data = response.data
    
    # For email list responses
    if "messages" in data:
        cleaned_messages = []
        for msg in data.get("messages", [])[:20]:  # Limit to 20
            cleaned_messages.append({
                "id": msg.get("id"),
                "from": msg.get("from"),
                "subject": msg.get("subject"),
                "date": msg.get("date"),
                "snippet": msg.get("snippet", "")[:200],
            })
        response.data = {"messages": cleaned_messages, "count": len(cleaned_messages)}
    
    return response


# =============================================================================
# DEMO: How to use modifiers with Composio session
# =============================================================================

def demo_modifier_usage():
    """
    Demonstrates how to use modifiers with a Composio session.
    
    NOTE: This is pseudo-code showing the integration pattern.
    """
    print("=" * 60)
    print("COMPOSIO SESSION MODIFIERS - INTEGRATION PATTERN")
    print("=" * 60)
    
    print("""
# 1. Define your modifiers using the decorator

@after_execute_meta(toolkits=["composio_search"])
def my_search_cleaner(tool, toolkit, session_id, response):
    # Clean response.data
    response.data = clean_format(response.data)
    return response


# 2. When getting tools from session, pass modifiers

tools = session.tools(
    modifiers=[
        my_search_cleaner,
        # add_metadata_modifier,  # Can add multiple
    ]
)


# 3. The modifiers are applied automatically when tools execute
# Response.data is cleaned BEFORE the LLM sees it

# BENEFITS:
# - LLM sees less token bloat
# - Consistent format across tools
# - Can aggregate/summarize before LLM processing
# - Separates data cleaning from agent logic
""")
    
    print("\n" + "=" * 60)
    print("MODIFIER FUNCTION SIGNATURES")
    print("=" * 60)
    
    print("""
# BeforeExecuteMeta - Modify params BEFORE tool runs
def before_modifier(
    tool: str,           # Tool name (e.g., "COMPOSIO_SEARCH_NEWS")
    toolkit: str,        # Toolkit name (e.g., "composio_search")
    session_id: str,     # Current session ID
    params: Dict,        # Input parameters (can modify!)
) -> Dict:
    # Modify params and return
    return params


# AfterExecuteMeta - Modify response AFTER tool runs
def after_modifier(
    tool: str,
    toolkit: str,
    session_id: str,
    response: ToolExecutionResponse,  # Has: data, error, successful
) -> ToolExecutionResponse:
    # Modify response.data and return
    return response


# SchemaModifier - Modify tool schema (for changing descriptions/params shown to LLM)
def schema_modifier(
    tool: str,
    toolkit: str,
    schema: Tool,  # The tool definition
) -> Tool:
    return schema
""")


if __name__ == "__main__":
    demo_modifier_usage()
    
    # Test the clean_search_response function
    print("\n" + "=" * 60)
    print("TESTING clean_search_response()")
    print("=" * 60)
    
    sample_data = {
        "data": {
            "news_results": [
                {
                    "title": "AI Breakthrough in 2025",
                    "source": {"name": "TechCrunch"},
                    "link": "https://example.com/article1",
                    "snippet": "A very long snippet that goes on and on about artificial intelligence developments and breakthroughs in the year 2025, covering various topics including large language models, autonomous agents, and more...",
                    "date": "2 hours ago",
                },
                {
                    "title": "Quantum Computing Update",
                    "source": "Wired",
                    "link": "https://example.com/article2",
                    "snippet": "Quantum computing news",
                    "date": "1 day ago",
                }
            ]
        }
    }
    
    cleaned = clean_search_response(sample_data)
    print(json.dumps(cleaned, indent=2))
