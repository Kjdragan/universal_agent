"""Identity resolution logic."""

import os
from typing import Optional


def resolve_user_id(requested_id: Optional[str] = None) -> str:
    """
    Resolve the effective user ID for the session.

    Logic:
    1. If `requested_id` is provided (e.g., from API request), use it.
    2. Check `COMPOSIO_USER_ID` env var.
    3. Check `DEFAULT_USER_ID` env var.
    4. Fallback to 'user_universal'.
    """
    if requested_id:
        return requested_id
    
    return (
        os.getenv("COMPOSIO_USER_ID")
        or os.getenv("DEFAULT_USER_ID")
        or "user_universal"
    )
