"""Factory heartbeat sender — periodically refreshes registration with HQ.

Sends a POST to ``{hq_base_url}/api/v1/factory/registrations`` with the
factory's current capabilities and metadata.  The Corporation View on HQ
uses ``last_seen_at`` for stale detection (>5 min threshold).

Designed to run as an asyncio task alongside the Redis→SQLite bridge.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class HeartbeatConfig:
    """Configuration for the factory heartbeat sender."""

    hq_base_url: str = ""
    ops_token: str = ""
    factory_id: str = ""
    factory_role: str = "LOCAL_WORKER"
    deployment_profile: str = "local_workstation"
    capabilities: list[str] = field(default_factory=list)
    interval_seconds: float = 60.0
    timeout_seconds: float = 15.0

    @classmethod
    def from_env(cls) -> "HeartbeatConfig":
        """Build config from environment variables (loaded via Infisical)."""
        hq_url = (
            str(os.getenv("UA_HQ_BASE_URL") or "").strip()
            or str(os.getenv("UA_BASE_URL") or "").strip()
            or str(os.getenv("UA_GATEWAY_URL") or "").strip()
        )
        ops_token = str(os.getenv("UA_OPS_TOKEN") or "").strip()
        factory_id = (
            str(os.getenv("UA_FACTORY_ID") or "").strip()
            or str(os.getenv("INFISICAL_ENVIRONMENT") or "").strip()
            or socket.gethostname()
        )
        factory_role = str(os.getenv("FACTORY_ROLE") or "LOCAL_WORKER").strip()
        deployment_profile = str(
            os.getenv("UA_DEPLOYMENT_PROFILE") or "local_workstation"
        ).strip()
        interval = float(os.getenv("UA_HEARTBEAT_INTERVAL_SECONDS", "60") or 60)

        capabilities = []
        if os.getenv("UA_DELEGATION_REDIS_ENABLED", "").strip() == "1":
            capabilities.append("delegation_redis")
        if os.getenv("ENABLE_VP_CODER", "").strip().lower() in ("1", "true"):
            capabilities.append("vp_coder")
        if os.getenv("ENABLE_VP_GENERAL", "").strip().lower() in ("1", "true"):
            capabilities.append("vp_general")
        capabilities.append(f"delegation_mode:listen_only")
        capabilities.append(f"heartbeat_scope:local")

        return cls(
            hq_base_url=hq_url,
            ops_token=ops_token,
            factory_id=factory_id,
            factory_role=factory_role,
            deployment_profile=deployment_profile,
            capabilities=capabilities,
            interval_seconds=max(10.0, interval),
        )


class FactoryHeartbeat:
    """Sends periodic registration heartbeats to HQ."""

    def __init__(
        self,
        config: HeartbeatConfig,
        paused_callback: Optional["Callable[[], bool]"] = None,
    ) -> None:
        self._config = config
        self._paused_callback = paused_callback
        self._last_sent_at: float = 0.0
        self._consecutive_failures: int = 0
        self._stopped = asyncio.Event()
        self._start_time = time.time()

    @property
    def is_healthy(self) -> bool:
        return self._consecutive_failures < 3

    @property
    def last_sent_at(self) -> float:
        return self._last_sent_at

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def stop(self) -> None:
        self._stopped.set()

    def _effective_interval(self) -> float:
        """Backoff: double interval per consecutive failure, cap at 5 min."""
        base = self._config.interval_seconds
        if self._consecutive_failures <= 0:
            return base
        return min(base * (2 ** self._consecutive_failures), 300.0)

    def _build_payload(self, latency_ms: Optional[float] = None) -> dict[str, Any]:
        is_paused = self._paused_callback() if self._paused_callback else False
        return {
            "factory_id": self._config.factory_id,
            "factory_role": self._config.factory_role,
            "registration_status": "paused" if is_paused else "online",
            "heartbeat_latency_ms": latency_ms,
            "capabilities": self._config.capabilities,
            "metadata": {
                "hostname": socket.gethostname(),
                "pid": os.getpid(),
                "uptime_seconds": round(time.time() - self._start_time, 1),
                "platform": platform.system(),
                "deployment_profile": self._config.deployment_profile,
                "heartbeat_source": "bridge_heartbeat",
            },
        }

    async def send(self) -> bool:
        """Send one heartbeat POST to HQ.  Returns True on success."""
        if not self._config.hq_base_url:
            logger.debug("FactoryHeartbeat: no HQ URL configured, skipping")
            return False

        url = f"{self._config.hq_base_url.rstrip('/')}/api/v1/factory/registrations"
        headers: dict[str, str] = {}
        if self._config.ops_token:
            headers["x-ua-ops-token"] = self._config.ops_token

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
                payload = self._build_payload()
                resp = await client.post(url, json=payload, headers=headers)
                latency_ms = round((time.monotonic() - t0) * 1000, 1)

                if resp.status_code in (200, 201):
                    self._last_sent_at = time.time()
                    self._consecutive_failures = 0
                    logger.info(
                        "FactoryHeartbeat sent factory_id=%s latency=%.1fms",
                        self._config.factory_id,
                        latency_ms,
                    )
                    return True
                else:
                    self._consecutive_failures += 1
                    logger.warning(
                        "FactoryHeartbeat rejected: %d %s (failures=%d)",
                        resp.status_code,
                        resp.text[:200],
                        self._consecutive_failures,
                    )
                    return False
        except Exception as exc:
            self._consecutive_failures += 1
            logger.warning(
                "FactoryHeartbeat failed: %s (failures=%d)",
                exc,
                self._consecutive_failures,
            )
            return False

    async def run(self) -> None:
        """Run heartbeat loop until stopped."""
        logger.info(
            "FactoryHeartbeat starting factory_id=%s interval=%.0fs hq=%s",
            self._config.factory_id,
            self._config.interval_seconds,
            self._config.hq_base_url or "(not configured)",
        )

        if not self._config.hq_base_url:
            logger.warning(
                "FactoryHeartbeat: UA_HQ_BASE_URL not set — heartbeat disabled. "
                "Set UA_HQ_BASE_URL in Infisical to enable."
            )
            # Still wait for stop so we don't exit the task group
            await self._stopped.wait()
            return

        # Send initial heartbeat immediately
        await self.send()

        while not self._stopped.is_set():
            interval = self._effective_interval()
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

            if self._stopped.is_set():
                break

            await self.send()

        logger.info(
            "FactoryHeartbeat stopped. failures=%d",
            self._consecutive_failures,
        )
