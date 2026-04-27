"""
Unified error handling for Universal Agent FastAPI servers.

Provides:
- Custom exception hierarchy (AppError subclasses)
- Standardized error response schema (ErrorResponse)
- A single ``register_error_handlers(app)`` call that attaches exception
  handlers and a catch-all HTTP middleware to any FastAPI instance.

Usage in each server module::

    from universal_agent.api.error_handlers import register_error_handlers

    app = FastAPI(...)
    register_error_handlers(app)
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exception hierarchy
# ---------------------------------------------------------------------------

class AppError(Exception):
    """Base application error.

    Subclasses should set ``status_code`` and ``error_code`` as class-level
    attributes so that the unified handler can build a correct response
    without any per-endpoint boilerplate.
    """

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"
    detail: str = "An unexpected error occurred."

    def __init__(
        self,
        detail: Optional[str] = None,
        *,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if detail is not None:
            self.detail = detail
        if status_code is not None:
            self.status_code = status_code
        if error_code is not None:
            self.error_code = error_code
        self.extra = extra or {}
        super().__init__(self.detail)


class AuthenticationError(AppError):
    """401 -- the caller is not authenticated."""

    status_code = 401
    error_code = "AUTHENTICATION_ERROR"
    detail = "Authentication required."


class AuthorizationError(AppError):
    """403 -- the caller is authenticated but not authorised."""

    status_code = 403
    error_code = "AUTHORIZATION_ERROR"
    detail = "Permission denied."


class ValidationError(AppError):
    """422 -- request payload / parameters are invalid."""

    status_code = 422
    error_code = "VALIDATION_ERROR"
    detail = "Validation failed."


class NotFoundError(AppError):
    """404 -- the requested resource does not exist."""

    status_code = 404
    error_code = "NOT_FOUND_ERROR"
    detail = "Resource not found."


class ConflictError(AppError):
    """409 -- the request conflicts with the current state."""

    status_code = 409
    error_code = "CONFLICT_ERROR"
    detail = "Conflict with current state."


class ServiceUnavailableError(AppError):
    """503 -- a dependent service is not available."""

    status_code = 503
    error_code = "SERVICE_UNAVAILABLE"
    detail = "Service temporarily unavailable."


class GatewayTimeoutError(AppError):
    """504 -- an upstream gateway timed out."""

    status_code = 504
    error_code = "GATEWAY_TIMEOUT"
    detail = "Upstream gateway timed out."


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------

class ErrorDetail(BaseModel):
    code: str = Field(description="Machine-readable error code")
    message: str = Field(description="Human-readable error description")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Optional structured context")


class ErrorResponse(BaseModel):
    error: ErrorDetail
    request_id: Optional[str] = Field(default=None, description="Correlation ID, if available")
    timestamp: str = Field(description="ISO-8601 timestamp of the error")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _request_id(request: Request) -> Optional[str]:
    """Extract a request id from the request headers or state."""
    rid = getattr(request.state, "request_id", None)
    if rid:
        return str(rid)
    for header in ("x-request-id", "x-correlation-id"):
        value = request.headers.get(header)
        if value:
            return value
    return None


def _build_error_response(
    *,
    status_code: int,
    error_code: str,
    message: str,
    extra: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
) -> JSONResponse:
    """Build a standardised JSON error response."""
    payload = ErrorResponse(
        error=ErrorDetail(code=error_code, message=message, extra=extra or {}),
        request_id=request_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump())


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_error_handlers(app: FastAPI) -> None:
    """Attach unified exception handlers and catch-all middleware to *app*.

    This is designed to be called **once** per FastAPI instance, right after
    the app is created but before endpoints are added.  It will not interfere
    with existing middleware (e.g. auth middleware) because the catch-all is
    added last and only handles exceptions that propagate past all other
    middleware.
    """

    # --- Exception handlers ------------------------------------------------

    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:  # noqa: F811
        rid = _request_id(request) or f"app-{uuid.uuid4().hex[:12]}"
        if exc.status_code >= 500:
            logger.exception(
                "AppError [%s] %s request_id=%s",
                exc.error_code,
                exc.detail,
                rid,
            )
        else:
            logger.warning(
                "AppError [%s] %s request_id=%s",
                exc.error_code,
                exc.detail,
                rid,
            )
        return _build_error_response(
            status_code=exc.status_code,
            error_code=exc.error_code,
            message=exc.detail,
            extra=exc.extra if exc.extra else None,
            request_id=rid,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request, exc: RequestValidationError,
    ) -> JSONResponse:
        rid = _request_id(request) or f"val-{uuid.uuid4().hex[:12]}"
        logger.warning(
            "RequestValidationError request_id=%s errors=%s", rid, exc.errors(),
        )
        errors = exc.errors()
        first = errors[0] if errors else {}
        msg = first.get("msg", "Validation error")
        return _build_error_response(
            status_code=422,
            error_code="VALIDATION_ERROR",
            message=msg,
            extra={"fields": errors},
            request_id=rid,
        )

    # --- Catch-all HTTP middleware ------------------------------------------

    @app.middleware("http")
    async def _error_handling_middleware(request: Request, call_next):  # type: ignore[misc]
        try:
            return await call_next(request)
        except Exception as exc:
            if getattr(request, "scope", {}).get("type") != "http":
                raise
            if isinstance(exc, AppError):
                raise
            rid = _request_id(request) or f"err-{uuid.uuid4().hex[:12]}"
            logger.exception(
                "Unhandled exception request_id=%s path=%s",
                rid,
                request.url.path,
            )
            return _build_error_response(
                status_code=500,
                error_code="INTERNAL_ERROR",
                message="An unexpected error occurred.",
                request_id=rid,
            )
