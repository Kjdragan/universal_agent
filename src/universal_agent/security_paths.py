"""Path-containment and session-ID validation helpers for the Universal Agent.

Centralizes two security-sensitive concerns shared by many entry points:

* **Session ID validation** -- :func:`is_valid_session_id` and
  :func:`validate_session_id` enforce a strict, path-safe token format so a
  session id can never carry traversal characters (``/``, ``..``, NUL,
  whitespace, ...) into downstream filesystem or database lookups.
* **Workspace path containment** -- :func:`resolve_workspace_dir` and
  :func:`resolve_ops_log_path` resolve caller-supplied paths and reject any
  result that escapes the configured workspaces root (``UA_WORKSPACES_DIR``),
  preventing path traversal from reaching arbitrary on-disk locations.
  :func:`allow_external_workspaces_from_env` exposes the explicit, auditable
  opt-out controlled by the ``UA_ALLOW_EXTERNAL_WORKSPACES`` env var.

These helpers are intentionally dependency-free and side-effect-free so they can
be reused by the gateway, the ops service, and tests without pulling in runtime
state.
"""

from __future__ import annotations

import os
from pathlib import Path
import re

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def is_valid_session_id(value: str) -> bool:
    """Return ``True`` if *value* is a syntactically valid session id.

    A valid id starts with an ASCII alphanumeric character followed by zero or
    more alphanumerics, underscores, dots, or hyphens, and is 1-128 characters
    long after surrounding whitespace is stripped. The format deliberately
    excludes slashes, spaces, control characters, and a leading separator so
    that session ids are safe to interpolate into filesystem paths and queries.

    ``None`` and empty or whitespace-only input return ``False`` rather than
    raising.
    """
    return bool(_SESSION_ID_RE.match((value or "").strip()))


def validate_session_id(value: str) -> str:
    """Validate *value* as a session id and return the stripped form.

    Surrounding whitespace is stripped before :func:`is_valid_session_id` is
    applied. Raises ``ValueError("Invalid session id format")`` when the result
    is not acceptable. Use this at trust boundaries where an invalid id should
    abort the request rather than be silently coerced.
    """
    session_id = (value or "").strip()
    if not is_valid_session_id(session_id):
        raise ValueError("Invalid session id format")
    return session_id


def _is_within(base_dir: Path, candidate: Path) -> bool:
    """Return ``True`` if *candidate* resolves to *base_dir* itself or beneath it.

    Both paths are resolved (symlinks followed) before comparison, so traversal
    segments like ``..`` cannot escape *base_dir*. Any resolution failure is
    treated as "not within" and returns ``False``.
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
    """Resolve a caller-requested workspace directory to an absolute path.

    Relative paths are interpreted under *workspaces_dir*; absolute paths are
    taken as-is. Returns ``None`` when *requested* is falsy (no workspace
    selected). When *allow_external* is ``False`` (the default) the resolved
    path must remain within *workspaces_dir*; otherwise ``ValueError`` is
    raised, blocking path traversal. Pass ``allow_external=True`` to permit
    out-of-tree workspaces (also gated globally by
    :func:`allow_external_workspaces_from_env`).

    Returns the resolved absolute path as a ``str``.
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
    """Resolve a caller-supplied ops-log path, constraining it to *workspaces_dir*.

    Relative paths are interpreted under *workspaces_dir*; absolute paths are
    taken as-is. The resolved path must remain within *workspaces_dir*;
    otherwise ``ValueError`` is raised, blocking path traversal (for example an
    injected ``/tmp/...`` or ``../../etc/passwd``). Unlike
    :func:`resolve_workspace_dir` there is no external opt-out: ops log paths
    are always contained.

    Returns the resolved absolute path as a :class:`~pathlib.Path`.
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
    """Return whether ``UA_ALLOW_EXTERNAL_WORKSPACES`` permits out-of-tree workspaces.

    Truthy when the env var is set to one of ``1``/``true``/``yes``/``on``
    (case-insensitive, surrounding whitespace tolerated). Unset or any other
    value means external workspaces are disallowed, which is the safe default.
    """
    return (os.getenv("UA_ALLOW_EXTERNAL_WORKSPACES") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
