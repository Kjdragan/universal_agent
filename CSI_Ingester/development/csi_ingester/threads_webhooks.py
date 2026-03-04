"""Threads webhook contract and verification scaffolding."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from pydantic import BaseModel, Field

from csi_ingester.adapters.threads_api import verify_threads_signature


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
