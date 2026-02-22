"""CSI request signing helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any


def canonical_json_body(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def build_signing_string(timestamp: str, request_id: str, payload: dict[str, Any]) -> str:
    return f"{timestamp}.{request_id}.{canonical_json_body(payload)}"


def generate_signature(shared_secret: str, request_id: str, payload: dict[str, Any]) -> tuple[str, str]:
    timestamp = str(int(time.time()))
    signing_string = build_signing_string(timestamp, request_id, payload)
    signature = hmac.new(
        shared_secret.encode("utf-8"),
        signing_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return signature, timestamp


def verify_signature(
    *,
    shared_secret: str,
    request_id: str,
    timestamp: str,
    payload: dict[str, Any],
    signature_hex: str,
) -> bool:
    signing_string = build_signing_string(timestamp, request_id, payload)
    expected = hmac.new(
        shared_secret.encode("utf-8"),
        signing_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_hex)

