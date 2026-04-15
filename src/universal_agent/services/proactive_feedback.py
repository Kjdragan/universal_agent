"""Feedback parsing and inbound reply handling for proactive artifacts."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional

from universal_agent.services import proactive_artifacts
from universal_agent.services.proactive_preferences import record_artifact_feedback_signal

_FEEDBACK_RE = re.compile(r"^\s*([1-5])(?:\s*[-:.)]\s*|\s+)?(.*)$", re.DOTALL)


@dataclass(frozen=True)
class ParsedFeedback:
    score: Optional[int]
    text: str
    raw_reply: str


def parse_feedback_text(reply_text: str) -> ParsedFeedback:
    raw = str(reply_text or "").strip()
    if not raw:
        return ParsedFeedback(score=None, text="", raw_reply="")
    match = _FEEDBACK_RE.match(raw)
    if not match:
        return ParsedFeedback(score=None, text=raw, raw_reply=raw)
    score = int(match.group(1))
    text = str(match.group(2) or "").strip()
    return ParsedFeedback(score=score, text=text, raw_reply=raw)


def handle_proactive_feedback_reply(
    conn: sqlite3.Connection,
    *,
    subject: str,
    reply_text: str,
    thread_id: str = "",
    message_id: str = "",
    actor: str = "kevin",
) -> Optional[dict[str, Any]]:
    """Handle a reply to a proactive review email.

    Returns a result dict when the email was consumed as proactive feedback.
    Returns None when the email should continue through normal EmailTaskBridge
    materialization.
    """
    artifact = proactive_artifacts.find_artifact_for_reply(
        conn,
        subject=subject,
        thread_id=thread_id,
        message_id=message_id,
    )
    if artifact is None:
        return None

    parsed = parse_feedback_text(reply_text)
    updated = proactive_artifacts.record_feedback(
        conn,
        artifact_id=str(artifact["artifact_id"]),
        score=parsed.score,
        text=parsed.text,
        raw_reply=parsed.raw_reply,
        actor=actor,
        thread_id=thread_id,
        message_id=message_id,
    )
    record_artifact_feedback_signal(
        conn,
        artifact=updated,
        score=parsed.score,
        text=parsed.text,
    )
    return {
        "handled_as": "proactive_feedback",
        "artifact_id": updated["artifact_id"],
        "score": parsed.score,
        "text": parsed.text,
        "status": updated["status"],
        "delivery_state": updated["delivery_state"],
    }
