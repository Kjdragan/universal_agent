"""
Shared search tool config mapping used by both CLI and MCP server.
"""

SEARCH_TOOL_CONFIG = {
    # Web & Default
    "COMPOSIO_SEARCH": {"list_key": "results", "url_key": "url"},
    "COMPOSIO_SEARCH_WEB": {"list_key": "results", "url_key": "url"},
    "COMPOSIO_SEARCH_TAVILY": {"list_key": "results", "url_key": "url"},
    "COMPOSIO_SEARCH_DUCK_DUCK_GO": {"list_key": "results", "url_key": "url"},
    "COMPOSIO_SEARCH_EXA_ANSWER": {"list_key": "results", "url_key": "url"},
    "COMPOSIO_SEARCH_GROQ_CHAT": {"list_key": "choices", "url_key": "message"},
    # News & Articles
    "COMPOSIO_SEARCH_NEWS": {"list_key": "articles", "url_key": "url"},
    "COMPOSIO_SEARCH_SCHOLAR": {"list_key": "articles", "url_key": "link"},
    # Products & Services
    "COMPOSIO_SEARCH_AMAZON": {"list_key": "data", "url_key": "product_url"},
    "COMPOSIO_SEARCH_SHOPPING": {"list_key": "data", "url_key": "product_url"},
    "COMPOSIO_SEARCH_WALMART": {"list_key": "data", "url_key": "product_url"},
    # Travel & Events
    "COMPOSIO_SEARCH_FLIGHTS": {"list_key": "data", "url_key": "booking_url"},
    "COMPOSIO_SEARCH_HOTELS": {"list_key": "data", "url_key": "url"},
    "COMPOSIO_SEARCH_EVENT": {"list_key": "data", "url_key": "link"},
    "COMPOSIO_SEARCH_TRIP_ADVISOR": {"list_key": "data", "url_key": "url"},
    # Other
    "COMPOSIO_SEARCH_IMAGE": {"list_key": "data", "url_key": "original_url"},
    "COMPOSIO_SEARCH_FINANCE": {"list_key": "data", "url_key": "link"},
    "COMPOSIO_SEARCH_GOOGLE_MAPS": {"list_key": "data", "url_key": "google_maps_link"},
}
