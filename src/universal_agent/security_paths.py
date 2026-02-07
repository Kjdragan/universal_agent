from __future__ import annotations

import os
import re
from pathlib import Path


_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def is_valid_session_id(value: str) -> bool:
    return bool(_SESSION_ID_RE.match((value or "").strip()))


def validate_session_id(value: str) -> str:
    session_id = (value or "").strip()
    if not is_valid_session_id(session_id):
        raise ValueError("Invalid session id format")
    return session_id


def _is_within(base_dir: Path, candidate: Path) -> bool:
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
    candidate = Path(candidate_path).expanduser()
    if not candidate.is_absolute():
        candidate = (workspaces_dir / candidate).resolve()
    else:
        candidate = candidate.resolve()

    if not _is_within(workspaces_dir, candidate):
        raise ValueError("Log path must remain under UA_WORKSPACES_DIR")
    return candidate


def allow_external_workspaces_from_env() -> bool:
    return (os.getenv("UA_ALLOW_EXTERNAL_WORKSPACES") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
