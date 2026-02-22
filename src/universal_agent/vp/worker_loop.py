from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import sqlite3

from universal_agent.durable.state import (
    acquire_vp_session_lease,
    append_vp_event,
    claim_next_vp_mission,
    finalize_vp_mission,
    get_vp_mission,
    heartbeat_vp_mission_claim,
    heartbeat_vp_session_lease,
    release_vp_session_lease,
    update_vp_session_status,
    upsert_vp_session,
)
from universal_agent.feature_flags import (
    vp_lease_ttl_seconds,
    vp_max_concurrent_missions,
    vp_poll_interval_seconds,
)
from universal_agent.vp.clients.base import VpClient
from universal_agent.vp.clients.claude_code_client import ClaudeCodeClient
from universal_agent.vp.clients.claude_generalist_client import ClaudeGeneralistClient
from universal_agent.vp.profiles import get_vp_profile

logger = logging.getLogger(__name__)


class VpWorkerLoop:
    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        vp_id: str,
        worker_id: Optional[str] = None,
        workspace_base: Optional[Path | str] = None,
        poll_interval_seconds: Optional[int] = None,
        lease_ttl_seconds: Optional[int] = None,
        max_concurrent_missions: Optional[int] = None,
    ) -> None:
        profile = get_vp_profile(vp_id, workspace_base=workspace_base)
        if profile is None:
            raise ValueError(f"VP profile not configured/enabled: {vp_id}")

        self.conn = conn
        self.profile = profile
        self.vp_id = vp_id
        self.worker_id = worker_id or f"{vp_id}.worker.{uuid.uuid4().hex[:8]}"
        self.poll_interval_seconds = int(poll_interval_seconds or vp_poll_interval_seconds(default=5))
        self.lease_ttl_seconds = int(lease_ttl_seconds or vp_lease_ttl_seconds(default=120))
        self.max_concurrent_missions = int(
            max_concurrent_missions or vp_max_concurrent_missions(default=1)
        )
        self._stopped = asyncio.Event()
        self._client = self._create_client()

    def stop(self) -> None:
        self._stopped.set()

    async def run_forever(self) -> None:
        logger.info("VP worker starting: vp_id=%s worker_id=%s", self.vp_id, self.worker_id)
        self.profile.workspace_root.mkdir(parents=True, exist_ok=True)
        self._upsert_session(status="idle")
        lease_ok = acquire_vp_session_lease(
            self.conn,
            vp_id=self.vp_id,
            lease_owner=self.worker_id,
            lease_ttl_seconds=self.lease_ttl_seconds,
        )
        if not lease_ok:
            logger.warning("VP worker lease acquisition failed: vp_id=%s worker_id=%s", self.vp_id, self.worker_id)
            update_vp_session_status(
                self.conn,
                vp_id=self.vp_id,
                status="degraded",
                last_error="worker lease acquisition failed",
            )

        while not self._stopped.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("VP worker tick failed: vp_id=%s err=%s", self.vp_id, exc)
                update_vp_session_status(
                    self.conn,
                    vp_id=self.vp_id,
                    status="degraded",
                    last_error=str(exc),
                )
                await asyncio.sleep(self.poll_interval_seconds)

        release_vp_session_lease(self.conn, vp_id=self.vp_id, lease_owner=self.worker_id)
        self._upsert_session(status="idle")
        logger.info("VP worker stopped: vp_id=%s worker_id=%s", self.vp_id, self.worker_id)

    async def _tick(self) -> None:
        heartbeat_vp_session_lease(
            self.conn,
            vp_id=self.vp_id,
            lease_owner=self.worker_id,
            lease_ttl_seconds=self.lease_ttl_seconds,
        )
        claimed = claim_next_vp_mission(
            self.conn,
            vp_id=self.vp_id,
            worker_id=self.worker_id,
            lease_ttl_seconds=self.lease_ttl_seconds,
        )
        if claimed is None:
            await asyncio.sleep(self.poll_interval_seconds)
            return

        mission_id = str(claimed["mission_id"])
        started_context = _mission_source_context(claimed)
        self._upsert_session(status="active")
        append_vp_event(
            self.conn,
            event_id=f"vp-event-{uuid.uuid4().hex}",
            mission_id=mission_id,
            vp_id=self.vp_id,
            event_type="vp.mission.started",
            payload={**started_context, "worker_id": self.worker_id},
        )

        mission = get_vp_mission(self.conn, mission_id)
        if mission is None:
            return
        source_context = _mission_source_context(mission)
        if int(mission["cancel_requested"] or 0) == 1:
            finalize_vp_mission(self.conn, mission_id, "cancelled")
            append_vp_event(
                self.conn,
                event_id=f"vp-event-{uuid.uuid4().hex}",
                mission_id=mission_id,
                vp_id=self.vp_id,
                event_type="vp.mission.cancelled",
                payload={
                    **source_context,
                    "worker_id": self.worker_id,
                    "reason": "cancel_requested_before_start",
                },
            )
            self._upsert_session(status="idle")
            return

        heartbeat_vp_mission_claim(
            self.conn,
            mission_id=mission_id,
            vp_id=self.vp_id,
            worker_id=self.worker_id,
            lease_ttl_seconds=self.lease_ttl_seconds,
        )

        outcome = await self._client.run_mission(
            mission=dict(mission),
            workspace_root=self.profile.workspace_root,
        )
        if outcome.status == "cancelled":
            finalize_vp_mission(self.conn, mission_id, "cancelled", result_ref=outcome.result_ref)
            event_type = "vp.mission.cancelled"
        elif outcome.status == "failed":
            finalize_vp_mission(self.conn, mission_id, "failed", result_ref=outcome.result_ref)
            event_type = "vp.mission.failed"
        else:
            finalize_vp_mission(self.conn, mission_id, "completed", result_ref=outcome.result_ref)
            event_type = "vp.mission.completed"

        payload = dict(outcome.payload or {})
        if outcome.message:
            payload["message"] = outcome.message
        if outcome.result_ref:
            payload["result_ref"] = outcome.result_ref
        payload["worker_id"] = self.worker_id
        payload.update(
            _write_vp_finalize_artifacts(
                mission_id=mission_id,
                mission_row=mission,
                vp_id=self.vp_id,
                worker_id=self.worker_id,
                outcome=outcome,
                terminal_status=event_type.replace("vp.mission.", "", 1),
                source_context=source_context,
                workspace_root=self.profile.workspace_root,
            )
        )

        append_vp_event(
            self.conn,
            event_id=f"vp-event-{uuid.uuid4().hex}",
            mission_id=mission_id,
            vp_id=self.vp_id,
            event_type=event_type,
            payload={**source_context, **payload},
        )
        self._upsert_session(status="idle")

    def _upsert_session(self, *, status: str) -> None:
        upsert_vp_session(
            self.conn,
            vp_id=self.vp_id,
            runtime_id=self.profile.runtime_id,
            status=status,
            session_id=f"{self.vp_id}.external",
            workspace_dir=str(self.profile.workspace_root),
            lease_owner=self.worker_id,
            metadata={"client_kind": self.profile.client_kind, "display_name": self.profile.display_name},
        )

    def _create_client(self) -> VpClient:
        if self.profile.client_kind == "claude_code":
            return ClaudeCodeClient()
        if self.profile.client_kind == "claude_generalist":
            return ClaudeGeneralistClient()
        raise ValueError(f"Unsupported VP client_kind: {self.profile.client_kind}")


