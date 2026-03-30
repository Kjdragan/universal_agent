"""Mission guardrails for structured goal-satisfaction checks."""

from __future__ import annotations

from dataclasses import dataclass
import json
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

    def __init__(self, contract: MissionContract, *, run_kind: str = "user"):
        self.contract = contract
        self.run_kind = str(run_kind or "user").strip().lower() or "user"
        self.tool_names: list[str] = []
        self._tool_flow: list[str] = []
        self.task_actions: list[str] = []
        self.email_send_count = 0
        self.gmail_send_count = 0
        self.successful_vp_dispatches: list[dict[str, Any]] = []

    def record_tool_call(self, tool_name: str, *, tool_input: Any = None) -> None:
        name = str(tool_name or "").strip()
        if not name:
            return
        self.tool_names.append(name)
        low = name.lower()
        self._tool_flow.append(low)
        if _is_email_send_tool(low):
            self.email_send_count += 1
        if _is_gmail_send_tool(low):
            self.gmail_send_count += 1
        self.task_actions.extend(_extract_task_hub_actions(low, tool_input))
        for nested_tool in _extract_nested_tool_names(tool_input):
            nested_lower = nested_tool.lower()
            self._tool_flow.append(nested_lower)
            if _is_email_send_tool(nested_lower):
                self.email_send_count += 1
            if _is_gmail_send_tool(nested_lower):
                self.gmail_send_count += 1

    def record_tool_result(
        self,
        tool_name: str,
        *,
        tool_input: Any = None,
        tool_result: Any = None,
        is_error: bool = False,
    ) -> None:
        low = str(tool_name or "").strip().lower()
        if not low or bool(is_error):
            return
        if "vp_dispatch_mission" not in low:
            return

        payload = _parse_tool_result_payload(tool_result)
        mission_id = str(payload.get("mission_id") or "").strip()
        if not mission_id or not bool(payload.get("ok", True)):
            return

        vp_id = str(payload.get("vp_id") or "").strip()
        objective = ""
        if isinstance(tool_input, dict):
            objective = str(tool_input.get("objective") or "").strip()
            if not vp_id:
                vp_id = str(tool_input.get("vp_id") or "").strip()

        self.successful_vp_dispatches.append(
            {
                "mission_id": mission_id,
                "vp_id": vp_id,
                "objective": objective,
            }
        )

    def evaluate(self) -> dict[str, Any]:
        if self.run_kind == "email_triage":
            return self._result(
                passed=True,
                stage_status="triaged",
                terminal=False,
                missing=[],
            )
        if self.run_kind.startswith("heartbeat"):
            return self._result(
                passed=True,
                stage_status="heartbeat",
                terminal=False,
                missing=[],
            )

        missing: list[dict[str, Any]] = []
        stage_status = "completed"
        terminal = True
        lifecycle_actions = {action for action in self.task_actions if action}
        if self.contract.email_required:
            observed = self.email_send_count
            required = self.contract.min_email_sends
            if observed < required:
                if self.run_kind == "todo_execution":
                    if "delegate" in lifecycle_actions:
                        stage_status = "delegated"
                        terminal = False
                    elif self.successful_vp_dispatches:
                        stage_status = "auto_delegate"
                        terminal = False
                    elif any(action in lifecycle_actions for action in ("review", "block", "park")):
                        stage_status = "awaiting_final_delivery"
                        terminal = False
                    elif any(action in lifecycle_actions for action in ("complete", "approve")):
                        missing.append(
                            {
                                "requirement": "email_send",
                                "required": required,
                                "observed": observed,
                                "message": "Final completion attempted without the required email delivery step.",
                            }
                        )
                    else:
                        missing.append(
                            {
                                "requirement": "lifecycle_mutation",
                                "required": 1,
                                "observed": 0,
                                "message": "ToDo execution ended without a durable Task Hub lifecycle mutation.",
                            }
                        )
                else:
                    missing.append(
                        {
                            "requirement": "email_send",
                            "required": required,
                            "observed": observed,
                            "message": "Required email delivery steps were not completed.",
                        }
                    )
        elif self.run_kind == "todo_execution":
            if "delegate" in lifecycle_actions:
                stage_status = "delegated"
                terminal = False
            elif self.successful_vp_dispatches:
                stage_status = "auto_delegate"
                terminal = False
            elif not any(action in lifecycle_actions for action in ("review", "complete", "block", "park", "approve")):
                missing.append(
                    {
                        "requirement": "lifecycle_mutation",
                        "required": 1,
                        "observed": 0,
                        "message": "ToDo execution ended without a durable Task Hub lifecycle mutation.",
                    }
                )

        passed = len(missing) == 0
        return self._result(
            passed=passed,
            stage_status=stage_status,
            terminal=terminal if passed else False,
            missing=missing,
        )

    def _result(
        self,
        *,
        passed: bool,
        stage_status: str,
        terminal: bool,
        missing: list[dict[str, Any]],
    ) -> dict[str, Any]:
        research_pipeline_adherence = _evaluate_research_pipeline_adherence(self._tool_flow)
        return {
            "passed": passed,
            "stage_status": stage_status,
            "terminal": terminal,
            "contract": self.contract.to_dict(),
            "observed": {
                "email_send_count": self.email_send_count,
                "gmail_send_count": self.gmail_send_count,
                "tool_calls_total": len(self.tool_names),
                "tool_names": self.tool_names[-100:],
                "task_actions": self.task_actions[-100:],
                "successful_vp_dispatches": self.successful_vp_dispatches[-50:],
                "research_pipeline_adherence": research_pipeline_adherence,
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


def _extract_nested_tool_names(tool_input: Any) -> list[str]:
    if not isinstance(tool_input, dict):
        return []
    nested = tool_input.get("tools")
    if not isinstance(nested, list):
        return []
    names: list[str] = []
    for item in nested:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("tool_slug") or "").strip()
        if slug:
            names.append(slug)
    return names


def _extract_task_hub_actions(tool_name_lower: str, tool_input: Any) -> list[str]:
    if "task_hub_task_action" not in tool_name_lower:
        return []
    if not isinstance(tool_input, dict):
        return []
    action = str(tool_input.get("action") or "").strip().lower()
    return [action] if action else []


def _parse_tool_result_payload(tool_result: Any) -> dict[str, Any]:
    if isinstance(tool_result, dict):
        if isinstance(tool_result.get("content"), list):
            for block in tool_result.get("content") or []:
                if not isinstance(block, dict):
                    continue
                text = str(block.get("text") or "").strip()
                parsed = _parse_tool_result_payload(text)
                if parsed:
                    return parsed
        return tool_result

    text = str(tool_result or "").strip()
    if not text:
        return {}
    if text.startswith("error:"):
        return {"ok": False, "error": text}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {}


def _evaluate_research_pipeline_adherence(tool_flow: list[str]) -> dict[str, Any]:
    search_index: int | None = None
    run_research_phase_index: int | None = None
    scouting_calls_before_phase = 0

    for idx, name in enumerate(tool_flow):
        normalized = str(name or "").lower()
        if search_index is None and _is_research_search_tool(normalized):
            search_index = idx
        if run_research_phase_index is None and normalized.endswith("run_research_phase"):
            run_research_phase_index = idx
            continue

        if search_index is not None and run_research_phase_index is None:
            if normalized.endswith("bash") or normalized.endswith("list_directory"):
                scouting_calls_before_phase += 1

    required = search_index is not None
    run_research_phase_called = run_research_phase_index is not None
    passed = (not required) or (run_research_phase_called and scouting_calls_before_phase == 0)
    return {
        "required": required,
        "passed": passed,
        "search_collection_detected": required,
        "run_research_phase_called": run_research_phase_called,
        "pre_phase_workspace_scouting_calls": scouting_calls_before_phase,
    }


def _is_research_search_tool(tool_name_lower: str) -> bool:
    return tool_name_lower.endswith("composio_search_news") or tool_name_lower.endswith(
        "composio_search_web"
    )
