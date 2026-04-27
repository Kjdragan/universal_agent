import threading
from typing import Dict, Optional

# Maps main_session_id -> sidebar_session_id
_sidebar_lock = threading.Lock()
_active_sidebars: Dict[str, str] = {}


def set_active_sidebar(main_session_id: str, sidebar_session_id: str) -> None:
    """Set the active sidebar session for a main session."""
    with _sidebar_lock:
        _active_sidebars[main_session_id] = sidebar_session_id


def get_active_sidebar(main_session_id: str) -> Optional[str]:
    """Get the active sidebar session for a main session, if any."""
    with _sidebar_lock:
        return _active_sidebars.get(main_session_id)


def clear_active_sidebar(main_session_id: str) -> Optional[str]:
    """Clear the active sidebar session for a main session and return it if it existed."""
    with _sidebar_lock:
        return _active_sidebars.pop(main_session_id, None)
