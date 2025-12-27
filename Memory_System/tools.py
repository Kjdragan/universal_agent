from typing import Any, Dict
from .manager import MemoryManager

# Since tools in this project are often dynamically bound or use a specific signature style,
# strict separation isn't always necessary if Manager exposes standardized dicts.
# However, this file serves as the explicit adapter layer if we need complex logic.

# In this simple implementation, the Manager itself provides the tool definitions and execution logic
# is mapped directly to manager methods.

def get_memory_tool_map(manager: MemoryManager) -> Dict[str, Any]:
    """
    Returns a dictionary mapping tool names to their executable functions.
    This is used by the agent execution loop to call the right method.
    """
    return {
        "core_memory_replace": manager.core_memory_replace,
        "core_memory_append": manager.core_memory_append,
        "archival_memory_insert": manager.archival_memory_insert,
        "archival_memory_search": manager.archival_memory_search
    }
