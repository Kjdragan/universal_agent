"""Pydantic schema for heartbeat findings JSON contract.

Matches the schema defined in HEARTBEAT.md and consumed by the gateway
heartbeat mediation pipeline.  All fields use permissive defaults so
partial / malformed agent output still validates rather than raising.

Used by:
- gateway_server._heartbeat_findings_from_artifacts  (read-side repair)
- heartbeat_service  (post-write validation & re-serialization)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator


class HeartbeatFinding(BaseModel):
    """A single finding entry inside a heartbeat findings artifact."""

    finding_id: str = "unknown"
    category: str = "unknown"
    severity: str = "warn"
    metric_key: str = ""
    observed_value: Any = None
    threshold_text: str = ""
    known_rule_match: bool = False
    confidence: str = "low"
    title: str = "Untitled Finding"
    recommendation: str = ""
    runbook_command: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("severity", mode="before")
    @classmethod
    def _normalize_severity(cls, v: Any) -> str:
        raw = str(v or "warn").strip().lower()
        mapping = {"warning": "warn", "error": "critical"}
        return mapping.get(raw, raw) if raw else "warn"

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, v: Any) -> str:
        raw = str(v or "low").strip().lower()
        return raw if raw in {"low", "medium", "high"} else "low"


class HeartbeatFindings(BaseModel):
    """Top-level heartbeat findings artifact."""

    version: int = 1
    overall_status: str = "warn"
    generated_at_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    source: str = "heartbeat"
    summary: str = "Heartbeat investigation required."
    findings: List[HeartbeatFinding] = Field(default_factory=list)

    @field_validator("overall_status", mode="before")
    @classmethod
    def _normalize_status(cls, v: Any) -> str:
        raw = str(v or "warn").strip().lower()
        mapping = {"warning": "warn", "error": "critical"}
        return mapping.get(raw, raw) if raw else "warn"
