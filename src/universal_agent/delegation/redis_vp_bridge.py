"""Redis → VP SQLite Bridge Adapter (Phase 3a).

Consumes MissionEnvelope messages from the Redis delegation bus and inserts
them into the local VP SQLite ``vp_missions`` table.  The existing
``VpWorkerLoop`` picks them up and executes them — no new execution engine
is required.

Architecture Decision D-006: Redis Streams for cross-machine transport,
local VP SQLite for execution.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
import re
import sqlite3
from typing import Any
import uuid

from universal_agent.delegation.redis_bus import (
    ConsumedMission,
    RedisMissionBus,
)
from universal_agent.delegation.schema import MissionEnvelope, MissionPayload
from universal_agent.delegation.system_handlers import (
    dispatch_system_mission,
    is_system_mission,
)
from universal_agent.durable.state import (
    get_vp_mission,
    queue_vp_mission,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mission-kind → VP ID routing
# ---------------------------------------------------------------------------

MISSION_KIND_TO_VP: dict[str, str] = {
    "coding_task": "vp.coder.primary",
    "general_task": "vp.general.primary",
    "research_task": "vp.general.primary",
    # tutorial_bootstrap_repo is handled by the existing tutorial worker
}

_SKIP_KINDS = frozenset({"tutorial_bootstrap_repo"})


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class BridgeConfig:
    """Configuration for the Redis→VP SQLite bridge."""

    poll_seconds: float = 5.0
    consumer_name: str = ""
    vp_id_map: dict[str, str] = field(default_factory=lambda: dict(MISSION_KIND_TO_VP))
    default_vp_id: str = "vp.general.primary"

    def resolve_consumer_name(self, factory_id: str = "") -> str:
        if self.consumer_name:
            return self.consumer_name
        base = factory_id or f"bridge-{uuid.uuid4().hex[:8]}"
        return re.sub(r"[^A-Za-z0-9:_-]+", "-", f"bridge_{base}")


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class RedisVpBridge:
    """Thin bridge: consumes Redis missions → inserts into VP SQLite."""

    def __init__(
        self,
        bus: RedisMissionBus,
        conn: sqlite3.Connection,
        config: BridgeConfig,
    ) -> None:
        self._bus = bus
        self._conn = conn
        self._config = config
        self._consumer_name = config.resolve_consumer_name()
        self._stopped = asyncio.Event()
        self._restart_requested = False
        self._paused = False
        self._metrics: dict[str, Any] = {
            "consumed_total": 0,
            "inserted_total": 0,
            "skipped_total": 0,
            "paused_skipped_total": 0,
            "system_handled_total": 0,
            "errors_total": 0,
            "last_error": None,
        }

    @property
    def metrics(self) -> dict[str, Any]:
        return dict(self._metrics)

    @property
    def restart_requested(self) -> bool:
        return self._restart_requested

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        """Pause mission consumption.  System missions still processed."""
        self._paused = True
        logger.info("RedisVpBridge paused")

    def resume(self) -> None:
        """Resume mission consumption."""
        self._paused = False
        logger.info("RedisVpBridge resumed")

    def stop(self) -> None:
        self._stopped.set()

    async def run(self, *, once: bool = False) -> int:
        """Main bridge loop.  Returns number of missions inserted."""
        logger.info(
            "RedisVpBridge starting consumer=%s poll=%.1fs",
            self._consumer_name,
            self._config.poll_seconds,
        )
        inserted = 0
        while not self._stopped.is_set():
            try:
                count = self._tick()
                inserted += count
            except Exception as exc:
                self._metrics["errors_total"] = int(self._metrics.get("errors_total", 0)) + 1
                self._metrics["last_error"] = str(exc)
                logger.exception("RedisVpBridge tick failed: %s", exc)

            if once:
                break

            try:
                await asyncio.wait_for(
                    self._stopped.wait(),
                    timeout=self._config.poll_seconds,
                )
            except asyncio.TimeoutError:
                continue

        logger.info("RedisVpBridge stopped. inserted=%d", inserted)
        return inserted

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _tick(self) -> int:
        """One consume-insert cycle.  Returns count of missions inserted."""
        consumed_list = self._bus.consume(
            consumer_name=self._consumer_name,
            count=5,
            block_ms=int(self._config.poll_seconds * 1000),
            stream_id=">",
        )
        if not consumed_list:
            return 0

        inserted = 0
        for consumed in consumed_list:
            self._metrics["consumed_total"] = int(self._metrics.get("consumed_total", 0)) + 1
            try:
                ok = self._process_one(consumed)
                if ok:
                    inserted += 1
                    self._metrics["inserted_total"] = int(self._metrics.get("inserted_total", 0)) + 1
            except Exception as exc:
                self._metrics["errors_total"] = int(self._metrics.get("errors_total", 0)) + 1
                self._metrics["last_error"] = str(exc)
                logger.error(
                    "RedisVpBridge failed processing job_id=%s: %s",
                    consumed.envelope.job_id,
                    exc,
                )
                self._handle_failure(consumed, str(exc))
        return inserted

    def _process_one(self, consumed: ConsumedMission) -> bool:
        """Process a single consumed mission.  Returns True if inserted."""
        envelope = consumed.envelope
        context = envelope.payload.context if isinstance(envelope.payload.context, dict) else {}
        mission_kind = str(context.get("mission_kind") or "").strip().lower()

        # Skip kinds handled by other consumers (e.g. tutorial worker)
        if mission_kind in _SKIP_KINDS:
            logger.debug(
                "RedisVpBridge skipping mission_kind=%s job_id=%s",
                mission_kind,
                envelope.job_id,
            )
            self._bus.ack(consumed.message_id)
            self._metrics["skipped_total"] = int(self._metrics.get("skipped_total", 0)) + 1
            return False

        # System missions are handled inline (not inserted into VP SQLite)
        if is_system_mission(mission_kind):
            result = dispatch_system_mission(mission_kind, context)
            self._bus.ack(consumed.message_id)
            self._metrics["system_handled_total"] = int(self._metrics.get("system_handled_total", 0)) + 1
            logger.info(
                "RedisVpBridge system mission handled kind=%s status=%s job_id=%s",
                mission_kind, result.status, envelope.job_id,
            )
            if result.restart_requested:
                logger.info("RedisVpBridge restart requested by system mission")
                self._restart_requested = True
                self.stop()
            if result.pause_requested:
                self.pause()
            if result.resume_requested:
                self.resume()
            return False

        # When paused, ack but do not insert work missions
        if self._paused:
            self._bus.ack(consumed.message_id)
            self._metrics["paused_skipped_total"] = int(self._metrics.get("paused_skipped_total", 0)) + 1
            logger.debug(
                "RedisVpBridge paused — skipping mission job_id=%s",
                envelope.job_id,
            )
            return False

        # Route to VP ID
        vp_id = self._config.vp_id_map.get(
            mission_kind,
            self._config.default_vp_id,
        )

        # Build mission parameters
        mission_id = f"bridge-{envelope.job_id}"
        mission_type = mission_kind or "delegated_task"
        objective = envelope.payload.task
        priority = envelope.priority or 100

        payload = {
            "task": envelope.payload.task,
            "context": context,
            "redis_job_id": envelope.job_id,
            "redis_idempotency_key": envelope.idempotency_key,
            "redis_message_id": consumed.message_id,
            "source_session_id": str(context.get("source_session_id") or ""),
            "source_turn_id": str(context.get("source_turn_id") or ""),
            "reply_mode": str(context.get("reply_mode") or ""),
        }

        budget = {
            "timeout_seconds": envelope.timeout_seconds,
            "max_retries": envelope.max_retries,
        }

        # Idempotency: skip if mission already exists
        existing = get_vp_mission(self._conn, mission_id)
        if existing is not None:
            logger.info(
                "RedisVpBridge mission already exists mission_id=%s, acking",
                mission_id,
            )
            self._bus.ack(consumed.message_id)
            return False

        # Insert into VP SQLite
        queue_vp_mission(
            self._conn,
            mission_id=mission_id,
            vp_id=vp_id,
            mission_type=mission_type,
            objective=objective,
            payload=payload,
            budget=budget,
            priority=priority,
            source="redis_bridge",
        )

        logger.info(
            "RedisVpBridge inserted mission_id=%s vp_id=%s kind=%s job_id=%s",
            mission_id,
            vp_id,
            mission_kind or "(default)",
            envelope.job_id,
        )

        # Ack the Redis message
        self._bus.ack(consumed.message_id)
        return True

    def _handle_failure(self, consumed: ConsumedMission, error: str) -> None:
        """Handle insertion failure: retry or DLQ."""
        context = consumed.envelope.payload.context if isinstance(consumed.envelope.payload.context, dict) else {}
        retry_count = int(context.get("_retry_count") or 0) + 1
        try:
            sent_to_dlq = self._bus.fail_and_maybe_dlq(
                consumed=consumed,
                failure_error=error,
                retry_count=retry_count,
            )
            if sent_to_dlq:
                logger.warning(
                    "RedisVpBridge mission sent to DLQ job_id=%s retries=%d",
                    consumed.envelope.job_id,
                    retry_count,
                )
            else:
                # Republish with incremented retry count
                ctx = dict(context)
                ctx["_retry_count"] = retry_count
                retry_envelope = MissionEnvelope(
                    job_id=consumed.envelope.job_id,
                    idempotency_key=consumed.envelope.idempotency_key,
                    priority=consumed.envelope.priority,
                    timeout_seconds=consumed.envelope.timeout_seconds,
                    max_retries=consumed.envelope.max_retries,
                    payload=MissionPayload(
                        task=consumed.envelope.payload.task,
                        context=ctx,
                    ),
                )
                self._bus.publish_mission(retry_envelope)
                self._bus.ack(consumed.message_id)
                logger.info(
                    "RedisVpBridge mission retried job_id=%s retry=%d",
                    consumed.envelope.job_id,
                    retry_count,
                )
        except Exception as nested:
            logger.error(
                "RedisVpBridge failed handling failure for job_id=%s: %s",
                consumed.envelope.job_id,
                nested,
            )
