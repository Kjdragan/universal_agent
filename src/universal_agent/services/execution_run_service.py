"""Execution Run Service — canonical allocation layer for task-scoped workspaces.

Every accepted Task Hub execution (chat, email, or dispatcher sweep) must
allocate a dedicated run workspace **before** finalizing the claim.  This
module provides:

  - ``ExecutionRunContext``: lightweight dataclass describing one execution run
  - ``allocate_execution_run()``: creates workspace + durable catalog entry
  - ``resolve_active_execution_workspace()``: canonical workspace resolver
  - ``resolve_active_run_id()``: canonical run-id resolver

Design contract:

  - Sessions are transport containers; they are NOT artifact roots.
  - Tasks are durable business identity.
  - Runs are artifact isolation and execution lineage.
  - Attempts are retries within a run.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Helper ──────────────────────────────────────────────────────────────────

def _project_root() -> Path:
    """Resolve the repository root (where AGENT_RUN_WORKSPACES/ lives)."""
    env = os.getenv("UA_REPO_ROOT", "").strip()
    if env:
        return Path(env).resolve()
    # Walk up from this file: services/ -> universal_agent/ -> src/ -> repo
    return Path(__file__).resolve().parents[3]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Data Model ──────────────────────────────────────────────────────────────

@dataclass
class ExecutionRunContext:
    """Describes one allocated execution run.

    Returned by ``allocate_execution_run`` and consumed by Task Hub
    claim call-sites so they can pass run-scoped lineage instead of
    session-scoped lineage.
    """

    run_id: str
    workspace_dir: str
    attempt_id: str
    attempt_number: int = 1
    provider_session_id: str = ""
    task_id: str = ""
    origin: str = ""  # e.g. chat_panel, email, todo_dispatch
    created_at: str = field(default_factory=_now_iso)


# ── Core Allocation ─────────────────────────────────────────────────────────

def allocate_execution_run(
    *,
    task_id: str = "",
    origin: str = "",
    provider_session_id: str = "",
    run_kind: str = "todo_execution",
    trigger_source: str = "",
    entrypoint: str = "task_hub",
    extra_run_spec: Optional[dict[str, Any]] = None,
) -> ExecutionRunContext:
    """Allocate a fresh, isolated execution run workspace.

    Steps:
      1. Generate unique ``run_id``.
      2. Create workspace directory under ``AGENT_RUN_WORKSPACES/``.
      3. Scaffold the workspace (``run_manifest.json``, ``attempts/``, etc.).
      4. Register the run in the durable ``runs`` table.
      5. Create the initial attempt record.

    Returns an ``ExecutionRunContext`` that callers pass into Task Hub
    claim functions.
    """
    from universal_agent.durable.db import connect_runtime_db, get_runtime_db_path
    from universal_agent.durable.state import create_run_attempt, upsert_run
    from universal_agent.run_workspace import ensure_run_workspace_scaffold

    # ① Generate identifiers
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    attempt_id = f"{run_id}:attempt:1"

    # ② Compute workspace path
    workspaces_root = _project_root() / "AGENT_RUN_WORKSPACES"
    workspaces_root.mkdir(parents=True, exist_ok=True)
    workspace_dir = str((workspaces_root / run_id).resolve())

    # ③ Scaffold the workspace on disk
    ensure_run_workspace_scaffold(
        workspace_dir=workspace_dir,
        run_id=run_id,
        attempt_id=attempt_id,
        attempt_number=1,
        status="running",
        run_kind=run_kind,
        trigger_source=trigger_source or origin,
    )

    # ④ Register in the durable runs catalog
    run_spec: dict[str, Any] = {
        "workspace_dir": workspace_dir,
        "task_id": task_id,
        "origin": origin,
    }
    if extra_run_spec:
        run_spec.update(extra_run_spec)

    db_path = get_runtime_db_path()
    conn = connect_runtime_db(db_path)
    try:
        upsert_run(
            conn,
            run_id=run_id,
            entrypoint=entrypoint,
            run_spec=run_spec,
            status="running",
            workspace_dir=workspace_dir,
            run_kind=run_kind,
            trigger_source=trigger_source or origin,
            external_origin=origin,
            external_origin_id=task_id or None,
        )
        create_run_attempt(
            conn,
            run_id=run_id,
            attempt_id=attempt_id,
            status="running",
            provider_session_id=provider_session_id or None,
        )
    finally:
        conn.close()

    ctx = ExecutionRunContext(
        run_id=run_id,
        workspace_dir=workspace_dir,
        attempt_id=attempt_id,
        attempt_number=1,
        provider_session_id=provider_session_id,
        task_id=task_id,
        origin=origin,
    )

    logger.info(
        "🏗️ Allocated execution run: run_id=%s task_id=%s origin=%s workspace=%s",
        run_id,
        task_id,
        origin,
        workspace_dir,
    )
    return ctx


# ── Canonical Resolvers ─────────────────────────────────────────────────────

def resolve_active_execution_workspace(
    *,
    session: Any = None,
    request_metadata: Optional[dict[str, Any]] = None,
    assignment: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Return the best available workspace dir for the current execution.

    Priority order (most specific → least specific):
      1. Assignment's ``workspace_dir`` (set at claim time)
      2. Request metadata ``workspace_dir`` (set by intake path)
      3. Session's ``workspace_dir`` (transport-level fallback)

    All downstream file-browsing and artifact-writing code should call
    this instead of reading ``session.workspace_dir`` directly.
    """
    # 1. Assignment workspace (run-scoped)
    if assignment:
        ws = str(assignment.get("workspace_dir") or "").strip()
        if ws:
            return ws

    # 2. Request metadata (request-scoped override)
    if request_metadata:
        ws = str(request_metadata.get("workspace_dir") or "").strip()
        if ws:
            return ws

    # 3. Session workspace (transport fallback)
    if session is not None:
        ws = str(getattr(session, "workspace_dir", "") or "").strip()
        if ws:
            return ws

    return None


def resolve_active_run_id(
    *,
    session: Any = None,
    request_metadata: Optional[dict[str, Any]] = None,
    assignment: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Return the best available run ID for the current execution.

    Priority order:
      1. Assignment's ``workflow_run_id``
      2. Request metadata ``workflow_run_id``
      3. Session metadata ``run_id`` or ``active_run_id``
    """
    # 1. Assignment (run-scoped)
    if assignment:
        rid = str(assignment.get("workflow_run_id") or "").strip()
        if rid:
            return rid

    # 2. Request metadata
    if request_metadata:
        rid = str(request_metadata.get("workflow_run_id") or "").strip()
        if rid:
            return rid

    # 3. Session metadata
    if session is not None:
        meta = getattr(session, "metadata", None)
        if isinstance(meta, dict):
            for key in ("active_run_id", "run_id"):
                rid = str(meta.get(key) or "").strip()
                if rid:
                    return rid

    return None


def resolve_active_codebase_root(
    *,
    session: Any = None,
    request_metadata: Optional[dict[str, Any]] = None,
    assignment: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Return the best available codebase root for the current execution."""
    if assignment:
        root = str(assignment.get("codebase_root") or "").strip()
        if root:
            return root

    if request_metadata:
        root = str(request_metadata.get("codebase_root") or "").strip()
        if root:
            return root

    if session is not None and isinstance(getattr(session, "metadata", None), dict):
        root = str(session.metadata.get("codebase_root") or "").strip()
        if root:
            return root

    return None
