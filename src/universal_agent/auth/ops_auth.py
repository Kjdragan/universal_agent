from __future__ import annotations

import hmac
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import jwt


@dataclass(frozen=True)
class OpsAuthValidationResult:
    ok: bool
    mode: str  # jwt | legacy | none
    subject: Optional[str]
    claims: Optional[dict[str, Any]]
    error: Optional[str] = None


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def allow_legacy_ops_auth() -> bool:
    return _env_flag("UA_OPS_AUTH_ALLOW_LEGACY", default=True)


def issue_ops_jwt(*, jwt_secret: str, subject: str, ttl_seconds: int = 3600) -> tuple[str, datetime]:
    now = int(time.time())
    expires_at = datetime.fromtimestamp(now + max(1, int(ttl_seconds)), timezone.utc)
    payload = {
        "iss": "universal-agent",
        "aud": "ua-ops",
        "sub": subject,
        "iat": now,
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, jwt_secret, algorithm="HS256")
    return token, expires_at


def validate_ops_token(
    token: str,
    *,
    jwt_secret: str,
    legacy_token: str,
    allow_legacy: bool,
) -> OpsAuthValidationResult:
    normalized = str(token or "").strip()
    if not normalized:
        return OpsAuthValidationResult(
            ok=False,
            mode="none",
            subject=None,
            claims=None,
            error="missing_token",
        )

    # Prefer JWT validation path when token has JWT shape.
    if normalized.count(".") == 2:
        if not jwt_secret:
            return OpsAuthValidationResult(
                ok=False,
                mode="jwt",
                subject=None,
                claims=None,
                error="jwt_secret_not_configured",
            )
        try:
            claims = jwt.decode(
                normalized,
                jwt_secret,
                algorithms=["HS256"],
                audience="ua-ops",
                issuer="universal-agent",
            )
            return OpsAuthValidationResult(
                ok=True,
                mode="jwt",
                subject=str(claims.get("sub") or ""),
                claims=claims,
            )
        except jwt.ExpiredSignatureError:
            return OpsAuthValidationResult(
                ok=False,
                mode="jwt",
                subject=None,
                claims=None,
                error="jwt_expired",
            )
        except jwt.InvalidTokenError:
            return OpsAuthValidationResult(
                ok=False,
                mode="jwt",
                subject=None,
                claims=None,
                error="jwt_invalid",
            )

    if allow_legacy and legacy_token:
        if hmac.compare_digest(normalized, legacy_token):
            return OpsAuthValidationResult(
                ok=True,
                mode="legacy",
                subject="legacy",
                claims=None,
            )

    return OpsAuthValidationResult(
        ok=False,
        mode="legacy" if allow_legacy else "none",
        subject=None,
        claims=None,
        error="unauthorized",
    )
