"""
Workspace Path Guardrail

Ensures all file operations are scoped to the session workspace.
Prevents cross-workspace writes that caused Web UI divergence.

This guardrail is critical for the unified gateway architecture:
- All clients (CLI, Web UI, Harness) must write to the same workspace
- Prevents accidental writes to repo root or other locations
- Enforces consistent output paths across all entry points
"""

from pathlib import Path
from typing import Optional, Union


class WorkspaceGuardError(Exception):
    """Raised when a path escapes the workspace boundary."""
    pass


def enforce_workspace_path(
    file_path: Union[str, Path],
    workspace_root: Path,
    allow_reads_outside: bool = False,
    operation: str = "access",
) -> Path:
    """
    Validate and resolve a file path within workspace boundaries.
    
    Args:
        file_path: The path to validate
        workspace_root: The session workspace root
        allow_reads_outside: If True, allow reads from outside (e.g., /tmp)
        operation: Description of operation (for error messages)
    
    Returns:
        Resolved absolute path within workspace
    
    Raises:
        WorkspaceGuardError: If path escapes workspace
    
    Examples:
        >>> workspace = Path("/workspaces/session_001")
        >>> enforce_workspace_path("output.txt", workspace)
        PosixPath('/workspaces/session_001/output.txt')
        
        >>> enforce_workspace_path("../other/file.txt", workspace)
        WorkspaceGuardError: Path '../other/file.txt' resolves outside workspace
    """
    if isinstance(file_path, str):
        file_path = Path(file_path)
    
    # Handle relative paths - resolve relative to workspace
    if not file_path.is_absolute():
        resolved = (workspace_root / file_path).resolve()
    else:
        resolved = file_path.resolve()
    
    root = workspace_root.resolve()
    
    # Check if path is inside workspace
    try:
        resolved.relative_to(root)
        return resolved
    except ValueError:
        if allow_reads_outside:
            return resolved
        raise WorkspaceGuardError(
            f"Path '{file_path}' resolves to '{resolved}' which is outside "
            f"workspace '{root}'. All {operation} operations must be inside "
            "the session workspace."
        )


def workspace_scoped_path(
    file_path: Union[str, Path],
    workspace_root: Path,
    create_parents: bool = False,
) -> Path:
    """
    Convert a path to an absolute workspace-scoped path.
    
    If path is relative, makes it relative to workspace_root.
    If path is absolute, validates it's inside workspace.
    
    Args:
        file_path: The path to scope
        workspace_root: The session workspace root
        create_parents: If True, create parent directories
    
    Returns:
        Absolute path inside workspace
    
    Raises:
        WorkspaceGuardError: If absolute path is outside workspace
    """
    resolved = enforce_workspace_path(file_path, workspace_root, operation="write")
    
    if create_parents:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    
    return resolved


def validate_tool_paths(
    tool_input: dict,
    workspace_root: Path,
    path_keys: Optional[list[str]] = None,
) -> dict:
    """
    Validate and rewrite file paths in tool input to be workspace-scoped.
    
    Args:
        tool_input: The tool's input dictionary
        workspace_root: The session workspace root
        path_keys: Keys to check for file paths (default: common path keys)
    
    Returns:
        Modified tool_input with validated paths
    
    Raises:
        WorkspaceGuardError: If any path escapes workspace
    """
    if path_keys is None:
        path_keys = [
            "path",
            "file_path",
            "filepath",
            "destination",
            "output_path",
            "output",
            "target",
            "target_path",
            "save_path",
        ]
    
    modified = tool_input.copy()
    
    for key in path_keys:
        if key in modified and isinstance(modified[key], str):
            original = modified[key]
            try:
                scoped = workspace_scoped_path(original, workspace_root)
                modified[key] = str(scoped)
            except WorkspaceGuardError:
                # Re-raise with more context
                raise WorkspaceGuardError(
                    f"Tool input '{key}' contains path '{original}' which "
                    f"is outside the session workspace '{workspace_root}'"
                )
    
    return modified


def is_inside_workspace(
    file_path: Union[str, Path],
    workspace_root: Path,
) -> bool:
    """
    Check if a path is inside the workspace (non-throwing version).
    
    Args:
        file_path: The path to check
        workspace_root: The session workspace root
    
    Returns:
        True if path is inside workspace, False otherwise
    """
    try:
        enforce_workspace_path(file_path, workspace_root)
        return True
    except WorkspaceGuardError:
        return False


def get_workspace_relative_path(
    file_path: Union[str, Path],
    workspace_root: Path,
) -> Optional[Path]:
    """
    Get the workspace-relative portion of a path.
    
    Args:
        file_path: The absolute path
        workspace_root: The session workspace root
    
    Returns:
        Relative path from workspace root, or None if outside workspace
    
    Example:
        >>> get_workspace_relative_path("/ws/session_001/work_products/out.txt", Path("/ws/session_001"))
        PosixPath('work_products/out.txt')
    """
    if isinstance(file_path, str):
        file_path = Path(file_path)
    
    resolved = file_path.resolve()
    root = workspace_root.resolve()
    
    try:
        return resolved.relative_to(root)
    except ValueError:
        return None
