"""UA HTTP emitter."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any

import httpx

from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.signature import generate_signature

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

_TRANSIENT_HTTP_STATUSES = {502, 503, 504}
_PERMANENT_HTTP_STATUSES = {400, 401, 403, 404}


def classify_emission_failure(
    *,
    exc: Exception | None = None,
    status_code: int = 0,
    maintenance_mode: bool = False,
) -> str:
    """Return a failure_class string for a delivery failure."""
    if maintenance_mode:
        return "maintenance_mode"
    if exc is not None:
        exc_name = type(exc).__name__.lower()
        exc_str = str(exc).lower()
        if isinstance(exc, httpx.ConnectError) or "connecterror" in exc_name or "connection refused" in exc_str:
            return "transient_connection"
        if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)) or "timeout" in exc_name:
            return "transient_timeout"
        return "unknown"
    if status_code in _TRANSIENT_HTTP_STATUSES:
        return "transient_server"
    if status_code == 429:
        return "transient_rate_limit"
    if status_code in _PERMANENT_HTTP_STATUSES:
        return "permanent_client_error"
    if status_code == 0:
        return "transient_connection"
    return "unknown"


def is_transient_failure(failure_class: str) -> bool:
    """True if the failure_class represents a transient/recoverable issue."""
    return failure_class.startswith("transient_") or failure_class == "maintenance_mode"


class UAEmitter:
    def __init__(self, *, endpoint: str, shared_secret: str, instance_id: str, csi_version: str = "1.0.0") -> None:
        self.endpoint = endpoint
        self.shared_secret = shared_secret
        self.instance_id = instance_id
        self.csi_version = csi_version
        # Allow overriding via env var; default 120s to accommodate heavy analytics payloads
        self.emit_timeout_seconds = int(os.environ.get("CSI_UA_EMIT_TIMEOUT_SECONDS", "120"))

    async def emit_batch(self, events: list[CreatorSignalEvent], timeout_seconds: int = 30) -> tuple[int, dict[str, Any]]:
        payload = {
            "csi_version": self.csi_version,
            "csi_instance_id": self.instance_id,
            "batch_id": f"batch_{uuid.uuid4().hex[:16]}",
            "events": [event.model_dump() for event in events],
        }
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        signature, timestamp = generate_signature(self.shared_secret, request_id, payload)
        headers = {
            "Authorization": f"Bearer {self.shared_secret}",
            "Content-Type": "application/json",
            "X-CSI-Signature": f"sha256={signature}",
            "X-CSI-Timestamp": timestamp,
            "X-CSI-Request-ID": request_id,
        }
        try:
            async with httpx.AsyncClient(timeout=max(5, timeout_seconds)) as client:
                response = await client.post(self.endpoint, headers=headers, json=payload)
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            fc = classify_emission_failure(exc=exc)
            return 0, {"ok": False, "failure_class": fc, "error": f"{type(exc).__name__}: {exc}"}
        except Exception as exc:
            fc = classify_emission_failure(exc=exc)
            return 0, {"ok": False, "failure_class": fc, "error": f"{type(exc).__name__}: {exc}"}

        body: dict[str, Any]
        try:
            decoded = response.json()
            body = decoded if isinstance(decoded, dict) else {"payload": decoded}
        except Exception:
            body = {"raw_text": response.text[:500]}
        if response.status_code not in range(200, 300):
            body.setdefault("failure_class", classify_emission_failure(status_code=response.status_code))
        return response.status_code, body

    async def emit_with_retries(
        self,
        events: list[CreatorSignalEvent],
        max_attempts: int = 5,
        *,
        maintenance_mode: bool = False,
    ) -> tuple[bool, int, dict[str, Any]]:
        from csi_ingester.emitter.retry import exponential_delay_seconds

        if maintenance_mode:
            body: dict[str, Any] = {
                "ok": False,
                "failure_class": "maintenance_mode",
                "error": "UA maintenance mode active — event queued to DLQ",
            }
            logger.info("UA maintenance mode active, skipping network emit")
            return False, 0, body

        last_status = 0
        last_body: dict[str, Any] = {}
        for attempt in range(1, max_attempts + 1):
            status_code, payload = await self.emit_batch(events, timeout_seconds=self.emit_timeout_seconds)
            last_status, last_body = status_code, payload
            if 200 <= status_code < 300:
                return True, status_code, payload
            if status_code in _PERMANENT_HTTP_STATUSES:
                payload.setdefault("failure_class", "permanent_client_error")
                return False, status_code, payload
            if status_code == 409:
                return True, status_code, payload
            if attempt >= max_attempts:
                break
            delay = exponential_delay_seconds(attempt + 1)
            fc = str(payload.get("failure_class") or classify_emission_failure(status_code=status_code))
            logger.info(
                "UA emit attempt %d/%d failed (%s, status=%s), retrying in %.1fs",
                attempt, max_attempts, fc, status_code, delay,
            )
            await asyncio.sleep(delay)

        fc = str(last_body.get("failure_class") or classify_emission_failure(status_code=last_status))
        last_body.setdefault("failure_class", fc)
        if is_transient_failure(fc):
            logger.info("UA emit failed after %d attempts (transient: %s), event queued to DLQ", max_attempts, fc)
        else:
            logger.warning("UA emit failed status=%s body=%s", last_status, last_body)
        return False, last_status, last_body

