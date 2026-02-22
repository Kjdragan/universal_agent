"""CSI -> UA signal ingest validation and response handling."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any

from pydantic import BaseModel, Field, ValidationError


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _split_csv_env(name: str) -> set[str]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def _extract_signature_hex(signature_header: str) -> str | None:
    value = (signature_header or "").strip()
    if not value:
        return None
    if value.startswith("sha256="):
        hex_part = value.split("=", 1)[1].strip()
        return hex_part or None
    return None


class CreatorSignalEvent(BaseModel):
    event_id: str
    dedupe_key: str
    source: str
    event_type: str
    occurred_at: str
    received_at: str
    emitted_at: str | None = None
    subject: dict[str, Any]
    routing: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw_ref: str | None = None
    contract_version: str = "1.0"


class CSIIngestRequest(BaseModel):
    csi_version: str
    csi_instance_id: str
    batch_id: str
    events: list[dict[str, Any]] = Field(..., max_length=100)


def extract_valid_events(payload: dict[str, Any]) -> list[CreatorSignalEvent]:
    """Return validated events from an ingest payload, ignoring invalid entries."""
    try:
        envelope = CSIIngestRequest.model_validate(payload)
    except ValidationError:
        return []
    valid: list[CreatorSignalEvent] = []
    seen_event_ids: set[str] = set()
    for event_payload in envelope.events:
        try:
            event = CreatorSignalEvent.model_validate(event_payload)
        except ValidationError:
            continue
        if event.event_id in seen_event_ids:
            continue
        seen_event_ids.add(event.event_id)
        valid.append(event)
    return valid


def to_manual_youtube_payload(event: CreatorSignalEvent) -> dict[str, Any] | None:
    """Map a CSI event to UA manual YouTube hook payload when applicable."""
    if str(event.source or "").strip() not in {"youtube_playlist", "youtube_channel_rss"}:
        return None
    subject = event.subject if isinstance(event.subject, dict) else {}
    if str(subject.get("platform") or "").strip().lower() != "youtube":
        return None
    video_url = str(subject.get("url") or "").strip()
    video_id = str(subject.get("video_id") or "").strip()
    if not video_url and video_id:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
    if not video_url:
        return None
    routing = event.routing if isinstance(event.routing, dict) else {}
    priority = str(routing.get("priority") or "standard").strip().lower()
    mode = "explainer_only"
    if priority in {"urgent", "high"}:
        mode = "explainer_plus_code"
    return {
        "video_url": video_url,
        "video_id": video_id,
        "channel_id": str(subject.get("channel_id") or ""),
        "title": str(subject.get("title") or ""),
        "mode": mode,
        "allow_degraded_transcript_only": True,
        "source": "csi_ingester",
    }


def _verify_auth(headers: dict[str, str], payload: dict[str, Any]) -> tuple[bool, int, dict[str, Any]]:
    if not _bool_env("UA_SIGNALS_INGEST_ENABLED", default=False):
        return False, 503, {"ok": False, "error": "signals_ingest_disabled"}

    shared_secret = (os.getenv("UA_SIGNALS_INGEST_SHARED_SECRET") or "").strip()
    if not shared_secret:
        return False, 503, {"ok": False, "error": "signals_ingest_secret_missing"}

    auth_header = (headers.get("authorization") or "").strip()
    if not auth_header.lower().startswith("bearer "):
        return False, 401, {"ok": False, "error": "unauthorized"}
    token = auth_header.split(" ", 1)[1].strip()
    if not hmac.compare_digest(token, shared_secret):
        return False, 401, {"ok": False, "error": "unauthorized"}

    request_id = (headers.get("x-csi-request-id") or "").strip()
    ts_raw = (headers.get("x-csi-timestamp") or "").strip()
    signature_header = (headers.get("x-csi-signature") or "").strip()
    signature_hex = _extract_signature_hex(signature_header)
    if not request_id or not ts_raw or not signature_hex:
        return False, 401, {"ok": False, "error": "unauthorized"}

    try:
        timestamp = int(ts_raw)
    except ValueError:
        return False, 401, {"ok": False, "error": "unauthorized"}

    tolerance = max(30, int((os.getenv("UA_SIGNALS_INGEST_TIMESTAMP_TOLERANCE_SECONDS") or "300").strip() or 300))
    now_ts = int(time.time())
    if abs(now_ts - timestamp) > tolerance:
        return False, 401, {"ok": False, "error": "unauthorized"}

    signing_string = f"{timestamp}.{request_id}.{_canonical_json(payload)}"
    expected_hex = hmac.new(
        shared_secret.encode("utf-8"),
        signing_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_hex, signature_hex):
        return False, 401, {"ok": False, "error": "unauthorized"}
    return True, 200, {}


def process_signals_ingest_payload(payload: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
    ok, status_code, auth_error = _verify_auth(headers, payload)
    if not ok:
        return status_code, auth_error

    try:
        envelope = CSIIngestRequest.model_validate(payload)
    except ValidationError as exc:
        return 400, {"ok": False, "error": "invalid_request", "detail": exc.errors()}

    allowed_instances = _split_csv_env("UA_SIGNALS_INGEST_ALLOWED_INSTANCES")
    if allowed_instances and envelope.csi_instance_id not in allowed_instances:
        return 403, {"ok": False, "error": "instance_not_allowed"}

    accepted = 0
    errors: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    for idx, event_payload in enumerate(envelope.events):
        try:
            event = CreatorSignalEvent.model_validate(event_payload)
        except ValidationError:
            errors.append({"index": idx, "error": "invalid_schema"})
            continue
        if event.event_id in seen_event_ids:
            errors.append({"index": idx, "error": "duplicate_event"})
            continue
        seen_event_ids.add(event.event_id)
        accepted += 1

    rejected = len(errors)
    body = {
        "ok": rejected == 0,
        "accepted": accepted,
        "rejected": rejected,
        "errors": errors,
    }
    if rejected == 0:
        return 200, body
    if accepted > 0:
        return 207, body
    return 400, body
