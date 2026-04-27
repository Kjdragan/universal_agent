"""Threads webhook contract, verification, and ingest helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import sqlite3
from typing import Any

from pydantic import BaseModel, Field

from csi_ingester.adapters.threads_api import stable_hash, verify_threads_signature
from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.store import (
    dedupe as dedupe_store,
    delivery_attempts as delivery_attempt_store,
    dlq as dlq_store,
    events as event_store,
    source_state as source_state_store,
)

if False:  # pragma: no cover - typing only
    from csi_ingester.emitter.ua_client import UAEmitter


class ThreadsWebhookChange(BaseModel):
    field: str = Field(default="")
    value: dict[str, Any] = Field(default_factory=dict)


class ThreadsWebhookEntry(BaseModel):
    id: str = Field(default="")
    time: int = Field(default=0)
    changes: list[ThreadsWebhookChange] = Field(default_factory=list)


class ThreadsWebhookEnvelope(BaseModel):
    object: str = Field(default="")
    entry: list[ThreadsWebhookEntry] = Field(default_factory=list)


@dataclass(slots=True)
class ThreadsWebhookSettings:
    enabled: bool
    verify_token: str
    app_secret: str


def webhook_settings_from_env() -> ThreadsWebhookSettings:
    enabled = str(os.getenv("CSI_THREADS_WEBHOOK_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}
    verify_token = str(os.getenv("THREADS_WEBHOOK_VERIFY_TOKEN") or "").strip()
    app_secret = str(os.getenv("THREADS_APP_SECRET") or "").strip()
    return ThreadsWebhookSettings(enabled=enabled, verify_token=verify_token, app_secret=app_secret)


def validate_verification_request(*, mode: str, verify_token: str, challenge: str, settings: ThreadsWebhookSettings) -> str:
    if not settings.enabled:
        raise PermissionError("threads_webhook_disabled")
    if str(mode or "").strip().lower() != "subscribe":
        raise ValueError("invalid_mode")
    if not settings.verify_token or verify_token != settings.verify_token:
        raise PermissionError("invalid_verify_token")
    return str(challenge or "")


def validate_signed_payload(*, raw_body: bytes, signature_header: str, settings: ThreadsWebhookSettings) -> bool:
    if not settings.enabled:
        return False
    if not settings.app_secret:
        return False
    return verify_threads_signature(raw_body=raw_body, signature_header=signature_header, app_secret=settings.app_secret)


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_from_epoch(epoch_seconds: Any) -> str:
    try:
        epoch = int(epoch_seconds)
    except Exception:
        return _iso_now()
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _extract_media_id(value: dict[str, Any]) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("media_id", "id", "post_id", "reply_id", "thread_id"):
        raw = str(value.get(key) or "").strip()
        if raw:
            return raw
    return ""


def _event_type_from_field(field: str) -> str:
    clean = str(field or "").strip().lower()
    if "mention" in clean:
        return "threads_mention_observed"
    if "reply" in clean:
        return "threads_reply_observed"
    if "thread" in clean or "post" in clean or "media" in clean:
        return "threads_post_observed"
    return "threads_webhook_change"


def envelope_to_events(envelope: ThreadsWebhookEnvelope) -> list[CreatorSignalEvent]:
    now_iso = _iso_now()
    out: list[CreatorSignalEvent] = []
    for entry in envelope.entry:
        entry_occurred = _iso_from_epoch(entry.time)
        entry_id = str(entry.id or "").strip()
        for change_idx, change in enumerate(entry.changes):
            value = change.value if isinstance(change.value, dict) else {}
            field = str(change.field or "").strip()
            media_id = _extract_media_id(value)
            occurred_at = str(value.get("timestamp") or entry_occurred or now_iso)
            canonical_value = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            change_hash = stable_hash([entry_id, str(entry.time), str(change_idx), field, canonical_value])
            dedupe_key = f"threads:{media_id}" if media_id else f"threads:webhook:{change_hash}"
            event_type = _event_type_from_field(field)
            event_id = f"threads:webhook:{event_type}:{change_hash}"

            event = CreatorSignalEvent(
                event_id=event_id,
                dedupe_key=dedupe_key,
                source="threads_owned",
                event_type=event_type,
                occurred_at=occurred_at,
                received_at=now_iso,
                subject={
                    "platform": "threads",
                    "media_id": media_id,
                    "text": str(value.get("text") or value.get("caption") or ""),
                    "timestamp": occurred_at,
                    "username": str(value.get("username") or ""),
                    "permalink": str(value.get("permalink") or ""),
                    "media_type": str(value.get("media_type") or ""),
                    "shortcode": str(value.get("shortcode") or ""),
                    "reply_count": _safe_int(value.get("reply_count"), 0),
                    "repost_count": _safe_int(value.get("repost_count"), 0),
                    "quote_count": _safe_int(value.get("quote_count"), 0),
                    "like_count": _safe_int(value.get("like_count"), 0),
                    "parent_media_id": str(value.get("parent_id") or value.get("parent_media_id") or ""),
                    "webhook_field": field,
                    "webhook_entry_id": entry_id,
                    "webhook_entry_time": int(_safe_int(entry.time, 0)),
                    "webhook_change_hash": change_hash,
                },
                routing={
                    "pipeline": "creator_watchlist_handler",
                    "priority": "standard",
                    "tags": ["threads", "webhook", "owned"],
                },
                metadata={
                    "source_adapter": "threads_webhook_v1",
                    "webhook_object": str(envelope.object or ""),
                },
            )
            out.append(event)
    return out


async def ingest_threads_webhook_envelope(
    *,
    conn: sqlite3.Connection,
    envelope: ThreadsWebhookEnvelope,
    emitter: "UAEmitter | None",
) -> dict[str, Any]:
    events = envelope_to_events(envelope)
    stats = {
        "status": "accepted",
        "object": str(envelope.object or ""),
        "entries": int(len(envelope.entry)),
        "changes": int(sum(len(entry.changes) for entry in envelope.entry)),
        "normalized": int(len(events)),
        "stored": 0,
        "deduped": 0,
        "delivered": 0,
        "dlq": 0,
        "emit_disabled": 0,
    }
    for event in events:
        if dedupe_store.has_key(conn, event.dedupe_key):
            stats["deduped"] += 1
            continue
        dedupe_store.upsert_key(conn, event.dedupe_key, ttl_days=90)
        event_store.insert_event(conn, event)
        stats["stored"] += 1

        if emitter is None:
            stats["emit_disabled"] += 1
            delivery_attempt_store.record_attempt(
                conn,
                event_id=event.event_id,
                target="ua_signals_ingest",
                delivered=False,
                status_code=503,
                payload={"error": "ua_delivery_not_configured"},
            )
            dlq_store.enqueue(
                conn,
                event_id=event.event_id,
                event=event.model_dump(),
                error_reason="ua_delivery_not_configured",
                retry_count=3,
            )
            stats["dlq"] += 1
            continue

        delivered, status_code, payload = await emitter.emit_with_retries([event])
        delivery_attempt_store.record_attempt(
            conn,
            event_id=event.event_id,
            target="ua_signals_ingest",
            delivered=bool(delivered),
            status_code=int(status_code or 0),
            payload=payload if isinstance(payload, dict) else {"payload": payload},
        )
        if delivered:
            event_store.mark_delivered(conn, event.event_id)
            stats["delivered"] += 1
        else:
            dlq_store.enqueue(
                conn,
                event_id=event.event_id,
                event=event.model_dump(),
                error_reason=f"ua_status_{status_code}",
                retry_count=3,
            )
            stats["dlq"] += 1

    _update_threads_webhook_state(conn=conn, stats=stats)
    return stats


def _update_threads_webhook_state(*, conn: sqlite3.Connection, stats: dict[str, Any]) -> None:
    state_key = "threads_webhook:state"
    prev = source_state_store.get_state(conn, state_key)
    base = prev if isinstance(prev, dict) else {}
    totals = base.get("totals") if isinstance(base.get("totals"), dict) else {}
    payload = {
        "last_ingested_at": _iso_now(),
        "last_cycle": {
            "entries": int(stats.get("entries") or 0),
            "changes": int(stats.get("changes") or 0),
            "normalized": int(stats.get("normalized") or 0),
            "stored": int(stats.get("stored") or 0),
            "deduped": int(stats.get("deduped") or 0),
            "delivered": int(stats.get("delivered") or 0),
            "dlq": int(stats.get("dlq") or 0),
            "emit_disabled": int(stats.get("emit_disabled") or 0),
        },
        "totals": {
            "requests": int(totals.get("requests") or 0) + 1,
            "entries": int(totals.get("entries") or 0) + int(stats.get("entries") or 0),
            "changes": int(totals.get("changes") or 0) + int(stats.get("changes") or 0),
            "normalized": int(totals.get("normalized") or 0) + int(stats.get("normalized") or 0),
            "stored": int(totals.get("stored") or 0) + int(stats.get("stored") or 0),
            "deduped": int(totals.get("deduped") or 0) + int(stats.get("deduped") or 0),
            "delivered": int(totals.get("delivered") or 0) + int(stats.get("delivered") or 0),
            "dlq": int(totals.get("dlq") or 0) + int(stats.get("dlq") or 0),
            "emit_disabled": int(totals.get("emit_disabled") or 0) + int(stats.get("emit_disabled") or 0),
        },
    }
    source_state_store.set_state(conn, state_key, payload)
