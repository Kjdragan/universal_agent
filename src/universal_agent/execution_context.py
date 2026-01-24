import os
from typing import Callable, Optional


def bind_workspace_env(workspace_dir: str, absolute: bool = False) -> str:
    resolved = os.path.abspath(workspace_dir) if absolute else workspace_dir
    os.environ["CURRENT_SESSION_WORKSPACE"] = resolved
    return resolved


def bind_workspace(
    workspace_dir: str,
    absolute: bool = False,
    observer_setter: Optional[Callable[[str], None]] = None,
) -> str:
    resolved = bind_workspace_env(workspace_dir, absolute=absolute)
    if observer_setter:
        observer_setter(resolved)
    return resolved
