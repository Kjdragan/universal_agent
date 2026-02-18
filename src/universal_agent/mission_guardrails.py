"""Mission guardrails for structured goal-satisfaction checks."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


_INTERIM_PATTERNS = (
    re.compile(r"\binterim\b", re.IGNORECASE),
    re.compile(r"\bprogress\s+update", re.IGNORECASE),
    re.compile(r"\beach\s+work\s+product\b", re.IGNORECASE),
    re.compile(r"\bone\s+per\s+gmail\b", re.IGNORECASE),
)
_FINAL_PATTERNS = (
    re.compile(r"\bfinal\s+work\s+product\b", re.IGNORECASE),
    re.compile(r"\bfinal\s+report\b", re.IGNORECASE),
    re.compile(r"\bgmail\s+me\s+the\s+final\b", re.IGNORECASE),
)
_EMAIL_REQUIRED_PATTERN = re.compile(r"\b(gmail|e-?mail)\b", re.IGNORECASE)
_EMAIL_NEGATION_PATTERN = re.compile(r"\b(do\s*not|don['’]t|no)\s+(gmail|e-?mail)\b", re.IGNORECASE)


@dataclass(frozen=True)
class MissionContract:
    """Requirements inferred from user prompt."""

    email_required: bool
    min_email_sends: int
    interim_required: bool
    final_required: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "email_required": self.email_required,
            "min_email_sends": self.min_email_sends,
            "interim_required": self.interim_required,
            "final_required": self.final_required,
        }


class MissionGuardrailTracker:
    """Tracks tool calls and evaluates whether inferred requirements were met."""

    def __init__(self, contract: MissionContract):
        self.contract = contract
        self.tool_names: list[str] = []
        self.email_send_count = 0
        self.gmail_send_count = 0

    def record_tool_call(self, tool_name: str) -> None:
        name = str(tool_name or "").strip()
        if not name:
            return
        self.tool_names.append(name)
        low = name.lower()
        if _is_email_send_tool(low):
            self.email_send_count += 1
        if _is_gmail_send_tool(low):
            self.gmail_send_count += 1

    def evaluate(self) -> dict[str, Any]:
        missing: list[dict[str, Any]] = []
        if self.contract.email_required:
            observed = self.email_send_count
            required = self.contract.min_email_sends
            if observed < required:
                missing.append(
                    {
                        "requirement": "email_send",
                        "required": required,
                        "observed": observed,
                        "message": "Required email delivery steps were not completed.",
                    }
                )

        passed = len(missing) == 0
        return {
            "passed": passed,
            "contract": self.contract.to_dict(),
            "observed": {
                "email_send_count": self.email_send_count,
                "gmail_send_count": self.gmail_send_count,
                "tool_calls_total": len(self.tool_names),
                "tool_names": self.tool_names[-100:],
            },
            "missing": missing,
        }


def build_mission_contract(user_input: str) -> MissionContract:
    text = str(user_input or "")
    text_l = text.lower()

    email_required = bool(_EMAIL_REQUIRED_PATTERN.search(text)) and not bool(
        _EMAIL_NEGATION_PATTERN.search(text)
    )
    interim_required = any(pattern.search(text) for pattern in _INTERIM_PATTERNS)
    final_required = any(pattern.search(text) for pattern in _FINAL_PATTERNS)

    min_email_sends = 0
    if email_required:
        min_email_sends = 1
        if interim_required and final_required:
            min_email_sends = 2

    # If user explicitly asks for interim work but does not say "final", infer final
    # when they also ask for a report/output artifact.
    if email_required and interim_required and not final_required:
        if "report" in text_l or "final" in text_l:
            final_required = True
            min_email_sends = max(min_email_sends, 2)

    return MissionContract(
        email_required=email_required,
        min_email_sends=min_email_sends,
        interim_required=interim_required,
        final_required=final_required,
    )


def _is_email_send_tool(tool_name_lower: str) -> bool:
    if not tool_name_lower:
        return False
    emailish = any(token in tool_name_lower for token in ("gmail", "email", "mail"))
    if not emailish:
        return False
    return any(token in tool_name_lower for token in ("send", "reply", "draft", "compose"))


def _is_gmail_send_tool(tool_name_lower: str) -> bool:
    if "gmail" not in tool_name_lower:
        return False
    return any(token in tool_name_lower for token in ("send", "reply", "draft", "compose"))