def _mission_source_context(mission_row: Any) -> dict[str, Any]:
    payload_json = mission_row["payload_json"] if "payload_json" in mission_row.keys() else None
    if not isinstance(payload_json, str) or not payload_json.strip():
        return {}
    try:
        payload = json.loads(payload_json)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    source_session_id = str(payload.get("source_session_id") or "").strip()
    source_turn_id = str(payload.get("source_turn_id") or "").strip()
    reply_mode = str(payload.get("reply_mode") or "").strip()

    context: dict[str, Any] = {}
    if source_session_id:
        context["source_session_id"] = source_session_id
    if source_turn_id:
        context["source_turn_id"] = source_turn_id
    if reply_mode:
        context["reply_mode"] = reply_mode
    return context


def _env_true(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _mission_workspace_dir(*, mission_id: str, result_ref: str, workspace_root: Path) -> Path:
    root_resolved = workspace_root.resolve()
    if result_ref.startswith("workspace://"):
        candidate = Path(result_ref.replace("workspace://", "", 1)).expanduser()
        try:
            resolved = candidate.resolve()
            if resolved == root_resolved or root_resolved in resolved.parents:
                return resolved
        except Exception:
            pass
    return (workspace_root / mission_id).resolve()


def _write_json_file(path: Path, payload: dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return True
    except Exception as exc:
        logger.warning("VP worker failed writing file path=%s err=%s", path, exc)
        return False


def _collect_user_artifact_relpaths(
    mission_workspace: Path,
    *,
    ignore_names: set[str],
    max_items: int = 8,
) -> list[str]:
    if not mission_workspace.exists() or max_items <= 0:
        return []
    relpaths: list[str] = []
    for file_path in sorted(path for path in mission_workspace.rglob("*") if path.is_file()):
        relpath = str(file_path.relative_to(mission_workspace))
        normalized_name = file_path.name.strip().lower()
        if normalized_name in ignore_names:
            continue
        if relpath.startswith("."):
            continue
        relpaths.append(relpath)
        if len(relpaths) >= max_items:
            break
    return relpaths


def _write_vp_finalize_artifacts(
    *,
    mission_id: str,
    mission_row: Any,
    vp_id: str,
    worker_id: str,
    outcome: Any,
    terminal_status: str,
    source_context: dict[str, Any],
    workspace_root: Path,
) -> dict[str, Any]:
    result_ref = str(getattr(outcome, "result_ref", "") or "").strip()
    mission_workspace = _mission_workspace_dir(
        mission_id=mission_id,
        result_ref=result_ref,
        workspace_root=workspace_root,
    )
    completed_epoch = time.time()
    created_at = str(mission_row["created_at"] or "")
    started_at = str(mission_row["started_at"] or "")
    updated_at = str(mission_row["updated_at"] or "")
    objective = str(mission_row["objective"] or "")
    mission_type = str(mission_row["mission_type"] or "")
    mission_payload_raw = mission_row["payload_json"] if "payload_json" in mission_row.keys() else None
    mission_payload = {}
    if isinstance(mission_payload_raw, str) and mission_payload_raw.strip():
        try:
            parsed = json.loads(mission_payload_raw)
            if isinstance(parsed, dict):
                mission_payload = parsed
        except Exception:
            mission_payload = {}

    artifact_refs: dict[str, Any] = {}
    receipt_filename = "mission_receipt.json"
    marker_name = (os.getenv("UA_VP_SYNC_READY_MARKER_FILENAME") or "").strip() or "sync_ready.json"

    if _env_true("UA_VP_MISSION_RECEIPT_ENABLED", True):
        receipt_payload = {
            "version": 1,
            "mission_id": mission_id,
            "vp_id": vp_id,
            "status": terminal_status,
            "worker_id": worker_id,
            "objective": objective,
            "mission_type": mission_type or None,
            "result_ref": result_ref or None,
            "source_session_id": source_context.get("source_session_id"),
            "source_turn_id": source_context.get("source_turn_id"),
            "reply_mode": source_context.get("reply_mode"),
            "created_at": created_at or None,
            "started_at": started_at or None,
            "updated_at": updated_at or None,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "completed_at_epoch": completed_epoch,
            "mission_payload": mission_payload,
            "outcome": {
                "status": str(getattr(outcome, "status", "") or "").strip() or terminal_status,
                "message": str(getattr(outcome, "message", "") or "").strip() or None,
                "payload": dict(getattr(outcome, "payload", {}) or {}),
            },
        }
        receipt_path = mission_workspace / receipt_filename
        if _write_json_file(receipt_path, receipt_payload):
            artifact_refs["mission_receipt_relpath"] = receipt_filename
            artifact_refs["mission_receipt_path"] = str(receipt_path)

    if _env_true("UA_VP_SYNC_READY_MARKER_ENABLED", True):
        marker_payload = {
            "version": 1,
            "mission_id": mission_id,
            "vp_id": vp_id,
            "state": terminal_status,
            "ready": True,
            "worker_id": worker_id,
            "result_ref": result_ref or None,
            "source_session_id": source_context.get("source_session_id"),
            "source_turn_id": source_context.get("source_turn_id"),
            "reply_mode": source_context.get("reply_mode"),
            "updated_at_epoch": completed_epoch,
            "completed_at_epoch": completed_epoch,
        }
        marker_path = mission_workspace / marker_name
        if _write_json_file(marker_path, marker_payload):
            artifact_refs["sync_ready_marker_relpath"] = marker_name
            artifact_refs["sync_ready_marker_path"] = str(marker_path)

    user_artifact_relpaths = _collect_user_artifact_relpaths(
        mission_workspace,
        ignore_names={receipt_filename.lower(), marker_name.lower()},
    )
    if user_artifact_relpaths:
        artifact_refs["artifact_relpath"] = user_artifact_relpaths[0]
        artifact_refs["artifact_relpaths"] = user_artifact_relpaths

    return artifact_refs
