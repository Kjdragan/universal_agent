"""Delivery attempt persistence for CSI emit paths."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def classify_delivery_error(*, status_code: int, payload: dict[str, Any] | None) -> str:
    """Return a compact error class for telemetry grouping."""
    code = int(status_code or 0)
    body = payload if isinstance(payload, dict) else {}
    raw_error = str(body.get("error") or "").strip().lower()
    raw_detail = str(body.get("detail") or "").strip().lower()
    joined = f"{raw_error} {raw_detail}".strip()

    if code in {0, 599}:
        if "timeout" in joined:
            return "timeout"
        if "connect" in joined:
            return "connect"
        if raw_error.startswith("emit_exception:"):
            return raw_error.split(":", 1)[1].strip() or "emit_exception"
        return "emit_exception"
    if code in {401, 403}:
        return "auth"
    if code in {400, 404}:
        return "request"
    if 500 <= code <= 599:
        return "upstream_5xx"
    return "unknown"


def record_attempt(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    target: str,
    delivered: bool,
    status_code: int,
    payload: dict[str, Any] | None = None,
) -> None:
    body = payload if isinstance(payload, dict) else {}
    error_class = ""
    error_detail = ""
    if not bool(delivered):
        error_class = classify_delivery_error(status_code=int(status_code or 0), payload=body)
        # Keep details compact for diagnostics; avoid large blobs in telemetry rows.
        if body:
            error_detail = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)[:800]
    conn.execute(
        """
        INSERT INTO delivery_attempts (
            event_id, target, delivered, status_code, error_class, error_detail
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(event_id or "").strip(),
            str(target or "ua_signals_ingest").strip() or "ua_signals_ingest",
            1 if bool(delivered) else 0,
            int(status_code or 0),
            error_class,
            error_detail,
        ),
    )
    conn.commit()

