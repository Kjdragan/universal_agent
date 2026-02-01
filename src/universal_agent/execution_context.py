import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Generator, Optional

# context-local workspace path to support concurrent sessions in a single process
_WORKSPACE_CONTEXT_VAR: ContextVar[Optional[str]] = ContextVar("current_session_workspace", default=None)


def get_current_workspace() -> Optional[str]:
    """Retrieve the workspace path for the current context."""
    return _WORKSPACE_CONTEXT_VAR.get() or os.getenv("CURRENT_SESSION_WORKSPACE")


def bind_workspace_env(workspace_dir: str, absolute: bool = False) -> str:
    """Bind the workspace to the current execution environment."""
    resolved = os.path.abspath(workspace_dir) if absolute else workspace_dir
    
    # 1. Update ContextVar (primary for modern engine/gateway)
    _WORKSPACE_CONTEXT_VAR.set(resolved)
    
    # 2. Sync with os.environ for legacy tools/subprocess compatibility
    os.environ["CURRENT_SESSION_WORKSPACE"] = resolved
    
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
