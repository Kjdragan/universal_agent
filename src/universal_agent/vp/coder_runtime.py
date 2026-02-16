from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import sqlite3

from universal_agent.durable.state import (
    acquire_vp_session_lease,
    append_vp_event,
    get_vp_mission,
    get_vp_session,
    upsert_vp_mission,
    upsert_vp_session,
)
from universal_agent.feature_flags import (
    coder_vp_enabled,
    coder_vp_force_fallback,
    coder_vp_id,
    coder_vp_lease_ttl_seconds,
    coder_vp_runtime_id,
    coder_vp_shadow_mode,
    coder_vp_workspace_dir,
)


_CODING_INTENT_MARKERS = (
    "code",
    "python",
    "typescript",
    "javascript",
    "refactor",
    "bug",
    "test",
    "implement",
    "fix",
    "function",
    "class",
    "api",
    "endpoint",
)


@dataclass(frozen=True)
class CoderVPRoutingDecision:
    use_coder_vp: bool
    intent_matched: bool
    reason: str
    shadow_mode: bool
    force_fallback: bool


class CoderVPRuntime:
    """Phase A CODER VP registry + mission lifecycle service."""

    def __init__(self, conn: sqlite3.Connection, workspace_base: Path | str):
        self._conn = conn
        self._workspace_base = Path(workspace_base)

    def route_decision(self, user_input: str) -> CoderVPRoutingDecision:
        intent_matched = self.is_coding_intent(user_input)
        force_fallback = coder_vp_force_fallback(default=False)
        shadow_mode = coder_vp_shadow_mode(default=False)

        if force_fallback:
            return CoderVPRoutingDecision(
                use_coder_vp=False,
                intent_matched=intent_matched,
                reason="forced_fallback",
                shadow_mode=shadow_mode,
                force_fallback=True,
            )

        if not coder_vp_enabled(default=False):
            return CoderVPRoutingDecision(
                use_coder_vp=False,
                intent_matched=intent_matched,
                reason="feature_disabled",
                shadow_mode=shadow_mode,
                force_fallback=False,
            )

        if not intent_matched:
            return CoderVPRoutingDecision(
                use_coder_vp=False,
                intent_matched=False,
                reason="intent_not_coding",
                shadow_mode=shadow_mode,
                force_fallback=False,
            )

        if shadow_mode:
            return CoderVPRoutingDecision(
                use_coder_vp=False,
                intent_matched=True,
                reason="shadow_mode",
                shadow_mode=True,
                force_fallback=False,
            )

        return CoderVPRoutingDecision(
            use_coder_vp=True,
            intent_matched=True,
            reason="eligible",
            shadow_mode=False,
            force_fallback=False,
        )

    def is_coding_intent(self, user_input: str) -> bool:
        text = (user_input or "").lower()
        if "```" in text:
            return True
        return any(marker in text for marker in _CODING_INTENT_MARKERS)

    def ensure_session(self, lease_owner: str, owner_user_id: Optional[str] = None) -> Optional[sqlite3.Row]:
        vp_identifier = coder_vp_id()
        runtime_identifier = coder_vp_runtime_id()
        workspace_dir = self._resolve_workspace_dir(vp_identifier)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_lane_soul(workspace_dir)

        metadata = {"lane": "coder_vp"}
        if owner_user_id:
            metadata["owner_user_id"] = owner_user_id

        upsert_vp_session(
            self._conn,
            vp_id=vp_identifier,
            runtime_id=runtime_identifier,
            status="idle",
            session_id=f"{vp_identifier}.session",
            workspace_dir=str(workspace_dir),
            metadata=metadata,
        )
        acquire_vp_session_lease(
            self._conn,
            vp_id=vp_identifier,
            lease_owner=lease_owner,
            lease_ttl_seconds=coder_vp_lease_ttl_seconds(default=300),
        )
        return get_vp_session(self._conn, vp_identifier)

    @staticmethod
    def _ensure_lane_soul(workspace_dir: Path) -> None:
        """Seed a CODIE SOUL.md into the VP workspace if one does not already exist."""
        soul_path = workspace_dir / "SOUL.md"
        if soul_path.exists():
            return

        template_path = Path(__file__).resolve().parents[1] / "prompt_assets" / "CODIE_SOUL.md"
        try:
            if template_path.exists() and template_path.is_file():
                content = template_path.read_text(encoding="utf-8").rstrip()
                if content:
                    soul_path.write_text(content + "\n", encoding="utf-8")
        except Exception:
            # Persona seeding should never block mission/session startup.
            return

    def bind_session_identity(self, session_id: str, status: str = "active") -> Optional[sqlite3.Row]:
        vp_identifier = coder_vp_id()
        row = get_vp_session(self._conn, vp_identifier)
        workspace_dir = self._resolve_workspace_dir(vp_identifier)
        runtime_identifier = coder_vp_runtime_id()
        if row is not None:
            workspace_dir = Path(str(row["workspace_dir"] or workspace_dir))
            runtime_identifier = str(row["runtime_id"] or runtime_identifier)

        upsert_vp_session(
            self._conn,
            vp_id=vp_identifier,
            runtime_id=runtime_identifier,
            status=status,
            session_id=session_id,
            workspace_dir=str(workspace_dir),
        )
        return get_vp_session(self._conn, vp_identifier)

    def start_mission(
        self,
        objective: str,
        run_id: Optional[str],
        trace_id: Optional[str] = None,
        budget: Optional[dict[str, Any]] = None,
    ) -> str:
        mission_id = f"vp-mission-{uuid.uuid4().hex}"
        vp_identifier = coder_vp_id()
        started_at = self._now_iso()

        upsert_vp_mission(
            self._conn,
            mission_id=mission_id,
            vp_id=vp_identifier,
            status="running",
            objective=objective,
            budget=budget,
            run_id=run_id,
            started_at=started_at,
        )
        append_vp_event(
            self._conn,
            event_id=f"vp-event-{uuid.uuid4().hex}",
            mission_id=mission_id,
            vp_id=vp_identifier,
            event_type="vp.mission.dispatched",
            payload={
                "trace_id": trace_id,
                "run_id": run_id,
                "started_at": started_at,
            },
        )
        return mission_id

    def append_progress(
        self,
        mission_id: str,
        summary: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        mission = get_vp_mission(self._conn, mission_id)
        if mission is None:
            return
        event_payload = {"summary": summary}
        if payload:
            event_payload.update(payload)
        append_vp_event(
            self._conn,
            event_id=f"vp-event-{uuid.uuid4().hex}",
            mission_id=mission_id,
            vp_id=str(mission["vp_id"]),
            event_type="vp.mission.progress",
            payload=event_payload,
        )

    def mark_mission_completed(
        self,
        mission_id: str,
        result_ref: Optional[str] = None,
        trace_id: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        mission = get_vp_mission(self._conn, mission_id)
        if mission is None:
            return

        completed_at = self._now_iso()
        upsert_vp_mission(
            self._conn,
            mission_id=mission_id,
            vp_id=str(mission["vp_id"]),
            status="completed",
            objective=str(mission["objective"]),
            budget=self._parse_budget(mission["budget_json"]),
            result_ref=result_ref,
            run_id=mission["run_id"],
            started_at=mission["started_at"],
            completed_at=completed_at,
        )
        event_payload = {
            "trace_id": trace_id,
            "result_ref": result_ref,
            "completed_at": completed_at,
        }
        if payload:
            event_payload.update(payload)
        append_vp_event(
            self._conn,
            event_id=f"vp-event-{uuid.uuid4().hex}",
            mission_id=mission_id,
            vp_id=str(mission["vp_id"]),
            event_type="vp.mission.completed",
            payload=event_payload,
        )

    def mark_mission_failed(
        self,
        mission_id: str,
        error_message: str,
        trace_id: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        mission = get_vp_mission(self._conn, mission_id)
        if mission is None:
            return

        completed_at = self._now_iso()
        upsert_vp_mission(
            self._conn,
            mission_id=mission_id,
            vp_id=str(mission["vp_id"]),
            status="failed",
            objective=str(mission["objective"]),
            budget=self._parse_budget(mission["budget_json"]),
            result_ref=mission["result_ref"],
            run_id=mission["run_id"],
            started_at=mission["started_at"],
            completed_at=completed_at,
        )
        event_payload = {
            "trace_id": trace_id,
            "error": error_message,
            "completed_at": completed_at,
        }
        if payload:
            event_payload.update(payload)
        append_vp_event(
            self._conn,
            event_id=f"vp-event-{uuid.uuid4().hex}",
            mission_id=mission_id,
            vp_id=str(mission["vp_id"]),
            event_type="vp.mission.failed",
            payload=event_payload,
        )

    def mark_mission_fallback(
        self,
        mission_id: str,
        reason: str,
        trace_id: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        mission = get_vp_mission(self._conn, mission_id)
        if mission is None:
            return
        event_payload = {"reason": reason, "trace_id": trace_id}
        if payload:
            event_payload.update(payload)
        append_vp_event(
            self._conn,
            event_id=f"vp-event-{uuid.uuid4().hex}",
            mission_id=mission_id,
            vp_id=str(mission["vp_id"]),
            event_type="vp.mission.fallback",
            payload=event_payload,
        )

    def _resolve_workspace_dir(self, vp_identifier: str) -> Path:
        configured = coder_vp_workspace_dir(default="")
        if configured:
            return Path(configured).expanduser().resolve()
        safe_name = vp_identifier.replace(".", "_")
        return (self._workspace_base / safe_name).resolve()

    @staticmethod
    def _parse_budget(raw_budget: Any) -> Optional[dict[str, Any]]:
        if not raw_budget:
            return None
        if isinstance(raw_budget, dict):
            return raw_budget
        if isinstance(raw_budget, str):
            try:
                parsed = json.loads(raw_budget)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return None
        return None

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
