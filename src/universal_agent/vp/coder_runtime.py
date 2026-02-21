from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import sqlite3

from universal_agent.durable.state import (
    acquire_vp_session_lease,
    append_vp_event,
    append_vp_session_event,
    get_vp_mission,
    get_vp_session,
    heartbeat_vp_session_lease,
    release_vp_session_lease,
    update_vp_session_status,
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

logger = logging.getLogger(__name__)


_CODING_INTENT_MARKERS = (
    "code",
    "python",
    "bash",
    "shell",
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

_CODIE_SCOPE_MARKERS = (
    "script",
    "project",
    "module",
    "service",
    "workflow",
    "autonomous",
    "end-to-end",
    "multi-file",
    "across files",
    "test suite",
    "integration test",
    "full implementation",
    "production-ready",
)

_UTILITY_REQUEST_MARKERS = (
    "bash command",
    "shell command",
    "cli command",
    "one-liner",
    "one liner",
    "quick command",
    "utility function",
    "helper function",
    "small helper",
    "small snippet",
    "quick snippet",
)

_INTERNAL_SYSTEM_MARKERS = (
    "simone",
    "universal agent",
    "our system",
    "this system",
    "mission control",
    "system configuration",
    "ops config",
    "session policy",
    "heartbeat",
    "calendar",
    "continuity",
    "webhook",
    "telegram",
    "gateway",
    "src/universal_agent",
    "web-ui/",
    "agent_run_workspaces",
    "/home/kjdragan/lrepos/universal_agent",
)

_RECOVERY_STATUSES = {"degraded", "recovering"}


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
        meets_threshold = self.meets_delegation_threshold(user_input)
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

        # Phase A has moved to sustained default-on posture: treat CODIE as enabled
        # unless explicitly disabled via UA_DISABLE_CODER_VP.
        if not coder_vp_enabled(default=True):
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

        # CODIE is intended for significant greenfield/external coding work.
        # Internal UA configuration and repository maintenance stays on Simone
        # (or explicit dedicated subagents).
        if self.is_internal_system_request(user_input):
            return CoderVPRoutingDecision(
                use_coder_vp=False,
                intent_matched=True,
                reason="internal_system_request",
                shadow_mode=shadow_mode,
                force_fallback=False,
            )

        if not meets_threshold:
            return CoderVPRoutingDecision(
                use_coder_vp=False,
                intent_matched=True,
                reason="below_codie_threshold",
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

    def meets_delegation_threshold(self, user_input: str) -> bool:
        text = (user_input or "").lower()
        if not self.is_coding_intent(text):
            return False
        if any(marker in text for marker in _CODIE_SCOPE_MARKERS):
            return True
        return not any(marker in text for marker in _UTILITY_REQUEST_MARKERS)

    def is_internal_system_request(self, user_input: str) -> bool:
        text = (user_input or "").lower()
        return any(marker in text for marker in _INTERNAL_SYSTEM_MARKERS)

    def ensure_session(self, lease_owner: str, owner_user_id: Optional[str] = None) -> Optional[sqlite3.Row]:
        vp_identifier = coder_vp_id()
        runtime_identifier = coder_vp_runtime_id()
        workspace_dir = self._resolve_workspace_dir(vp_identifier)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_lane_soul(workspace_dir)
        previous_row = get_vp_session(self._conn, vp_identifier)
        previous_status = str(previous_row["status"] or "") if previous_row is not None else None

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
        lease_acquired = acquire_vp_session_lease(
            self._conn,
            vp_id=vp_identifier,
            lease_owner=lease_owner,
            lease_ttl_seconds=coder_vp_lease_ttl_seconds(default=300),
        )
        if lease_acquired:
            event_type = "vp.session.created" if previous_row is None else "vp.session.resumed"
            payload = {
                "lease_owner": lease_owner,
                "workspace_dir": str(workspace_dir),
                "session_id": f"{vp_identifier}.session",
            }
            if previous_status in _RECOVERY_STATUSES:
                payload["recovered_from_status"] = previous_status
            self._append_session_event(vp_id=vp_identifier, event_type=event_type, payload=payload)
        else:
            update_vp_session_status(
                self._conn,
                vp_id=vp_identifier,
                status="degraded",
                last_error="vp lease acquisition failed",
            )
            self._append_session_event(
                vp_id=vp_identifier,
                event_type="vp.session.degraded",
                payload={
                    "lease_owner": lease_owner,
                    "reason": "lease_acquisition_failed",
                },
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
        if row is None:
            self._append_session_event(
                vp_id=vp_identifier,
                event_type="vp.session.created",
                payload={"session_id": session_id, "status": status},
            )
        elif str(row["session_id"] or "") != session_id or str(row["status"] or "") != status:
            self._append_session_event(
                vp_id=vp_identifier,
                event_type="vp.session.resumed",
                payload={
                    "session_id": session_id,
                    "previous_session_id": row["session_id"],
                    "status": status,
                    "previous_status": row["status"],
                },
            )
        return get_vp_session(self._conn, vp_identifier)

    def heartbeat_session_lease(self, lease_owner: str) -> bool:
        vp_identifier = coder_vp_id()
        heartbeat_ok = heartbeat_vp_session_lease(
            self._conn,
            vp_id=vp_identifier,
            lease_owner=lease_owner,
            lease_ttl_seconds=coder_vp_lease_ttl_seconds(default=300),
        )
        if not heartbeat_ok:
            update_vp_session_status(
                self._conn,
                vp_id=vp_identifier,
                status="degraded",
                last_error="vp lease heartbeat failed",
            )
            self._append_session_event(
                vp_id=vp_identifier,
                event_type="vp.session.degraded",
                payload={"lease_owner": lease_owner, "reason": "lease_heartbeat_failed"},
            )
        return heartbeat_ok

    def release_session_lease(self, lease_owner: str) -> None:
        vp_identifier = coder_vp_id()
        release_vp_session_lease(
            self._conn,
            vp_id=vp_identifier,
            lease_owner=lease_owner,
        )
        update_vp_session_status(
            self._conn,
            vp_id=vp_identifier,
            status="idle",
        )
        self._append_session_event(
            vp_id=vp_identifier,
            event_type="vp.session.resumed",
            payload={"lease_owner": lease_owner, "status": "idle", "reason": "lease_released"},
        )

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
        normalized_result_ref = self._normalize_result_ref(mission_id=mission_id, result_ref=result_ref)
        upsert_vp_mission(
            self._conn,
            mission_id=mission_id,
            vp_id=str(mission["vp_id"]),
            status="completed",
            objective=str(mission["objective"]),
            budget=self._parse_budget(mission["budget_json"]),
            result_ref=normalized_result_ref,
            run_id=mission["run_id"],
            started_at=mission["started_at"],
            completed_at=completed_at,
        )
        event_payload = {
            "trace_id": trace_id,
            "result_ref": normalized_result_ref,
            "completed_at": completed_at,
        }
        if payload:
            event_payload.update(payload)
        event_payload.update(
            self._write_finalize_artifacts(
                mission_id=mission_id,
                mission_row=mission,
                terminal_status="completed",
                result_ref=normalized_result_ref,
                event_payload=event_payload,
            )
        )
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
        normalized_result_ref = self._normalize_result_ref(
            mission_id=mission_id,
            result_ref=str(mission["result_ref"] or ""),
        )
        upsert_vp_mission(
            self._conn,
            mission_id=mission_id,
            vp_id=str(mission["vp_id"]),
            status="failed",
            objective=str(mission["objective"]),
            budget=self._parse_budget(mission["budget_json"]),
            result_ref=normalized_result_ref,
            run_id=mission["run_id"],
            started_at=mission["started_at"],
            completed_at=completed_at,
        )
        event_payload = {
            "trace_id": trace_id,
            "error": error_message,
            "completed_at": completed_at,
            "result_ref": normalized_result_ref,
        }
        if payload:
            event_payload.update(payload)
        event_payload.update(
            self._write_finalize_artifacts(
                mission_id=mission_id,
                mission_row=mission,
                terminal_status="failed",
                result_ref=normalized_result_ref,
                event_payload=event_payload,
            )
        )
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

    def _mission_workspace_dir(self, *, mission_id: str, result_ref: str) -> Path:
        if result_ref.startswith("workspace://"):
            base = Path(result_ref.replace("workspace://", "", 1)).expanduser()
            try:
                resolved = base.resolve()
                if resolved.name == mission_id or mission_id in resolved.parts:
                    return resolved
                return (resolved / mission_id).resolve()
            except Exception:
                pass
        vp_workspace = self._resolve_workspace_dir(coder_vp_id())
        return (vp_workspace / mission_id).resolve()

    def _normalize_result_ref(self, *, mission_id: str, result_ref: Optional[str]) -> str:
        mission_workspace = self._mission_workspace_dir(
            mission_id=mission_id,
            result_ref=str(result_ref or ""),
        )
        return f"workspace://{mission_workspace}"

    def _write_finalize_artifacts(
        self,
        *,
        mission_id: str,
        mission_row: sqlite3.Row,
        terminal_status: str,
        result_ref: Optional[str],
        event_payload: dict[str, Any],
    ) -> dict[str, Any]:
        mission_workspace = self._mission_workspace_dir(
            mission_id=mission_id,
            result_ref=str(result_ref or ""),
        )
        completed_epoch = time.time()
        mission_payload = self._parse_json_payload(mission_row["payload_json"])
        references: dict[str, Any] = {}

        if _env_true("UA_VP_MISSION_RECEIPT_ENABLED", True):
            receipt_payload: dict[str, Any] = {
                "version": 1,
                "mission_id": mission_id,
                "vp_id": str(mission_row["vp_id"] or ""),
                "status": terminal_status,
                "objective": str(mission_row["objective"] or ""),
                "mission_type": str(mission_row["mission_type"] or "") or None,
                "result_ref": str(result_ref or mission_row["result_ref"] or "") or None,
                "run_id": str(mission_row["run_id"] or "") or None,
                "created_at": str(mission_row["created_at"] or "") or None,
                "started_at": str(mission_row["started_at"] or "") or None,
                "updated_at": str(mission_row["updated_at"] or "") or None,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "completed_at_epoch": completed_epoch,
                "mission_payload": mission_payload,
                "outcome": {
                    "status": terminal_status,
                    "event_payload": dict(event_payload),
                },
            }
            receipt_path = mission_workspace / "mission_receipt.json"
            if self._write_json_file(receipt_path, receipt_payload):
                references["mission_receipt_relpath"] = "mission_receipt.json"
                references["mission_receipt_path"] = str(receipt_path)

        if _env_true("UA_VP_SYNC_READY_MARKER_ENABLED", True):
            marker_name = (os.getenv("UA_VP_SYNC_READY_MARKER_FILENAME") or "").strip() or "sync_ready.json"
            marker_payload: dict[str, Any] = {
                "version": 1,
                "mission_id": mission_id,
                "vp_id": str(mission_row["vp_id"] or ""),
                "state": terminal_status,
                "ready": True,
                "updated_at_epoch": completed_epoch,
                "completed_at_epoch": completed_epoch,
                "result_ref": str(result_ref or mission_row["result_ref"] or "") or None,
            }
            marker_path = mission_workspace / marker_name
            if self._write_json_file(marker_path, marker_payload):
                references["sync_ready_marker_relpath"] = marker_name
                references["sync_ready_marker_path"] = str(marker_path)

        return references

    @staticmethod
    def _write_json_file(path: Path, payload: dict[str, Any]) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return True
        except Exception as exc:
            logger.warning("Coder VP failed writing file path=%s err=%s", path, exc)
            return False

    @staticmethod
    def _parse_json_payload(raw_payload: Any) -> dict[str, Any]:
        if isinstance(raw_payload, dict):
            return raw_payload
        if isinstance(raw_payload, str):
            text = raw_payload.strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {}
        return {}

    def _append_session_event(
        self,
        vp_id: str,
        event_type: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        append_vp_session_event(
            self._conn,
            event_id=f"vp-session-event-{uuid.uuid4().hex}",
            vp_id=vp_id,
            event_type=event_type,
            payload=payload,
        )

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


def _env_true(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}
