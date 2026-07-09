"""Security helpers for validating session identifiers and confining
filesystem paths to a workspace root.

These functions are the boundary that keeps caller-supplied session ids and
workspace/log paths from escaping ``UA_WORKSPACES_DIR``. They guard against
path traversal and injection on the gateway and execution paths.
"""

from __future__ import annotations

import os
from pathlib import Path
import re

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def is_valid_session_id(value: str) -> bool:
    """Return ``True`` if ``value`` is a safe session identifier.

    A valid id is 1-128 characters long, starts with an alphanumeric
    character, and otherwise contains only ``[A-Za-z0-9_.-]``. This
    rejects empty/whitespace strings, leading separators (``_``, ``-``,
    ``.``), path separators, whitespace, and control/null bytes. ``None``
    and other non-str inputs are treated as invalid rather than raising.
    """
    return bool(_SESSION_ID_RE.match((value or "").strip()))


def validate_session_id(value: str) -> str:
    """Validate and return a cleaned session identifier.

    Strips surrounding whitespace and returns the id when it passes
    :func:`is_valid_session_id`; otherwise raises ``ValueError`` with the
    message ``"Invalid session id format"``. Use this at trust boundaries
    where an invalid id should fail loudly instead of being treated as
    falsy.
    """
    session_id = (value or "").strip()
    if not is_valid_session_id(session_id):
        raise ValueError("Invalid session id format")
    return session_id


def _is_within(base_dir: Path, candidate: Path) -> bool:
    """Return ``True`` when ``candidate`` is ``base_dir`` itself or beneath it.

    Both paths are :meth:`~pathlib.Path.resolve`'d first, so traversal
    segments (``..``) and symlinks are normalized before the containment
    check. Returns ``False`` on any resolution failure rather than raising.
    """
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
    """Resolve a requested workspace directory against ``workspaces_dir``.

    Relative paths are interpreted underneath ``workspaces_dir``; absolute
    paths are taken as-is. Returns the resolved path as a string, or
    ``None`` when ``requested`` is empty/``None``.

    Unless ``allow_external`` is ``True`` (the operator opt-out governed by
    :func:`allow_external_workspaces_from_env`), the resolved path must
    remain within ``workspaces_dir``; otherwise ``ValueError`` is raised.
    This is the path-traversal guard for workspace selection.
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
    """Resolve an operator log file path and confine it to ``workspaces_dir``.

    Relative paths are interpreted underneath ``workspaces_dir``. Returns
    the resolved :class:`~pathlib.Path`; raises ``ValueError`` if the
    resolved path escapes ``workspaces_dir`` (e.g. via ``..`` or an
    absolute path like ``/tmp/injected.log``). Unlike
    :func:`resolve_workspace_dir` there is no external opt-out: log paths
    are always confined.
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
    """Return whether external (non-confined) workspace paths are permitted.

    Reads ``UA_ALLOW_EXTERNAL_WORKSPACES``; case-insensitive truthy values
    ``{"1", "true", "yes", "on"}`` (with surrounding whitespace tolerated)
    enable the opt-out. Unset or any other value returns ``False``, keeping
    workspaces confined by default.
    """
    return (os.getenv("UA_ALLOW_EXTERNAL_WORKSPACES") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
