from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Optional

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
        self._upsert_session(status="active")
        append_vp_event(
            self.conn,
            event_id=f"vp-event-{uuid.uuid4().hex}",
            mission_id=mission_id,
            vp_id=self.vp_id,
            event_type="vp.mission.started",
            payload={"worker_id": self.worker_id},
        )

        mission = get_vp_mission(self.conn, mission_id)
        if mission is None:
            return
        if int(mission["cancel_requested"] or 0) == 1:
            finalize_vp_mission(self.conn, mission_id, "cancelled")
            append_vp_event(
                self.conn,
                event_id=f"vp-event-{uuid.uuid4().hex}",
                mission_id=mission_id,
                vp_id=self.vp_id,
                event_type="vp.mission.cancelled",
                payload={"worker_id": self.worker_id, "reason": "cancel_requested_before_start"},
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

        append_vp_event(
            self.conn,
            event_id=f"vp-event-{uuid.uuid4().hex}",
            mission_id=mission_id,
            vp_id=self.vp_id,
            event_type=event_type,
            payload=payload,
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
