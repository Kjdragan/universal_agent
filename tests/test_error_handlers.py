"""Tests for unified error handling middleware."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict

import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel

from universal_agent.api.error_handlers import (
    AppError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    ErrorDetail,
    ErrorResponse,
    GatewayTimeoutError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
    register_error_handlers,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app() -> FastAPI:
    """Create a minimal FastAPI app with error handlers registered."""
    app = FastAPI()
    register_error_handlers(app)
    return app


def _extract_error_body(response: Any) -> Dict[str, Any]:
    return response.json()


# ---------------------------------------------------------------------------
# Exception class attribute tests
# ---------------------------------------------------------------------------

class TestExceptionAttributes:
    """Verify that each custom exception carries the correct defaults."""

    @pytest.mark.parametrize(
        "cls, expected_code, expected_status",
        [
            (AppError, "INTERNAL_ERROR", 500),
            (AuthenticationError, "AUTHENTICATION_ERROR", 401),
            (AuthorizationError, "AUTHORIZATION_ERROR", 403),
            (ValidationError, "VALIDATION_ERROR", 422),
            (NotFoundError, "NOT_FOUND_ERROR", 404),
            (ConflictError, "CONFLICT_ERROR", 409),
            (ServiceUnavailableError, "SERVICE_UNAVAILABLE", 503),
            (GatewayTimeoutError, "GATEWAY_TIMEOUT", 504),
        ],
    )
    def test_default_attributes(self, cls, expected_code, expected_status):
        exc = cls()
        assert exc.error_code == expected_code
        assert exc.status_code == expected_status
        assert isinstance(exc.detail, str)
        assert len(exc.detail) > 0

    def test_custom_detail_override(self):
        exc = NotFoundError(detail="Session xyz not found")
        assert exc.detail == "Session xyz not found"
        assert exc.status_code == 404

    def test_custom_status_and_code_override(self):
        exc = AppError("custom", status_code=418, error_code="TEAPOT")
        assert exc.status_code == 418
        assert exc.error_code == "TEAPOT"
        assert exc.detail == "custom"

    def test_extra_context(self):
        exc = ValidationError(extra={"field": "email", "reason": "bad format"})
        assert exc.extra == {"field": "email", "reason": "bad format"}


# ---------------------------------------------------------------------------
# Response schema tests
# ---------------------------------------------------------------------------

class TestResponseSchema:
    def test_error_detail_model(self):
        detail = ErrorDetail(code="TEST", message="test msg", extra={"k": "v"})
        d = detail.model_dump()
        assert d["code"] == "TEST"
        assert d["message"] == "test msg"
        assert d["extra"] == {"k": "v"}

    def test_error_detail_defaults(self):
        detail = ErrorDetail(code="X", message="y")
        assert detail.extra == {}

    def test_error_response_model(self):
        resp = ErrorResponse(
            error=ErrorDetail(code="E", message="m"),
            request_id="abc",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        d = resp.model_dump()
        assert d["error"]["code"] == "E"
        assert d["request_id"] == "abc"
        assert d["timestamp"] == "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Integration tests via TestClient
# ---------------------------------------------------------------------------

class TestAppErrorIntegration:
    """Verify that raising AppError subclasses in endpoints produces the
    standardised error response."""

    def _client(self, app: FastAPI) -> TestClient:
        return TestClient(app, raise_server_exceptions=False)

    def test_authentication_error_returns_401(self):
        app = _make_app()

        @app.get("/test")
        def _ep():
            raise AuthenticationError()

        resp = self._client(app).get("/test")
        assert resp.status_code == 401
        body = _extract_error_body(resp)
        assert body["error"]["code"] == "AUTHENTICATION_ERROR"
        assert "request_id" in body
        assert "timestamp" in body

    def test_authorization_error_returns_403(self):
        app = _make_app()

        @app.get("/test")
        def _ep():
            raise AuthorizationError(detail="You cannot access this resource")

        resp = self._client(app).get("/test")
        assert resp.status_code == 403
        body = _extract_error_body(resp)
        assert body["error"]["code"] == "AUTHORIZATION_ERROR"
        assert "cannot access" in body["error"]["message"].lower()

    def test_validation_error_returns_422(self):
        app = _make_app()

        @app.get("/test")
        def _ep():
            raise ValidationError(detail="Field 'name' is required")

        resp = self._client(app).get("/test")
        assert resp.status_code == 422
        body = _extract_error_body(resp)
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert "name" in body["error"]["message"]

    def test_not_found_error_returns_404(self):
        app = _make_app()

        @app.get("/test")
        def _ep():
            raise NotFoundError(detail="Widget not found")

        resp = self._client(app).get("/test")
        assert resp.status_code == 404
        body = _extract_error_body(resp)
        assert body["error"]["code"] == "NOT_FOUND_ERROR"

    def test_conflict_error_returns_409(self):
        app = _make_app()

        @app.get("/test")
        def _ep():
            raise ConflictError()

        resp = self._client(app).get("/test")
        assert resp.status_code == 409
        body = _extract_error_body(resp)
        assert body["error"]["code"] == "CONFLICT_ERROR"

    def test_service_unavailable_returns_503(self):
        app = _make_app()

        @app.get("/test")
        def _ep():
            raise ServiceUnavailableError(detail="Redis is down")

        resp = self._client(app).get("/test")
        assert resp.status_code == 503
        body = _extract_error_body(resp)
        assert body["error"]["code"] == "SERVICE_UNAVAILABLE"

    def test_gateway_timeout_returns_504(self):
        app = _make_app()

        @app.get("/test")
        def _ep():
            raise GatewayTimeoutError()

        resp = self._client(app).get("/test")
        assert resp.status_code == 504
        body = _extract_error_body(resp)
        assert body["error"]["code"] == "GATEWAY_TIMEOUT"

    def test_extra_context_included(self):
        app = _make_app()

        @app.get("/test")
        def _ep():
            raise NotFoundError(
                detail="Session not found",
                extra={"session_id": "s-123", "owner": "alice"},
            )

        resp = self._client(app).get("/test")
        body = _extract_error_body(resp)
        assert body["error"]["extra"]["session_id"] == "s-123"
        assert body["error"]["extra"]["owner"] == "alice"


class TestUnhandledExceptionIntegration:
    """Verify that an unhandled exception in an endpoint is caught by the
    middleware and returned as a 500 with a safe message (no stack traces)."""

    def _client(self, app: FastAPI) -> TestClient:
        return TestClient(app, raise_server_exceptions=False)

    def test_unhandled_exception_returns_500(self):
        app = _make_app()

        @app.get("/test")
        def _ep():
            raise RuntimeError("database connection lost")

        resp = self._client(app).get("/test")
        assert resp.status_code == 500
        body = _extract_error_body(resp)
        assert body["error"]["code"] == "INTERNAL_ERROR"
        assert "unexpected error" in body["error"]["message"].lower()
        # Must NOT contain the raw exception message
        assert "database connection lost" not in body["error"]["message"]
        assert "request_id" in body

    def test_unhandled_value_error_returns_500(self):
        app = _make_app()

        @app.get("/test")
        def _ep():
            raise ValueError("bad value")

        resp = self._client(app).get("/test")
        assert resp.status_code == 500
        body = _extract_error_body(resp)
        assert body["error"]["code"] == "INTERNAL_ERROR"
        assert "bad value" not in body["error"]["message"]

    def test_timestamp_is_iso_format(self):
        app = _make_app()

        @app.get("/test")
        def _ep():
            raise NotFoundError()

        resp = self._client(app).get("/test")
        body = _extract_error_body(resp)
        # Should parse as ISO-8601
        datetime.fromisoformat(body["timestamp"])

    def test_request_id_propagated_from_header(self):
        app = _make_app()

        @app.get("/test")
        def _ep():
            raise NotFoundError()

        resp = self._client(app).get(
            "/test", headers={"X-Request-Id": "my-custom-id"},
        )
        body = _extract_error_body(resp)
        assert body["request_id"] == "my-custom-id"


class TestRequestValidationErrorIntegration:
    """Verify that FastAPI's built-in RequestValidationError is handled."""

    def _client(self, app: FastAPI) -> TestClient:
        return TestClient(app, raise_server_exceptions=False)

    def test_query_param_validation_error(self):
        app = _make_app()

        @app.get("/test")
        def _ep(val: int):
            return {"val": val}

        resp = self._client(app).get("/test?val=not_a_number")
        assert resp.status_code == 422
        body = _extract_error_body(resp)
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert "fields" in body["error"]["extra"]

    def test_request_body_validation_error(self):
        app = _make_app()

        class Input(BaseModel):
            name: str
            age: int

        @app.post("/test")
        def _ep(body: Input):
            return body

        resp = self._client(app).post("/test", json={"name": "Alice"})
        assert resp.status_code == 422
        body = _extract_error_body(resp)
        assert body["error"]["code"] == "VALIDATION_ERROR"
        # The extra.fields should contain the validation failure details
        assert isinstance(body["error"]["extra"]["fields"], list)
        assert len(body["error"]["extra"]["fields"]) > 0


