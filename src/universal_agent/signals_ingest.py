"""CSI -> UA signal ingest validation and response handling."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from universal_agent.youtube_mode_utils import (
    MODE_EXPLAINER_ONLY,
    MODE_EXPLAINER_PLUS_CODE,
    infer_youtube_mode as _infer_youtube_learning_mode,
)


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, minimum: int = 0, maximum: int = 86_400) -> int:
    raw = os.getenv(name)
    if raw is None:
        return max(minimum, min(maximum, int(default)))
    try:
        value = int(raw.strip())
    except Exception:
        return max(minimum, min(maximum, int(default)))
    return max(minimum, min(maximum, value))


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
    # Only playlist events should trigger full UA YouTube learning dispatch.
    # RSS channel events are handled by CSI-side enrichment/analytics pipelines.
    if str(event.source or "").strip() != "youtube_playlist":
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
    mode = _infer_youtube_learning_mode(
        subject.get("title"),
        subject.get("description"),
        subject.get("channel_title"),
        subject.get("channel_id"),
        subject.get("url"),
    )
    return {
        "video_url": video_url,
        "video_id": video_id,
        "channel_id": str(subject.get("channel_id") or ""),
        "title": str(subject.get("title") or ""),
        "mode": mode,
        "allow_degraded_transcript_only": True,
        "source": "csi_ingester",
    }


def _safe_json_preview(value: Any, *, max_chars: int = 6000) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except Exception:
        rendered = str(value)
    if len(rendered) <= max_chars:
        return rendered
    return f"{rendered[:max_chars]}...(truncated)"


def _category_mix(subject: dict[str, Any]) -> str:
    totals = subject.get("totals")
    by_category = {}
    if isinstance(totals, dict):
        maybe = totals.get("by_category")
        if isinstance(maybe, dict):
            by_category = maybe
    if not by_category:
        maybe_direct = subject.get("by_category")
        if isinstance(maybe_direct, dict):
            by_category = maybe_direct
    if not isinstance(by_category, dict) or not by_category:
        return ""
    parts: list[str] = []
    for key in ("ai", "political", "war", "other_interest"):
        if key in by_category:
            parts.append(f"{key}={int(by_category.get(key) or 0)}")
    extras = [(str(k), int(v or 0)) for k, v in by_category.items() if str(k) not in {"ai", "political", "war", "other_interest"}]
    extras.sort(key=lambda item: item[1], reverse=True)
    for slug, count in extras[:4]:
        parts.append(f"{slug}={count}")
    return ", ".join(parts)


def _analytics_message(event: CreatorSignalEvent) -> str:
    event_type = str(event.event_type or "").strip()
    subject = event.subject if isinstance(event.subject, dict) else {}
    occurred_at = str(event.occurred_at or "")
    lines: list[str] = []
    lines.append("CSI analytics signal received.")
    lines.append(f"event_type: {event_type}")
    lines.append(f"source: {str(event.source or '')}")
    lines.append(f"event_id: {str(event.event_id or '')}")
    lines.append(f"occurred_at: {occurred_at}")

    # Dispatch table: each handler appends event-specific lines in-place.
    for prefix, handler in _ANALYTICS_HANDLERS:
        if event_type == prefix or event_type.startswith(prefix):
            handler(event_type, subject, lines)
            break

    lines.append("")
    lines.append("subject_json:")
    lines.append(_safe_json_preview(subject, max_chars=8000))
    return "\n".join(lines)


def _handle_hourly_token_usage(_event_type: str, subject: dict, lines: list[str]) -> None:
    totals = subject.get("totals") if isinstance(subject.get("totals"), dict) else {}
    lines.append(
        "hourly_tokens: "
        f"prompt={int(totals.get('prompt_tokens') or 0)} "
        f"completion={int(totals.get('completion_tokens') or 0)} "
        f"total={int(totals.get('total_tokens') or 0)}"
    )


def _handle_rss_trend_report(_event_type: str, subject: dict, lines: list[str]) -> None:
    totals = subject.get("totals") if isinstance(subject.get("totals"), dict) else {}
    lines.append(f"window: {str(subject.get('window_start_utc') or '')} -> {str(subject.get('window_end_utc') or '')}")
    lines.append(f"items: {int(totals.get('items') or 0)}")
    mix = _category_mix(subject)
    if mix:
        lines.append(f"category_mix: {mix}")
    top_themes = subject.get("top_themes")
    if isinstance(top_themes, list) and top_themes:
        lines.append(f"top_themes_preview: {_safe_json_preview(top_themes[:6], max_chars=800)}")


def _handle_threads_trend_report(_event_type: str, subject: dict, lines: list[str]) -> None:
    lines.append(f"report_key: {str(subject.get('report_key') or '')}")
    lines.append(f"window: {str(subject.get('window_start_utc') or '')} -> {str(subject.get('window_end_utc') or '')}")
    lines.append(f"items: {int(subject.get('total_items') or 0)}")
    top_terms = subject.get("top_terms")
    if isinstance(top_terms, list) and top_terms:
        lines.append(f"top_terms_preview: {_safe_json_preview(top_terms[:8], max_chars=800)}")


def _handle_global_trend_brief_ready(_event_type: str, subject: dict, lines: list[str]) -> None:
    lines.append(f"brief_key: {str(subject.get('brief_key') or '')}")
    lines.append(f"window: {str(subject.get('window_start_utc') or '')} -> {str(subject.get('window_end_utc') or '')}")
    source_totals = subject.get("source_totals") if isinstance(subject.get("source_totals"), dict) else {}
    lines.append(f"source_totals: {_safe_json_preview(source_totals, max_chars=300)}")


def _handle_csi_global_brief_review_due(_event_type: str, subject: dict, lines: list[str]) -> None:
    lines.append(f"brief_key: {str(subject.get('brief_key') or '')}")
    lines.append(f"slot: {str(subject.get('slot_display') or subject.get('slot') or '')}")
    lines.append(f"timezone: {str(subject.get('timezone') or '')}")


def _handle_rss_insight(_event_type: str, subject: dict, lines: list[str]) -> None:
    lines.append(f"report_key: {str(subject.get('report_key') or '')}")
    lines.append(f"items: {int(subject.get('total_items') or 0)}")
    mix = _category_mix(subject)
    if mix:
        lines.append(f"category_mix: {mix}")


def _handle_category_quality_report(_event_type: str, subject: dict, lines: list[str]) -> None:
    metrics = subject.get("metrics") if isinstance(subject.get("metrics"), dict) else {}
    lines.append(f"quality_action: {str(subject.get('action') or '')}")
    lines.append(
        "quality_metrics: "
        f"items={int(metrics.get('total_items') or 0)} "
        f"other_ratio={float(metrics.get('other_interest_ratio') or 0.0):.4f} "
        f"uncategorized={int(metrics.get('uncategorized_items') or 0)}"
    )


def _handle_analysis_task(_event_type: str, subject: dict, lines: list[str]) -> None:
    lines.append(f"task_id: {str(subject.get('task_id') or '')}")
    lines.append(f"request_type: {str(subject.get('request_type') or '')}")
    lines.append(f"task_status: {str(subject.get('status') or '')}")


# Ordered dispatch table for _analytics_message.
# Each entry is (prefix, handler). First match wins; prefix "" is a catch-all.
_ANALYTICS_HANDLERS: list[tuple[str, callable]] = [
    ("hourly_token_usage_report", _handle_hourly_token_usage),
    ("rss_trend_report", _handle_rss_trend_report),
    ("threads_trend_report", _handle_threads_trend_report),
    ("global_trend_brief_ready", _handle_global_trend_brief_ready),
    ("csi_global_brief_review_due", _handle_csi_global_brief_review_due),
    ("rss_insight_", _handle_rss_insight),
    ("category_quality_report", _handle_category_quality_report),
    ("analysis_task_", _handle_analysis_task),
]


def to_csi_analytics_action(event: CreatorSignalEvent) -> dict[str, Any] | None:
    """
    Map CSI-native analytics/analyst events to an internal UA agent action.

    CSI Redesign (2026-03-15): Disabled. CSI analytics events are no longer
    dispatched as agent actions. The operational monitoring layer (auto-remediation,
    SLO, delivery health, specialist loops) was generating excessive noise and
    burning tokens without producing user value. CSI now operates as a passive
    trend digest system — interesting findings are stored as digests for the
    UI to display, not dispatched to agent lanes.

    YouTube playlist processing is unaffected (handled by to_manual_youtube_payload).
    """
    return None


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
