"""UA HTTP emitter."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import httpx

from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.signature import generate_signature

logger = logging.getLogger(__name__)


class UAEmitter:
    def __init__(self, *, endpoint: str, shared_secret: str, instance_id: str, csi_version: str = "1.0.0") -> None:
        self.endpoint = endpoint
        self.shared_secret = shared_secret
        self.instance_id = instance_id
        self.csi_version = csi_version

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
        async with httpx.AsyncClient(timeout=max(5, timeout_seconds)) as client:
            response = await client.post(self.endpoint, headers=headers, json=payload)
        body: dict[str, Any]
        try:
            decoded = response.json()
            body = decoded if isinstance(decoded, dict) else {"payload": decoded}
        except Exception:
            body = {"raw_text": response.text[:500]}
        return response.status_code, body

    async def emit_with_retries(self, events: list[CreatorSignalEvent], max_attempts: int = 3) -> tuple[bool, int, dict[str, Any]]:
        from csi_ingester.emitter.retry import retry_delay_seconds

        last_status = 0
        last_body: dict[str, Any] = {}
        for attempt in range(1, max_attempts + 1):
            status_code, payload = await self.emit_batch(events)
            last_status, last_body = status_code, payload
            if 200 <= status_code < 300:
                return True, status_code, payload
            if status_code in {400, 401, 403, 404}:
                return False, status_code, payload
            if status_code == 409:
                return True, status_code, payload
            if attempt >= max_attempts:
                break
            await asyncio.sleep(retry_delay_seconds(attempt + 1))
        logger.warning("UA emit failed status=%s body=%s", last_status, last_body)
        return False, last_status, last_body

