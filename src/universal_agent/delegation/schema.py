from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class MissionPayload(BaseModel):
    """Task description and execution context for a delegated mission."""

    task: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)


class MissionEnvelope(BaseModel):
    """Wire format for a mission travelling over the Redis delegation bus.

    Carries routing metadata (priority, timeout, retries) alongside the
    :class:`MissionPayload`.
    """

    job_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    priority: int = Field(default=1)
    timeout_seconds: int = Field(default=3600, ge=1)
    max_retries: int = Field(default=3, ge=0)
    payload: MissionPayload


class MissionResultEnvelope(BaseModel):
    """Wire format for the outcome of a completed mission."""

    job_id: str = Field(min_length=1)
    status: Literal["SUCCESS", "FAILED"]
    result: Optional[Any] = None
    error: Optional[str] = None
