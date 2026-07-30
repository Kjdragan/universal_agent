"""Session-ID and workspace-path security helpers for the gateway.

These validators back the gateway's path-handling layer: they keep
operator-supplied session IDs and workspace/log paths from escaping the
configured UA_WORKSPACES_DIR root (path-traversal / symlink-escape guard) and
reject session IDs that are unsafe to embed in filesystem paths or keys.
"""

from __future__ import annotations

import os
from pathlib import Path
import re

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def is_valid_session_id(value: str) -> bool:
    """Return True if value is a structurally valid session ID.

    A valid ID is 1-128 characters: an alphanumeric first character followed by
    zero or more of [A-Za-z0-9_.-]. Whitespace is stripped before matching, so
    leading/trailing spaces never salvage an otherwise invalid value. Leading
    separators (_ - .), slashes, spaces, and control/null bytes are rejected so
    the ID is safe to embed in paths and database keys.
    """
    return bool(_SESSION_ID_RE.match((value or "").strip()))


def validate_session_id(value: str) -> str:
    """Return the stripped session ID if valid; raise ValueError otherwise.

    Always returns the whitespace-stripped form. Raises ValueError with the
    message "Invalid session id format" when value is empty or fails
    is_valid_session_id.
    """
    session_id = (value or "").strip()
    if not is_valid_session_id(session_id):
        raise ValueError("Invalid session id format")
    return session_id


def _is_within(base_dir: Path, candidate: Path) -> bool:
    """Return True if candidate resolves to base_dir or somewhere beneath it."""
    try:
        candidate.resolve().relative_to(base_dir.resolve())
        return True
    except Exception:
        return False


def resolve_workspace_dir(
    workspaces_dir: Path,
    requested: str | None,
    *,
    allow_external: bool = False,
) -> str | None:
    """Resolve a requested workspace path and enforce UA_WORKSPACES_DIR containment.

    Relative paths are resolved under workspaces_dir; absolute paths are taken
    as-is. Returns None when requested is falsy (empty/None). Unless
    allow_external is True, the resolved path must remain at or beneath
    workspaces_dir or ValueError is raised -- this is the path-traversal and
    absolute-escape guard. The result is always returned as a string.
    """
    if not requested:
        return None

    candidate = Path(requested).expanduser()
    if not candidate.is_absolute():
        candidate = (workspaces_dir / candidate).resolve()
    else:
        candidate = candidate.resolve()

    if allow_external:
        return str(candidate)

    if not _is_within(workspaces_dir, candidate):
        raise ValueError("Workspace path must remain under UA_WORKSPACES_DIR")
    return str(candidate)


def resolve_ops_log_path(
    workspaces_dir: Path,
    candidate_path: str,
) -> Path:
    """Resolve an ops log file path, enforcing containment under workspaces_dir.

    Relative paths resolve under workspaces_dir; absolute paths are taken as-is.
    Unlike resolve_workspace_dir there is no allow_external escape: a log path
    that resolves outside workspaces_dir always raises ValueError. Returns a
    Path object (not a string).
    """
    candidate = Path(candidate_path).expanduser()
    if not candidate.is_absolute():
        candidate = (workspaces_dir / candidate).resolve()
    else:
        candidate = candidate.resolve()

    if not _is_within(workspaces_dir, candidate):
        raise ValueError("Log path must remain under UA_WORKSPACES_DIR")
    return candidate


def allow_external_workspaces_from_env() -> bool:
    """Return whether UA_ALLOW_EXTERNAL_WORKSPACES permits out-of-root paths.

    Truthy when the env var is one of 1/true/yes/on (case-insensitive); unset or
    any other value returns False.
    """
    return (os.getenv("UA_ALLOW_EXTERNAL_WORKSPACES") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
