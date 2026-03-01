"""CSI Follow-Up Contract v2 (Packet 17).

Defines the request/response schema for UA<->CSI refinement loops,
with correlation IDs, hard budget enforcement, and timeout policy.

Every follow-up request is tracked by a correlation_id that links
the original request to its outcome (completion, timeout, or budget_exhausted).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


# Hard limits
MAX_FOLLOWUP_BUDGET = 10
DEFAULT_FOLLOWUP_BUDGET = 3
DEFAULT_TIMEOUT_SECONDS = 3600  # 1 hour


@dataclass
class FollowUpRequest:
    """Schema for a UA->CSI follow-up request."""
    correlation_id: str = field(default_factory=lambda: f"fu_{uuid.uuid4().hex[:12]}")
    topic_key: str = ""
    request_type: str = "targeted_followup"  # targeted_followup | full_reanalysis | source_expansion
    reason: str = ""
    requested_by: str = "ua_gateway"
    budget_remaining: int = DEFAULT_FOLLOWUP_BUDGET
    budget_total: int = DEFAULT_FOLLOWUP_BUDGET
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    deadline_utc: str = ""
    quality_threshold: float = 0.6
    parent_correlation_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self):
        if not self.deadline_utc:
            deadline_ts = time.time() + self.timeout_seconds
            self.deadline_utc = datetime.fromtimestamp(deadline_ts, tz=timezone.utc).isoformat()
        self.budget_remaining = min(self.budget_remaining, MAX_FOLLOWUP_BUDGET)
        self.budget_total = min(self.budget_total, MAX_FOLLOWUP_BUDGET)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FollowUpResponse:
    """Schema for a CSI->UA follow-up response."""
    correlation_id: str = ""
    topic_key: str = ""
    outcome: str = "pending"  # completed | timeout | budget_exhausted | error | cancelled
    quality_score: Optional[float] = None
    quality_grade: Optional[str] = None
    artifact_paths: Optional[dict[str, str]] = None
    budget_consumed: int = 0
    budget_remaining: int = 0
    elapsed_seconds: float = 0.0
    error_detail: Optional[str] = None
    completed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_followup_request(data: dict[str, Any]) -> tuple[bool, str]:
    """Validate an incoming follow-up request dict. Returns (valid, error_message)."""
    if not isinstance(data, dict):
        return False, "request must be a dict"

    topic_key = str(data.get("topic_key") or "").strip()
    if not topic_key:
        return False, "topic_key is required"

    request_type = str(data.get("request_type") or "").strip()
    valid_types = {"targeted_followup", "full_reanalysis", "source_expansion"}
    if request_type and request_type not in valid_types:
        return False, f"request_type must be one of {valid_types}"

    budget = data.get("budget_remaining")
    if budget is not None:
        try:
            budget_int = int(budget)
            if budget_int < 0 or budget_int > MAX_FOLLOWUP_BUDGET:
                return False, f"budget_remaining must be 0-{MAX_FOLLOWUP_BUDGET}"
        except (ValueError, TypeError):
            return False, "budget_remaining must be an integer"

    timeout = data.get("timeout_seconds")
    if timeout is not None:
        try:
            timeout_int = int(timeout)
            if timeout_int < 60 or timeout_int > 86400:
                return False, "timeout_seconds must be 60-86400"
        except (ValueError, TypeError):
            return False, "timeout_seconds must be an integer"

    return True, ""


def build_followup_request(
    *,
    topic_key: str,
    reason: str,
    budget_remaining: int = DEFAULT_FOLLOWUP_BUDGET,
    budget_total: int = DEFAULT_FOLLOWUP_BUDGET,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    request_type: str = "targeted_followup",
    quality_threshold: float = 0.6,
    parent_correlation_id: Optional[str] = None,
) -> FollowUpRequest:
    """Build a validated follow-up request."""
    return FollowUpRequest(
        topic_key=topic_key,
        request_type=request_type,
        reason=reason,
        budget_remaining=min(int(budget_remaining), MAX_FOLLOWUP_BUDGET),
        budget_total=min(int(budget_total), MAX_FOLLOWUP_BUDGET),
        timeout_seconds=max(60, min(int(timeout_seconds), 86400)),
        quality_threshold=float(quality_threshold),
        parent_correlation_id=parent_correlation_id,
    )


def build_followup_response(
    *,
    correlation_id: str,
    topic_key: str,
    outcome: str,
    quality_score: Optional[float] = None,
    quality_grade: Optional[str] = None,
    artifact_paths: Optional[dict[str, str]] = None,
    budget_consumed: int = 0,
    budget_remaining: int = 0,
    elapsed_seconds: float = 0.0,
    error_detail: Optional[str] = None,
) -> FollowUpResponse:
    """Build a follow-up response."""
    return FollowUpResponse(
        correlation_id=correlation_id,
        topic_key=topic_key,
        outcome=outcome,
        quality_score=quality_score,
        quality_grade=quality_grade,
        artifact_paths=artifact_paths,
        budget_consumed=int(budget_consumed),
        budget_remaining=int(budget_remaining),
        elapsed_seconds=float(elapsed_seconds),
        error_detail=error_detail,
    )


def check_followup_timeout(request: FollowUpRequest) -> bool:
    """Check if a follow-up request has exceeded its deadline."""
    if not request.deadline_utc:
        return False
    try:
        deadline_dt = datetime.fromisoformat(request.deadline_utc.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) > deadline_dt
    except Exception:
        return False


def check_followup_budget(request: FollowUpRequest) -> bool:
    """Check if follow-up budget is exhausted. Returns True if exhausted."""
    return request.budget_remaining <= 0
