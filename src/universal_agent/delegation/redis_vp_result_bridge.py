"""VP SQLite → Redis Result Bridge (Phase 3a).

Monitors the local VP SQLite ``vp_missions`` table for finalized missions
that were inserted by the Redis→SQLite bridge, and publishes their results
back to the Redis results stream so the originating HQ can observe outcomes.

Only missions with ``source='redis_bridge'`` and ``result_published=0`` are
considered — gateway-local missions are not published.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from typing import Any, Optional

from universal_agent.delegation.redis_bus import RedisMissionBus
from universal_agent.delegation.schema import MissionResultEnvelope
from universal_agent.durable.state import (
    list_unpublished_bridge_missions,
    mark_mission_result_published,
)

logger = logging.getLogger(__name__)


class RedisVpResultBridge:
    """Polls VP SQLite for finalized bridge-sourced missions and publishes
    ``MissionResultEnvelope`` back to the Redis results stream."""

    def __init__(
        self,
        bus: RedisMissionBus,
        conn: sqlite3.Connection,
        *,
        poll_seconds: float = 5.0,
    ) -> None:
        self._bus = bus
        self._conn = conn
        self._poll_seconds = max(0.5, float(poll_seconds))
        self._stopped = asyncio.Event()
        self._metrics: dict[str, Any] = {
            "published_total": 0,
            "errors_total": 0,
            "last_error": None,
        }

    @property
    def metrics(self) -> dict[str, Any]:
        return dict(self._metrics)

    def stop(self) -> None:
        self._stopped.set()

    async def run(self) -> None:
        """Main result-bridge loop — runs until stopped."""
        logger.info(
            "RedisVpResultBridge starting poll=%.1fs",
            self._poll_seconds,
        )
        while not self._stopped.is_set():
            try:
                self._tick()
            except Exception as exc:
                self._metrics["errors_total"] = int(self._metrics.get("errors_total", 0)) + 1
                self._metrics["last_error"] = str(exc)
                logger.exception("RedisVpResultBridge tick failed: %s", exc)

            try:
                await asyncio.wait_for(
                    self._stopped.wait(),
                    timeout=self._poll_seconds,
                )
            except asyncio.TimeoutError:
                continue

        logger.info(
            "RedisVpResultBridge stopped. published=%d",
            self._metrics.get("published_total", 0),
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _tick(self) -> int:
        """One publish cycle.  Returns count of results published."""
        rows = list_unpublished_bridge_missions(self._conn, limit=20)
        if not rows:
            return 0

        published = 0
        for row in rows:
            mission_id = str(row["mission_id"])
            try:
                self._publish_one(row)
                mark_mission_result_published(self._conn, mission_id)
                published += 1
                self._metrics["published_total"] = int(
                    self._metrics.get("published_total", 0)
                ) + 1
            except Exception as exc:
                self._metrics["errors_total"] = int(self._metrics.get("errors_total", 0)) + 1
                self._metrics["last_error"] = str(exc)
                logger.error(
                    "RedisVpResultBridge failed publishing mission_id=%s: %s",
                    mission_id,
                    exc,
                )
        return published

    def _publish_one(self, row: sqlite3.Row) -> str:
        """Build and publish a MissionResultEnvelope for one mission."""
        mission_id = str(row["mission_id"])
        status_raw = str(row["status"] or "").strip().lower()
        payload_json = row["payload_json"] if "payload_json" in row.keys() else None

        # Extract the original Redis job_id from the payload
        redis_job_id = ""
        error_detail: Optional[str] = None
        result_data: Optional[Any] = None

        if isinstance(payload_json, str) and payload_json.strip():
            try:
                payload = json.loads(payload_json)
                if isinstance(payload, dict):
                    redis_job_id = str(payload.get("redis_job_id") or "")
            except Exception:
                pass

        if not redis_job_id:
            # Fallback: strip the bridge- prefix
            redis_job_id = mission_id.removeprefix("bridge-")

        # Map VP status to result envelope status
        if status_raw == "completed":
            result_status = "SUCCESS"
            result_data = {
                "mission_id": mission_id,
                "result_ref": str(row["result_ref"] or ""),
            }
        else:
            result_status = "FAILED"
            error_detail = f"Mission {status_raw}: {mission_id}"
            result_data = {
                "mission_id": mission_id,
                "terminal_status": status_raw,
            }

        envelope = MissionResultEnvelope(
            job_id=redis_job_id,
            status=result_status,
            result=result_data,
            error=error_detail,
        )

        message_id = self._bus.publish_result(envelope)
        logger.info(
            "RedisVpResultBridge published job_id=%s status=%s redis_msg=%s",
            redis_job_id,
            result_status,
            message_id,
        )
        return message_id
