from contextlib import contextmanager
from contextvars import ContextVar
import os
from typing import Callable, Generator, Optional

# Context-local run workspace path to support concurrent executions in a single process.
_WORKSPACE_CONTEXT_VAR: ContextVar[Optional[str]] = ContextVar("current_session_workspace", default=None)


def get_current_workspace() -> Optional[str]:
    """Retrieve the current run workspace path for this execution context."""
    return (
        _WORKSPACE_CONTEXT_VAR.get()
        or os.getenv("CURRENT_RUN_WORKSPACE")
        or os.getenv("CURRENT_SESSION_WORKSPACE")
    )


def bind_workspace_env(workspace_dir: str, absolute: bool = False) -> str:
    """Bind the workspace to the current execution environment.

    Uses ContextVar for per-task isolation in the concurrent gateway.
    Does NOT write to os.environ to prevent run-workspace leakage between
    concurrent executions sharing the same process.
    """
    resolved = os.path.abspath(workspace_dir) if absolute else workspace_dir
    
    # Update ContextVar only — this is per-asyncio-task, not process-global.
    # os.environ["CURRENT_SESSION_WORKSPACE"] was removed here because it
    # caused workspace leakage: concurrent executions in the gateway process
    # would overwrite each other's workspace path, leading to subagents
    # writing to the wrong run workspace.
    _WORKSPACE_CONTEXT_VAR.set(resolved)
    
    return resolved


def bind_workspace(
    workspace_dir: str,
    absolute: bool = False,
    observer_setter: Optional[Callable[[str], None]] = None,
) -> str:
    """Bind workspace and optionally notify an observer."""
    resolved = bind_workspace_env(workspace_dir, absolute=absolute)
    if observer_setter:
        observer_setter(resolved)
    return resolved


@contextmanager
def workspace_context(workspace_dir: str) -> Generator[str, None, None]:
    """Context manager to temporarily bind a workspace."""
    token = _WORKSPACE_CONTEXT_VAR.set(workspace_dir)
    try:
        yield workspace_dir
    finally:
        _WORKSPACE_CONTEXT_VAR.reset(token)
