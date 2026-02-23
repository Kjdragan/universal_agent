from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class GoogleErrorClass(StrEnum):
    AUTH_REVOKED = "auth_revoked"
    SCOPE_DENIED = "scope_denied"
    RATE_LIMIT = "rate_limit"
    TRANSIENT = "transient"
    PERMANENT = "permanent"


class RecoveryAction(StrEnum):
    REAUTHORIZE = "reauthorize"
    REQUEST_ADDITIONAL_SCOPE = "request_additional_scope"
    RETRY_WITH_BACKOFF = "retry_with_backoff"
    FALLBACK_TO_COMPOSIO = "fallback_to_composio"
    FAIL_FAST = "fail_fast"


@dataclass(frozen=True)
class ErrorHandlingDecision:
    error_class: GoogleErrorClass
    action: RecoveryAction
    should_retry: bool
    max_attempts: int
    allow_fallback: bool
    reason: str


_AUTH_REVOKED_TOKENS = (
    "invalid_grant",
    "invalid_token",
    "token has been expired or revoked",
    "reauth related error",
)

_SCOPE_DENIED_TOKENS = (
    "insufficientpermissions",
    "insufficient permissions",
    "request had insufficient authentication scopes",
    "permission denied",
)


def classify_http_error(status_code: int, error_text: str = "") -> GoogleErrorClass:
    text = error_text.strip().lower()

    if status_code == 401 or any(token in text for token in _AUTH_REVOKED_TOKENS):
        return GoogleErrorClass.AUTH_REVOKED
    if status_code == 403 or any(token in text for token in _SCOPE_DENIED_TOKENS):
        return GoogleErrorClass.SCOPE_DENIED
    if status_code == 429:
        return GoogleErrorClass.RATE_LIMIT
    if 500 <= status_code <= 599:
        return GoogleErrorClass.TRANSIENT
    return GoogleErrorClass.PERMANENT


def decide_error_handling(
    status_code: int,
    error_text: str = "",
    *,
    allow_composio_fallback: bool = True,
) -> ErrorHandlingDecision:
    error_class = classify_http_error(status_code, error_text)

    if error_class is GoogleErrorClass.AUTH_REVOKED:
        return ErrorHandlingDecision(
            error_class=error_class,
            action=RecoveryAction.REAUTHORIZE,
            should_retry=False,
            max_attempts=0,
            allow_fallback=allow_composio_fallback,
            reason="Refresh/access token is invalid or revoked; user re-consent is required.",
        )

    if error_class is GoogleErrorClass.SCOPE_DENIED:
        return ErrorHandlingDecision(
            error_class=error_class,
            action=RecoveryAction.REQUEST_ADDITIONAL_SCOPE,
            should_retry=False,
            max_attempts=0,
            allow_fallback=allow_composio_fallback,
            reason="Granted scopes are insufficient for the requested operation.",
        )

    if error_class is GoogleErrorClass.RATE_LIMIT:
        return ErrorHandlingDecision(
            error_class=error_class,
            action=RecoveryAction.RETRY_WITH_BACKOFF,
            should_retry=True,
            max_attempts=4,
            allow_fallback=allow_composio_fallback,
            reason="Rate-limited by provider; retry with exponential backoff and jitter.",
        )

    if error_class is GoogleErrorClass.TRANSIENT:
        return ErrorHandlingDecision(
            error_class=error_class,
            action=RecoveryAction.RETRY_WITH_BACKOFF,
            should_retry=True,
            max_attempts=3,
            allow_fallback=allow_composio_fallback,
            reason="Transient provider/server failure.",
        )

    if allow_composio_fallback:
        return ErrorHandlingDecision(
            error_class=error_class,
            action=RecoveryAction.FALLBACK_TO_COMPOSIO,
            should_retry=False,
            max_attempts=0,
            allow_fallback=True,
            reason="Non-retryable direct-path failure; fail over to Composio when safe.",
        )

    return ErrorHandlingDecision(
        error_class=error_class,
        action=RecoveryAction.FAIL_FAST,
        should_retry=False,
        max_attempts=0,
        allow_fallback=False,
        reason="Non-retryable direct-path failure and fallback disabled.",
    )