class TestErrorResponseConsistency:
    """Verify that ALL error responses share the same top-level keys."""

    ALL_ERRORS = [
        (AuthenticationError(), 401),
        (AuthorizationError(), 403),
        (ValidationError(), 422),
        (NotFoundError(), 404),
        (ConflictError(), 409),
        (ServiceUnavailableError(), 503),
        (GatewayTimeoutError(), 504),
    ]

    @pytest.mark.parametrize("exc, expected_status", ALL_ERRORS)
    def test_response_shape(self, exc, expected_status):
        app = _make_app()

        @app.get("/test")
        def _ep():
            raise exc

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test")
        assert resp.status_code == expected_status
        body = _extract_error_body(resp)

        # Top-level keys
        assert set(body.keys()) == {"error", "request_id", "timestamp"}

        # error sub-object keys
        assert set(body["error"].keys()) == {"code", "message", "extra"}

        # request_id should be a non-empty string
        assert isinstance(body["request_id"], str)
        assert len(body["request_id"]) > 0

        # timestamp should be parseable
        datetime.fromisoformat(body["timestamp"])

    def test_unhandled_500_shape(self):
        app = _make_app()

        @app.get("/test")
        def _ep():
            raise RuntimeError("boom")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test")
        assert resp.status_code == 500
        body = _extract_error_body(resp)
        assert set(body.keys()) == {"error", "request_id", "timestamp"}
        assert set(body["error"].keys()) == {"code", "message", "extra"}
