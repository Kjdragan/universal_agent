"""Deterministic task priority classification.

Pure-Python, no LLM calls.  Assigns one of four priority tiers to
an inbound email or generic task based on observable signals.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Optional

# ── Priority Tiers ───────────────────────────────────────────────────────────

class TaskPriority(str, Enum):
    """Priority tiers in descending urgency."""

    P0_IMMEDIATE = "p0"   # Operator email, direct instruction — dispatch NOW
    P1_SOON = "p1"        # agent-ready + due today, follow-up thread — next idle slot
    P2_SCHEDULED = "p2"   # Scheduled for later, calendar-driven
    P3_BACKGROUND = "p3"  # Maintenance, brainstorm, scan


# ── Dispatch Strategy ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DispatchDecision:
    """What to do with a classified task."""

    priority: TaskPriority
    strategy: str          # "immediate" | "idle_slot" | "scheduled" | "heartbeat"
    max_wait_seconds: int  # Upper bound before escalating
    reason: str            # Human-readable explanation


# ── Priority → Dispatch mapping ──────────────────────────────────────────────

_STRATEGY_MAP: dict[TaskPriority, tuple[str, int]] = {
    TaskPriority.P0_IMMEDIATE: ("immediate", 60),
    TaskPriority.P1_SOON:      ("idle_slot", 300),
    TaskPriority.P2_SCHEDULED: ("scheduled", 1800),
    TaskPriority.P3_BACKGROUND: ("heartbeat", 1800),
}


# ── Urgency Keywords ─────────────────────────────────────────────────────────

_URGENCY_KEYWORDS = re.compile(
    r"\b(urgent|asap|immediately|right now|critical|emergency|time.?sensitive)\b",
    re.IGNORECASE,
)

_DEFERRAL_KEYWORDS = re.compile(
    r"\b(when you get a chance|no rush|low priority|whenever|not urgent|later)\b",
    re.IGNORECASE,
)


# ── Email Classifier ─────────────────────────────────────────────────────────

def classify_email_priority(
    *,
    sender_trusted: bool,
    is_reply: bool,
    thread_message_count: int = 1,
    subject: str = "",
    body_snippet: str = "",
    classification: str = "",
) -> DispatchDecision:
    """Classify an inbound email into a priority tier.

    All logic is deterministic — no LLM calls.

    Parameters
    ----------
    sender_trusted : bool
        True if the sender is Kevin (verified by transport layer).
    is_reply : bool
        True if this is a reply to an existing thread.
    thread_message_count : int
        Number of messages in the thread (including this one).
    subject : str
        Email subject line.
    body_snippet : str
        First ~200 chars of the email body (for keyword scanning).
    classification : str
        The email-handler triage classification
        (instruction, feedback_approval, feedback_correction, status_update, etc.).
    """
    text = f"{subject} {body_snippet}"

    # ── Untrusted sender → urgent user review ──────────────────────────────
    # This is NOT a low-priority background item.  External/unsolicited
    # emails require active human triage — the operator must decide
    # whether to engage, ignore, or block.
    if not sender_trusted:
        return _decision(TaskPriority.P1_SOON, "untrusted_sender_security_review")

    # ── Explicit deferral keywords override everything ───────────────────
    if _DEFERRAL_KEYWORDS.search(text):
        return _decision(TaskPriority.P2_SCHEDULED, "deferral_keyword_detected")

    # ── Explicit urgency keywords → p0 ───────────────────────────────────
    if _URGENCY_KEYWORDS.search(text):
        return _decision(TaskPriority.P0_IMMEDIATE, "urgency_keyword_detected")

    # ── Kevin replying to active conversation → immediate ────────────────
    if is_reply and thread_message_count > 1:
        return _decision(TaskPriority.P0_IMMEDIATE, "operator_reply_to_active_thread")

    # ── New direct instruction from Kevin → immediate ────────────────────
    if classification == "instruction":
        return _decision(TaskPriority.P0_IMMEDIATE, "operator_instruction")

    # ── Feedback (approval or correction) → soon ─────────────────────────
    if classification in ("feedback_approval", "feedback_correction"):
        return _decision(TaskPriority.P1_SOON, f"operator_{classification}")

    # ── Status update → can wait ─────────────────────────────────────────
    if classification == "status_update":
        return _decision(TaskPriority.P2_SCHEDULED, "operator_status_update")

    # ── Default for trusted sender → soon ────────────────────────────────
    return _decision(TaskPriority.P1_SOON, "operator_default")


# ── Generic Task Classifier ──────────────────────────────────────────────────

def classify_task_priority(
    *,
    source_kind: str = "",
    labels: Optional[list[str]] = None,
    is_due_today: bool = False,
    has_due_date: bool = False,
    must_complete: bool = False,
) -> DispatchDecision:
    """Classify a generic Task Hub task into a priority tier."""
    _labels = set(labels or [])

    if must_complete or "must-complete" in _labels:
        return _decision(TaskPriority.P0_IMMEDIATE, "must_complete_flag")

    if source_kind == "email" or "email-task" in _labels:
        return _decision(TaskPriority.P1_SOON, "email_derived_task")

    if is_due_today:
        return _decision(TaskPriority.P1_SOON, "due_today")

    if has_due_date:
        return _decision(TaskPriority.P2_SCHEDULED, "has_due_date")

    if "brainstorm" in _labels or "heartbeat-candidate" in _labels:
        return _decision(TaskPriority.P3_BACKGROUND, "brainstorm_or_candidate")

    return _decision(TaskPriority.P2_SCHEDULED, "default")


# ── Helper ────────────────────────────────────────────────────────────────────

def _decision(priority: TaskPriority, reason: str) -> DispatchDecision:
    strategy, max_wait = _STRATEGY_MAP[priority]
    return DispatchDecision(
        priority=priority,
        strategy=strategy,
        max_wait_seconds=max_wait,
        reason=reason,
    )
