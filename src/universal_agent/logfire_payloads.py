import json
import os
import re
from dataclasses import dataclass
from typing import Any


_SENSITIVE_KEY_RE = re.compile(
    r"(token|secret|password|api[_-]?key|authorization|cookie|set-cookie|private[_-]?key|client[_-]?secret|refresh[_-]?token)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{10,}\b", re.IGNORECASE)
_JWT_RE = re.compile(r"\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{10,}\b")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_REDACTED = "[REDACTED]"


def _is_truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _safe_int(value: str | None, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int((value or "").strip())
    except Exception:
        return default
    return max(min_value, min(max_value, parsed))


@dataclass(frozen=True)
class PayloadLoggingConfig:
    full_payload_mode: bool
    redact_sensitive: bool
    redact_emails: bool
    max_chars: int


def load_payload_logging_config() -> PayloadLoggingConfig:
    return PayloadLoggingConfig(
        full_payload_mode=_is_truthy(os.getenv("UA_LOGFIRE_FULL_PAYLOAD_MODE"), default=False),
        redact_sensitive=_is_truthy(os.getenv("UA_LOGFIRE_FULL_PAYLOAD_REDACT"), default=True),
        redact_emails=_is_truthy(os.getenv("UA_LOGFIRE_FULL_PAYLOAD_REDACT_EMAILS"), default=True),
        max_chars=_safe_int(
            os.getenv("UA_LOGFIRE_FULL_PAYLOAD_MAX_CHARS"),
            default=50000,
            min_value=500,
            max_value=2_000_000,
        ),
    )


def _redact_text(text: str, redact_emails: bool) -> tuple[str, bool]:
    updated = text
    changed = False
    for pattern in (_BEARER_RE, _JWT_RE, _OPENAI_KEY_RE):
        new_value = pattern.sub(_REDACTED, updated)
        if new_value != updated:
            updated = new_value
            changed = True
    if redact_emails:
        new_value = _EMAIL_RE.sub(_REDACTED, updated)
        if new_value != updated:
            updated = new_value
            changed = True
    return updated, changed


def _redact_value(value: Any, *, redact_emails: bool) -> tuple[Any, bool]:
    if isinstance(value, dict):
        changed = False
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if _SENSITIVE_KEY_RE.search(key_str):
                redacted[key] = _REDACTED
                changed = True
                continue
            item_redacted, item_changed = _redact_value(item, redact_emails=redact_emails)
            redacted[key] = item_redacted
            changed = changed or item_changed
        return redacted, changed
    if isinstance(value, list):
        changed = False
        redacted_items: list[Any] = []
        for item in value:
            item_redacted, item_changed = _redact_value(item, redact_emails=redact_emails)
            redacted_items.append(item_redacted)
            changed = changed or item_changed
        return redacted_items, changed
    if isinstance(value, str):
        return _redact_text(value, redact_emails=redact_emails)
    return value, False


def serialize_payload_for_logfire(
    value: Any,
    config: PayloadLoggingConfig,
) -> tuple[str, bool, bool]:
    redacted = False
    source = value
    if config.redact_sensitive:
        source, redacted = _redact_value(value, redact_emails=config.redact_emails)

    if isinstance(source, (dict, list)):
        payload = json.dumps(source, default=str, ensure_ascii=False)
    else:
        payload = str(source)

    truncated = False
    if len(payload) > config.max_chars:
        payload = payload[: config.max_chars] + "...[TRUNCATED]"
        truncated = True
    return payload, truncated, redacted
