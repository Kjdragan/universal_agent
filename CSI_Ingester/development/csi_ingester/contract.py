"""Creator Signal Contract v1 models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreatorSignalEvent(BaseModel):
    """Universal creator signal event contract v1."""

    event_id: str = Field(..., description="Unique event identifier")
    dedupe_key: str = Field(..., description="Deduplication key")
    source: str = Field(..., description="Source adapter name")
    event_type: str = Field(..., description="Event type identifier")
    occurred_at: str = Field(..., description="ISO 8601 UTC when event happened")
    received_at: str = Field(..., description="ISO 8601 UTC when CSI ingested")
    emitted_at: str | None = Field(None, description="ISO 8601 UTC when sent to UA")
    subject: dict[str, Any] = Field(..., description="Platform-specific event payload")
    routing: dict[str, Any] = Field(..., description="Routing hints for UA")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Event metadata")
    raw_ref: str | None = Field(None, description="Reference to raw payload")
    contract_version: str = Field(default="1.0", description="Contract schema version")


class CSIIngestBatch(BaseModel):
    """Batch payload delivered from CSI to UA."""

    csi_version: str = Field(..., description="CSI service version")
    csi_instance_id: str = Field(..., description="CSI instance identifier")
    batch_id: str = Field(..., description="Batch identifier")
    events: list[CreatorSignalEvent] = Field(..., max_length=100)

