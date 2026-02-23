"""Helpers for CSI analytics event emission and DLQ fallback."""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

from csi_ingester.config import CSIConfig
from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.emitter.ua_client import UAEmitter
from csi_ingester.store import dlq as dlq_store
from csi_ingester.store import events as event_store


def emit_and_track(
    conn: sqlite3.Connection,
    *,
    config: CSIConfig,
    event: CreatorSignalEvent,
    retry_count: int = 3,
) -> tuple[bool, int, dict[str, Any]]:
    event_store.insert_event(conn, event)
    if not config.ua_endpoint or not config.ua_shared_secret:
        dlq_store.enqueue(
            conn,
            event_id=event.event_id,
            event=event.model_dump(),
            error_reason="ua_delivery_not_configured",
            retry_count=retry_count,
        )
        return False, 503, {"error": "ua_delivery_not_configured"}
    emitter = UAEmitter(
        endpoint=config.ua_endpoint,
        shared_secret=config.ua_shared_secret,
        instance_id=config.instance_id,
    )
    try:
        delivered, status_code, payload = asyncio.run(
            emitter.emit_with_retries([event], max_attempts=retry_count)
        )
    except Exception as exc:
        delivered = False
        status_code = 599
        payload = {"error": f"emit_exception:{type(exc).__name__}", "detail": str(exc)[:400]}
    if delivered:
        event_store.mark_delivered(conn, event.event_id)
        return True, status_code, payload
    dlq_store.enqueue(
        conn,
        event_id=event.event_id,
        event=event.model_dump(),
        error_reason=f"ua_status_{status_code}",
        retry_count=retry_count,
    )
    return False, status_code, payload
