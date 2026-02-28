from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class MissionPayload(BaseModel):
    task: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)


class MissionEnvelope(BaseModel):
    job_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    priority: int = Field(default=1)
    timeout_seconds: int = Field(default=3600, ge=1)
    max_retries: int = Field(default=3, ge=0)
    payload: MissionPayload


class MissionResultEnvelope(BaseModel):
    job_id: str = Field(min_length=1)
    status: Literal["SUCCESS", "FAILED"]
    result: Optional[Any] = None
    error: Optional[str] = None
