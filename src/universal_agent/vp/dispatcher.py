from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import sqlite3

from universal_agent.durable.state import (
    append_vp_event,
    append_vp_session_event,
    get_vp_mission,
    queue_vp_mission,
    request_vp_mission_cancel,
    upsert_vp_session,
)
from universal_agent.feature_flags import (
    coder_vp_id,
    vp_handoff_root,
    vp_hard_block_ua_repo,
)
from universal_agent.guardrails.workspace_guard import (
    WorkspaceGuardError,
    enforce_external_target_path,
)
from universal_agent.vp.profiles import VpProfile, get_vp_profile


@dataclass(frozen=True)
class MissionDispatchRequest:
    vp_id: str
    mission_type: str
    objective: str
    constraints: dict[str, Any]
    budget: dict[str, Any]
    idempotency_key: str
    source_session_id: str
    source_turn_id: str
    reply_mode: str
    priority: int
    run_id: Optional[str] = None


def dispatch_mission(
    conn: sqlite3.Connection,
    request: MissionDispatchRequest,
    *,
    workspace_base: Optional[Path | str] = None,
) -> sqlite3.Row:
    profile = get_vp_profile(request.vp_id, workspace_base=workspace_base)
    if profile is None:
        raise ValueError(f"VP profile not enabled: {request.vp_id}")

    mission_id = _resolve_mission_id(request)
    payload = _build_payload(request=request, profile=profile, mission_id=mission_id)
    _validate_dispatch_constraints(profile=profile, constraints=request.constraints)

    existing = get_vp_mission(conn, mission_id)
    if existing is not None:
        return existing

    profile.workspace_root.mkdir(parents=True, exist_ok=True)
    upsert_vp_session(
        conn=conn,
        vp_id=profile.vp_id,
        runtime_id=profile.runtime_id,
        status="idle",
        session_id=f"{profile.vp_id}.external",
        workspace_dir=str(profile.workspace_root),
        metadata={"client_kind": profile.client_kind, "display_name": profile.display_name},
    )
    append_vp_session_event(
        conn=conn,
        event_id=f"vp-session-event-{uuid.uuid4().hex}",
        vp_id=profile.vp_id,
        event_type="vp.session.available",
        payload={"workspace_root": str(profile.workspace_root)},
    )

    queue_vp_mission(
        conn=conn,
        mission_id=mission_id,
        vp_id=profile.vp_id,
        mission_type=request.mission_type,
        objective=request.objective,
        payload=payload,
        budget=request.budget,
        run_id=request.run_id,
        priority=request.priority,
    )
    append_vp_event(
        conn=conn,
        event_id=f"vp-event-{uuid.uuid4().hex}",
        mission_id=mission_id,
        vp_id=profile.vp_id,
        event_type="vp.mission.dispatched",
        payload={
            "source_session_id": request.source_session_id,
            "source_turn_id": request.source_turn_id,
            "reply_mode": request.reply_mode,
            "mission_type": request.mission_type,
            "idempotency_key": request.idempotency_key,
        },
    )
    row = get_vp_mission(conn, mission_id)
    if row is None:
        raise RuntimeError("Mission queueing failed unexpectedly.")
    return row


def cancel_mission(
    conn: sqlite3.Connection,
    mission_id: str,
    *,
    reason: str = "cancel_requested",
) -> bool:
    mission = get_vp_mission(conn, mission_id)
    if mission is None:
        return False
    cancelled = request_vp_mission_cancel(conn, mission_id)
    if cancelled:
        append_vp_event(
            conn=conn,
            event_id=f"vp-event-{uuid.uuid4().hex}",
            mission_id=mission_id,
            vp_id=str(mission["vp_id"]),
            event_type="vp.mission.cancel_requested",
            payload={"reason": reason},
        )
    return cancelled


def _resolve_mission_id(request: MissionDispatchRequest) -> str:
    key = request.idempotency_key.strip()
    if not key:
        return f"vp-mission-{uuid.uuid4().hex}"
    digest = hashlib.sha1(f"{request.vp_id}:{key}".encode("utf-8")).hexdigest()  # noqa: S324
    return f"vp-mission-{digest[:24]}"


def _build_payload(
    request: MissionDispatchRequest,
    profile: VpProfile,
    mission_id: str,
) -> dict[str, Any]:
    return {
        "mission_id": mission_id,
        "vp_id": request.vp_id,
        "vp_display_name": profile.display_name,
        "mission_type": request.mission_type,
        "objective": request.objective,
        "constraints": request.constraints or {},
        "budget": request.budget or {},
        "idempotency_key": request.idempotency_key,
        "source_session_id": request.source_session_id,
        "source_turn_id": request.source_turn_id,
        "reply_mode": request.reply_mode or "async",
        "priority": int(request.priority),
    }


def _validate_dispatch_constraints(profile: VpProfile, constraints: dict[str, Any]) -> None:
    if profile.vp_id != coder_vp_id():
        return
    if not vp_hard_block_ua_repo(default=True):
        return

    candidate_paths = _extract_target_paths(constraints)
    if not candidate_paths:
        return

    repo_root = Path(__file__).resolve().parents[3]
    runtime_roots = [
        repo_root,
        (repo_root / "AGENT_RUN_WORKSPACES").resolve(),
        (repo_root / "artifacts").resolve(),
        (repo_root / "Memory_System").resolve(),
    ]
    handoff_root = Path(vp_handoff_root()).expanduser().resolve()
    runtime_roots = [path for path in runtime_roots if path.exists()]

    for raw_path in candidate_paths:
        try:
            enforce_external_target_path(
                raw_path,
                blocked_roots=runtime_roots,
                allowlisted_roots=[handoff_root],
                operation="CODIE mission target",
            )
        except WorkspaceGuardError as exc:
            raise ValueError(
                "CODIE external mission target path is blocked inside UA runtime/repo roots. "
                f"Use handoff root {handoff_root} or another external project path. ({exc})"
            ) from exc


def _extract_target_paths(constraints: dict[str, Any]) -> list[str]:
    keys = ("target_path", "path", "repo_path", "workspace_dir", "project_path")
    values: list[str] = []
    for key in keys:
        value = constraints.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    nested = constraints.get("targets")
    if isinstance(nested, list):
        for value in nested:
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
    return values
