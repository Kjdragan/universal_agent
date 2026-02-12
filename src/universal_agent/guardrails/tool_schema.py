"""Tool schema guardrails for core tools.

This module provides a curated schema map for known tools and hook helpers
that block malformed tool calls while injecting an inline schema/example hint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import os
import re
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence
from zoneinfo import ZoneInfo

from universal_agent.durable.tool_gateway import parse_tool_identity


@dataclass(frozen=True)
class ToolSchema:
    required: Sequence[str] = field(default_factory=tuple)
    required_any: Sequence[Sequence[str]] = field(default_factory=tuple)
    example: str = ""
    # Minimum content length for string fields (0 = no validation)
    content_min_length: dict[str, int] = field(default_factory=dict)


_TOOL_SCHEMAS: dict[str, ToolSchema] = {
    # Local toolkit tools
    "write_local_file": ToolSchema(
        required=("path", "content"),
        example=(
            "write_local_file({path: '/tmp/report.html', content: '<html>...</html>'})"
        ),
        # Require at least 10 bytes to prevent 0-byte/empty writes
        content_min_length={"content": 10},
    ),
    "upload_to_composio": ToolSchema(
        required=("path", "tool_slug", "toolkit_slug"),
        example=(
            "upload_to_composio({path: '/tmp/report.pdf', tool_slug: 'GMAIL_SEND_EMAIL', "
            "toolkit_slug: 'gmail'})"
        ),
    ),
    "read_local_file": ToolSchema(
        required=("path",),
        example="read_local_file({path: '/tmp/report.html'})",
    ),
    "list_directory": ToolSchema(
        required=("path",),
        example="list_directory({path: '/tmp'})",
    ),
    "inspect_session_workspace": ToolSchema(
        example=(
            "inspect_session_workspace({session_id: 'session_20260211_231748_2b12a9df', "
            "include_transcript: true, tail_lines: 120})"
        ),
    ),
    "append_to_file": ToolSchema(
        required=("path", "content"),
        example="append_to_file({path: '/tmp/report.html', content: '<html>...</html>'})",
        content_min_length={"content": 10},
    ),
    "write_text_file": ToolSchema(
        required=("path", "content"),
        example=(
            "write_text_file({path: '/abs/path/inside/session/or/artifacts.txt', content: '...', overwrite: true})"
        ),
        # Allow small files (e.g. manifest.json) but prevent empty writes from schema mistakes.
        content_min_length={"content": 1},
    ),
    "finalize_research": ToolSchema(
        required=("session_dir",),
        example="finalize_research({session_dir: '/tmp/session_20260101_120000', task_name: 'ai_news'})",
    ),
    "crawl_parallel": ToolSchema(
        required=("urls", "session_dir"),
        example="crawl_parallel({urls: ['https://example.com'], session_dir: '/tmp/session_20260101_120000'})",
    ),
    "ask_user_questions": ToolSchema(
        required=("questions",),
        example=(
            "ask_user_questions({questions: [{question: 'Timeframe?', header: 'Time', options: [{label: '7 days', description: 'Recent'}], multiSelect: false}]})"
        ),
    ),
    # Curated Composio tools (outer tool validation only)
    "composio_multi_execute_tool": ToolSchema(
        required=("tools",),
        example=(
            "COMPOSIO_MULTI_EXECUTE_TOOL({session_id: '...', tools: [{tool_slug: 'GMAIL_SEND_EMAIL', arguments: {...}}]})"
        ),
    ),
    "composio_search_tools": ToolSchema(
        required=("queries",),
        example=(
            "COMPOSIO_SEARCH_TOOLS({queries: [{use_case: 'search AI news', known_fields: 'timeframe: last 30 days'}], "
            "session: {generate_id: true}})"
        ),
    ),
    "composio_search_news": ToolSchema(
        required=("query",),
        example="COMPOSIO_SEARCH_NEWS({query: 'Venezuela sanctions', when: 'w'})",
    ),
    "composio_search_web": ToolSchema(
        required=("query",),
        example="COMPOSIO_SEARCH_WEB({query: 'US Venezuela military action 2025'})",
    ),
    "gmail_send_email": ToolSchema(
        required=("subject", "body"),
        required_any=(("recipient_email",), ("to",)),
        example=(
            "GMAIL_SEND_EMAIL({recipient_email: 'user@example.com', subject: 'Report', body: '...', attachment: {...}})"
        ),
    ),
    # Native Claude SDK tools
    "write": ToolSchema(
        required=("file_path", "content"),
        example="Write(file_path='/path/to/file.html', content='<html>...</html>')",
        content_min_length={"content": 10},
    ),
    # Planning tools
    "todowrite": ToolSchema(
        required=("todos",),
        example="TodoWrite(todos=[{content: 'Research X', status: 'pending'}])",
    ),
}

_NUMBER_WORDS_TO_INT = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "twenty-one": 21,
    "twenty-two": 22,
    "twenty-three": 23,
    "twenty-four": 24,
    "twenty-five": 25,
    "twenty-six": 26,
    "twenty-seven": 27,
    "twenty-eight": 28,
    "twenty-nine": 29,
    "thirty": 30,
}


def _match_schema(tool_name: str) -> Optional[ToolSchema]:
    if not tool_name:
        return None
    tool_name = tool_name.lower()
    
    # 1. Try exact match first (Highest priority)
    if tool_name in _TOOL_SCHEMAS:
        return _TOOL_SCHEMAS[tool_name]
        
    # 2. Try suffix match, but be careful with commonly shared suffixes
    # We sort by length descending to match longer keys first (e.g. 'write_local_file' before 'write')
    sorted_keys = sorted(_TOOL_SCHEMAS.keys(), key=len, reverse=True)
    
    for key in sorted_keys:
        if tool_name == key:
            return _TOOL_SCHEMAS[key]
        if tool_name.endswith(key):
            # Special case: Don't match 'todowrite' with 'write'
             # If we matched 'write', check if the full name indicates a different known tool suffix
            if key == "write" and "todowrite" in tool_name:
                continue
            return _TOOL_SCHEMAS[key]
            
    return None


def _missing_required(schema: ToolSchema, tool_input: dict) -> list[str]:
    missing: list[str] = []
    for field in schema.required:
        if not tool_input.get(field):
            missing.append(field)
    if schema.required_any:
        satisfied = False
        for group in schema.required_any:
            if all(tool_input.get(field) for field in group):
                satisfied = True
                break
        if not satisfied:
            missing.append("one_of:" + "|".join(",".join(group) for group in schema.required_any))
    
    # Validate content_min_length for string fields
    for field_name, min_len in schema.content_min_length.items():
        value = tool_input.get(field_name)
        if isinstance(value, str) and len(value) < min_len:
            missing.append(f"{field_name}_too_short(min:{min_len})")
        elif value is None or (isinstance(value, str) and len(value) == 0):
            # Already caught by required check, but re-emphasize for 0-byte case
            if field_name not in missing:
                missing.append(f"{field_name}_empty")
    
    return missing


def validate_tool_input(tool_name: str, tool_input: object) -> tuple[bool, list[str], Optional[ToolSchema]]:
    schema = _match_schema(tool_name)
    if not schema:
        return True, [], None
    if not isinstance(tool_input, dict):
        missing = list(schema.required) or ["tools"]
        return False, missing, schema
    missing = _missing_required(schema, tool_input)
    return (not missing), missing, schema


def _try_parse_json_list(raw_value: object) -> Optional[list]:
    if not isinstance(raw_value, str):
        return None
    stripped = raw_value.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except Exception:
        return None
    return parsed if isinstance(parsed, list) else None


def _try_parse_json_object(raw_value: object) -> Optional[dict]:
    if not isinstance(raw_value, str):
        return None
    stripped = raw_value.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _to_snake_case(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value or "")
    cleaned = re.sub(r"_+", "_", cleaned).strip("_").lower()
    return cleaned or "default"


def _rolling_window_label(days: int) -> str:
    clamped_days = max(1, min(days, 30))
    tz_name = os.getenv("USER_TIMEZONE", "America/Chicago")
    try:
        now = datetime.now(ZoneInfo(tz_name))
    except Exception:
        now = datetime.now()
    end_date = now.date()
    start_date = end_date - timedelta(days=clamped_days - 1)
    start_str = start_date.strftime("%B %d, %Y").replace(" 0", " ")
    end_str = end_date.strftime("%B %d, %Y").replace(" 0", " ")
    return f"Canonical rolling window (inclusive): {start_str} to {end_str}."


def _is_blocked_crontab_mutation(command: str) -> bool:
    text = (command or "").strip().lower()
    if not text:
        return False
    if "crontab" not in text and "/etc/cron" not in text and "cron.d" not in text:
        return False
    # Block mutation/edit flows.
    if re.search(r"\bcrontab\s+-(e|r)\b", text):
        return True
    if re.search(r"\bcrontab\s+-(?!l\b)", text):
        return True
    if re.search(r"\|\s*crontab\b", text):
        return True
    if re.search(r"\bcrontab\s+/", text):
        return True
    if re.search(r"(>>|>|tee)\s*/etc/cron", text):
        return True
    if "/etc/cron" in text or "cron.d" in text:
        return True
    # Allow read-only inspection.
    if re.search(r"\bcrontab\s+-l\b", text):
        return False
    if re.search(r"\bcat\s+/etc/cron", text):
        return False
    return False


def _looks_like_system_configuration_intent(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    target_patterns = [
        r"\bchron\b",
        r"\bcron\b",
        r"\bheartbeat\b",
        r"\bops\s+config\b",
        r"\bruntime\s+config\b",
        r"\bservice\s+(settings|config|configuration)\b",
    ]
    action_patterns = [
        r"\bcreate\b",
        r"\bset\b",
        r"\bchange\b",
        r"\bupdate\b",
        r"\breschedul",
        r"\bpause\b",
        r"\bresume\b",
        r"\benable\b",
        r"\bdisable\b",
        r"\brun\s+now\b",
        r"\bevery\b",
        r"\bdaily\b",
        r"\bweekly\b",
        r"\binterval\b",
    ]
    has_target = any(re.search(pattern, lowered) for pattern in target_patterns)
    has_action = any(re.search(pattern, lowered) for pattern in action_patterns)
    return has_target and has_action


def _parse_past_days_token(token: str) -> Optional[int]:
    cleaned = (token or "").strip().lower()
    if not cleaned:
        return None
    if cleaned.isdigit():
        return int(cleaned)
    return _NUMBER_WORDS_TO_INT.get(cleaned)


def _looks_like_file_reference(value: str) -> bool:
    stripped = (value or "").strip()
    if not stripped:
        return False
    if stripped.startswith("file://"):
        return True
    if stripped.startswith("/"):
        return True
    # Windows-style absolute path
    if re.match(r"^[A-Za-z]:[\\/]", stripped):
        return True
    return False


def _resolve_refined_corpus_path(task_name: Optional[str]) -> Optional[str]:
    workspace = (os.getenv("CURRENT_SESSION_WORKSPACE") or "").strip()
    if not workspace:
        return None
    workspace_path = Path(workspace)
    candidates: list[Path] = []
    if task_name and task_name.strip():
        raw_task = task_name.strip()
        candidates.append(workspace_path / "tasks" / raw_task / "refined_corpus.md")
        normalized_task = _to_snake_case(raw_task)
        if normalized_task != raw_task:
            candidates.append(
                workspace_path / "tasks" / normalized_task / "refined_corpus.md"
            )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    tasks_dir = workspace_path / "tasks"
    if tasks_dir.is_dir():
        try:
            refined_files = sorted(
                tasks_dir.glob("*/refined_corpus.md"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except Exception:
            refined_files = []
        if refined_files:
            return str(refined_files[0])
    return None


def _normalize_tool_input(tool_name: str, tool_input: dict) -> Optional[dict]:
    normalized_name = (tool_name or "").lower()
    if normalized_name == "task":
        subagent_type = str(tool_input.get("subagent_type", "") or "").strip().lower()
        prompt = tool_input.get("prompt")
        if subagent_type == "research-specialist" and isinstance(prompt, str):
            match = re.search(
                r"\bpast\s+([a-zA-Z0-9-]{1,20})\s+days?\b",
                prompt,
                flags=re.IGNORECASE,
            )
            if match:
                parsed_days = _parse_past_days_token(match.group(1))
                if parsed_days is not None:
                    window_label = _rolling_window_label(parsed_days)
                    canonical_range = window_label.replace(
                        "Canonical rolling window (inclusive): ", ""
                    ).rstrip(".")
                    updated_prompt = prompt
                    phrase_text = match.group(0)
                    # Replace stale inline date windows like:
                    # "past three days (January 30-31, February 1, 2026)"
                    inline_pattern = re.escape(phrase_text) + r"\s*\([^)]*\)"
                    updated_prompt = re.sub(
                        inline_pattern,
                        f"{phrase_text} ({canonical_range})",
                        updated_prompt,
                        count=1,
                        flags=re.IGNORECASE,
                    )
                    # Ensure there is an explicit mandatory directive in the prompt.
                    mandatory_line = (
                        f"MANDATORY DATE WINDOW: {canonical_range}. "
                        "Use only this window for all searches and date references."
                    )
                    if mandatory_line not in updated_prompt:
                        updated_prompt = updated_prompt.strip() + "\n\n" + mandatory_line
                    if updated_prompt != prompt:
                        updated = dict(tool_input)
                        updated["prompt"] = updated_prompt
                        return updated
    if normalized_name.endswith("composio_multi_execute_tool"):
        # Handle wrapper-shape hallucination:
        # {"arguments": "{\"tools\": [...], \"session_id\": \"...\"}"}
        if "tools" not in tool_input:
            parsed_arguments = _try_parse_json_object(tool_input.get("arguments"))
            if parsed_arguments is not None:
                merged = dict(tool_input)
                merged.pop("arguments", None)
                merged.update(parsed_arguments)
                # If tools are already materialized as a list, return immediately.
                if isinstance(merged.get("tools"), list):
                    return merged
                tool_input = merged

        tools_value = tool_input.get("tools")
        parsed_tools = _try_parse_json_list(tools_value)
        if parsed_tools is not None:
            updated = dict(tool_input)
            updated["tools"] = parsed_tools
            return updated
    if (
        normalized_name.endswith("run_research_phase")
        or normalized_name.endswith("run_report_generation")
        or normalized_name.endswith("finalize_research")
    ):
        updated = dict(tool_input)
        changed = False
        task_name = tool_input.get("task_name")
        normalized_task_name: Optional[str] = None
        if isinstance(task_name, str) and task_name.strip():
            normalized_task = _to_snake_case(task_name)
            normalized_task_name = normalized_task
            if normalized_task != task_name:
                updated["task_name"] = normalized_task
                changed = True
        if normalized_name.endswith("run_report_generation"):
            corpus_data = tool_input.get("corpus_data")
            if isinstance(corpus_data, str):
                corpus_stripped = corpus_data.strip()
                inline_corpus_payload = (
                    not _looks_like_file_reference(corpus_stripped)
                    and (len(corpus_stripped) > 300 or "\n" in corpus_stripped)
                )
                if inline_corpus_payload:
                    resolved_path = _resolve_refined_corpus_path(
                        normalized_task_name or (task_name if isinstance(task_name, str) else None)
                    )
                    if resolved_path:
                        updated["corpus_data"] = resolved_path
                        changed = True
        if changed:
            return updated
    if normalized_name.endswith("html_to_pdf"):
        pdf_path = tool_input.get("pdf_path")
        if isinstance(pdf_path, str) and pdf_path.strip().lower().endswith(".pdf"):
            candidate = Path(pdf_path)
            normalized_stem = _to_snake_case(candidate.stem)
            if normalized_stem and normalized_stem != candidate.stem:
                updated = dict(tool_input)
                updated["pdf_path"] = str(candidate.with_name(f"{normalized_stem}.pdf"))
                return updated
    if normalized_name.endswith("composio_search_tools"):
        queries_value = tool_input.get("queries")
        parsed_queries = _try_parse_json_list(queries_value)
        if parsed_queries is not None:
            updated = dict(tool_input)
            updated["queries"] = parsed_queries
            return updated
    return None


def _format_missing_fields(missing: Iterable[str]) -> str:
    normalized = []
    for field in missing:
        if field.startswith("one_of:"):
            normalized.append("one of: " + field.split(":", 1)[1])
        else:
            normalized.append(field)
    return ", ".join(normalized)


def _example_value_from_property(prop: object) -> object:
    if not isinstance(prop, dict):
        return "..."
    examples = prop.get("examples")
    if isinstance(examples, list) and examples:
        return examples[0]
    enum = prop.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    prop_type = prop.get("type")
    if isinstance(prop_type, list) and prop_type:
        prop_type = prop_type[0]
    if prop_type == "integer":
        return 1
    if prop_type == "number":
        return 1.0
    if prop_type == "boolean":
        return True
    if prop_type == "array":
        return []
    if prop_type == "object":
        return {}
    return "..."


def build_tool_schema_from_raw_composio(
    raw_tool: dict, tool_name: str
) -> Optional[ToolSchema]:
    if not isinstance(raw_tool, dict):
        return None
    tool_block = raw_tool.get("function") if "function" in raw_tool else raw_tool
    if not isinstance(tool_block, dict):
        return None
    params = tool_block.get("parameters")
    if not isinstance(params, dict):
        return None
    required = params.get("required") or []
    if not isinstance(required, list):
        required = []
    properties = params.get("properties") or {}
    if not isinstance(properties, dict):
        properties = {}

    example_payload = {}
    for field in required:
        example_payload[field] = _example_value_from_property(properties.get(field))
    example = f"{tool_name}({json.dumps(example_payload)})" if example_payload else ""

    return ToolSchema(
        required=tuple(required),
        example=example,
    )


async def pre_tool_use_schema_guardrail(
    input_data: dict,
    run_id: Optional[str] = None,
    step_id: Optional[str] = None,
    logger: Optional[object] = None,
    skip_guardrail: bool = False,
    schema_fetcher: Optional[Callable[[str], Optional[ToolSchema]]] = None,
) -> dict:
    if skip_guardrail:
        return {}
    tool_name = str(input_data.get("tool_name", "") or "")
    tool_input = input_data.get("tool_input", {}) or {}

    # 0. Detect XML-style argument concatenation in tool name
    # Claude sometimes hallucinates tool calls like:
    #   mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOLtools</arg_key><arg_value>[...]
    # This is a model-level hallucination that we must block immediately.
    xml_fragments = ["</arg_key>", "<arg_value>", "</arg_value>", "<arg_key>"]
    if any(frag in tool_name for frag in xml_fragments):
        # Extract the likely intended tool name (everything before the XML garbage)
        import re
        clean_match = re.match(r"^(mcp__[a-z_]+__[A-Z_]+)", tool_name)
        clean_name = clean_match.group(1) if clean_match else tool_name.split("<")[0].split("</")[0]
        
        return {
            "systemMessage": (
                f"⚠️ MALFORMED TOOL CALL DETECTED.\n"
                f"You concatenated arguments into the tool name using XML syntax.\n\n"
                f"WRONG: `{tool_name[:100]}...`\n"
                f"RIGHT: `{clean_name}` with arguments as a JSON object.\n\n"
                f"**Fix**: Call `{clean_name}` and pass `tools`, `query`, etc. as JSON parameters, NOT as part of the tool name."
            ),
            "decision": "block",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"XML-style argument concatenation in tool name. Use: {clean_name}",
            },
        }

    # 1. Sanitize and Validate Tool Name
    # If the tool name is malformed (e.g. contains XML or 'tools' suffix),
    # we REJECT it immediately and tell the agent the correct name.
    identity = parse_tool_identity(tool_name)
    base_name = tool_name.split("__")[-1] if tool_name else ""
    normalized_names = {
        tool_name.lower(),
        base_name.lower(),
    }
    if identity.tool_name.lower() not in normalized_names:
         # The parser had to clean it up significantly (beyond just stripping namespace)
         # e.g. "COMPOSIO_MULTI_EXECUTE_TOOLtools..." -> "COMPOSIO_MULTI_EXECUTE_TOOL"
         return {
            "systemMessage": (
                f"⚠️ Tool name '{tool_name}' appears malformed/hallucinated. "
                f"Did you mean '{identity.tool_name}'? "
                "Please retry with the correct tool name."
            ),
            "decision": "block",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Malformed tool name. Suggested: {identity.tool_name}",
            },
        }

    normalized_name = identity.tool_name.lower()

    # 1.5. Research-phase guardrail: warn if search_results don't exist yet (race condition possible)
    # NOTE: We no longer block here because observers save files asynchronously and may not have
    # finished writing before this tool is called. The tool itself handles missing data gracefully.
    if normalized_name.endswith("run_research_phase"):
        workspace = os.getenv("CURRENT_SESSION_WORKSPACE", "")
        if not workspace:
            # Still block if workspace is completely missing - this is a config error
            return {
                "systemMessage": (
                    "⚠️ Cannot run research phase: CURRENT_SESSION_WORKSPACE is not set. "
                    "Bind the workspace for this phase before calling run_research_phase."
                ),
                "decision": "block",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Missing CURRENT_SESSION_WORKSPACE.",
                },
            }
        # Allow the tool to proceed - it will handle missing data gracefully
        # The observer may still be writing files due to async save
    if isinstance(tool_input, dict):
        if normalized_name == "task":
            subagent_type = str(tool_input.get("subagent_type", "") or "").strip().lower()
            prompt_text = str(tool_input.get("prompt", "") or "")
            if (
                subagent_type
                and subagent_type != "system-configuration-agent"
                and _looks_like_system_configuration_intent(prompt_text)
            ):
                return {
                    "systemMessage": (
                        "⚠️ Misrouted delegation detected. This task looks like a system/runtime configuration request.\n"
                        "Delegate to `Task(subagent_type='system-configuration-agent', ...)`."
                    ),
                    "decision": "block",
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "System configuration intents must route to system-configuration-agent.",
                    },
                }

        # Enforce Chron APIs for scheduling changes; block OS-level crontab mutation.
        if normalized_name.endswith("bash"):
            command = str(tool_input.get("command", "") or "")
            if _is_blocked_crontab_mutation(command):
                return {
                    "systemMessage": (
                        "⚠️ Blocked shell-level cron mutation. "
                        "Use the system configuration path instead.\n\n"
                        "Required routing:\n"
                        "1) Delegate to `Task(subagent_type='system-configuration-agent', ... )`\n"
                        "2) Apply scheduling through Universal Agent Chron APIs, not OS crontab."
                    ),
                    "decision": "block",
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "OS crontab mutation blocked. Use system-configuration-agent and Chron APIs.",
                    },
                }
        normalized_input = _normalize_tool_input(identity.tool_name, tool_input)
        if normalized_input is not None:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "updatedInput": normalized_input,
                },
            }

        if normalized_name.endswith("composio_multi_execute_tool"):
            tools_value = tool_input.get("tools")
            if tools_value is not None and not isinstance(tools_value, list):
                return {
                    "systemMessage": (
                        "⚠️ Invalid COMPOSIO_MULTI_EXECUTE_TOOL call. "
                        "The `tools` field must be a JSON array of {tool_slug, arguments} objects."
                    ),
                    "decision": "block",
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "tools must be a list, not a string.",
                    },
                }
            if isinstance(tools_value, list):
                invalid_indices = []
                for idx, item in enumerate(tools_value):
                    if not isinstance(item, dict):
                        invalid_indices.append(str(idx))
                        continue
                    if not item.get("tool_slug") or "arguments" not in item:
                        invalid_indices.append(str(idx))
                if invalid_indices:
                    return {
                        "systemMessage": (
                            "⚠️ Invalid COMPOSIO_MULTI_EXECUTE_TOOL call. "
                            "Each item in `tools` must include `tool_slug` and `arguments`."
                            f" Fix entries at indices: {', '.join(invalid_indices)}."
                        ),
                        "decision": "block",
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": "tools entries missing tool_slug or arguments.",
                        },
                    }
                
                # [MAX PARALLEL SEARCHES GUARDRAIL]
                # Composio's backend times out when too many parallel searches are executed.
                # Limit to 4 tools per call to prevent None responses.
                MAX_PARALLEL_TOOLS = 4
                if len(tools_value) > MAX_PARALLEL_TOOLS:
                    return {
                        "systemMessage": (
                            f"⚠️ TOO MANY PARALLEL TOOLS: You requested {len(tools_value)} tools, but the limit is {MAX_PARALLEL_TOOLS} per call.\n\n"
                            f"**SOLUTION**: Split your request into multiple `COMPOSIO_MULTI_EXECUTE_TOOL` calls, each with at most {MAX_PARALLEL_TOOLS} tools.\n"
                            f"Example: If you need 8 searches, make 2 separate calls with 4 tools each."
                        ),
                        "decision": "block",
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": f"Too many parallel tools ({len(tools_value)} > {MAX_PARALLEL_TOOLS}). Split into multiple calls.",
                        },
                    }

            # recursive validation of inner tools
            if isinstance(tools_value, list):
                invalid_inner_tools = []
                schema_hints = []
                for idx, item in enumerate(tools_value):
                    if not isinstance(item, dict):
                        continue
                        
                    inner_slug = item.get("tool_slug")
                    inner_args = item.get("arguments") or {}
                    
                    # We can reuse the existing validation logic for the inner tool
                    # Note: validate_tool_input is available in this module scope
                    is_valid, missing, inner_schema = validate_tool_input(inner_slug, inner_args)
                    
                    if not is_valid:
                        # Format the specific missing fields for this inner tool
                        missing_str = _format_missing_fields(missing)
                        invalid_inner_tools.append(f"Tool #{idx+1} '{inner_slug}' missing: {missing_str}")
                        # Add the schema example if available
                        if inner_schema and inner_schema.example:
                            schema_hints.append(f"  {inner_slug}: {inner_schema.example}")

                if invalid_inner_tools:
                    bullet_errors = "\n".join(f"- {err}" for err in invalid_inner_tools)
                    # Build the guidance section with schema examples
                    guidance = "Please fix the arguments for these inner tools."
                    if schema_hints:
                        guidance += "\n\n**Correct schema examples:**\n" + "\n".join(schema_hints)
                    return {
                        "systemMessage": (
                            f"⚠️ Invalid COMPOSIO_MULTI_EXECUTE_TOOL call. Inner tool validation failed:\n{bullet_errors}\n\n"
                            f"{guidance}"
                        ),
                        "decision": "block",
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": f"Inner tool schema validation failed: {'; '.join(invalid_inner_tools)}",
                        },
                    }
        if normalized_name.endswith("composio_search_tools"):
            queries_value = tool_input.get("queries")
            if queries_value is not None and not isinstance(queries_value, list):
                return {
                    "systemMessage": (
                        "⚠️ Invalid COMPOSIO_SEARCH_TOOLS call. "
                        "The `queries` field must be a JSON array of {use_case, known_fields} objects."
                    ),
                    "decision": "block",
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "queries must be a list, not a string.",
                    },
                }
            if isinstance(queries_value, list):
                invalid_indices = []
                for idx, item in enumerate(queries_value):
                    if not isinstance(item, dict) or not item.get("use_case"):
                        invalid_indices.append(str(idx))
                if invalid_indices:
                    return {
                        "systemMessage": (
                            "⚠️ Invalid COMPOSIO_SEARCH_TOOLS call. "
                            "Each item in `queries` must include `use_case`."
                            f" Fix entries at indices: {', '.join(invalid_indices)}."
                        ),
                        "decision": "block",
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": "queries entries missing use_case.",
                        },
                    }
            


    # 2. Schema Validation
    # Use the VALIDATED/CLEAN identity for schema matching, just in case
    # (Though if we triggered above, we returned. If we didn't, name is likely fine-ish)
    schema = _match_schema(identity.tool_name)
    if schema is None and schema_fetcher:
        schema = schema_fetcher(identity.tool_name)
    if schema is None:
        return {}
    if not isinstance(tool_input, dict):
        missing = list(schema.required) or ["tools"]
    else:
        missing = _missing_required(schema, tool_input)
    if not missing:
        return {}

    if logger is not None:
        logger.warning(
            "tool_validation_failed",
            tool_name=tool_name,
            missing_fields=missing,
            run_id=run_id,
            step_id=step_id,
        )

    example = schema.example if schema else ""
    example_hint = f" Example: {example}" if example else ""
    missing_fields = _format_missing_fields(missing)

    return {
        "systemMessage": (
            f"⚠️ Invalid {tool_name} call. Missing required fields: {missing_fields}."
            f"{example_hint}"
        ),
        "decision": "block",
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Tool schema validation failed.",
        },
    }


async def post_tool_use_schema_nudge(
    input_data: dict,
    run_id: Optional[str] = None,
    step_id: Optional[str] = None,
    logger: Optional[object] = None,
) -> dict:
    tool_name = str(input_data.get("tool_name", "") or "")
    tool_response = input_data.get("tool_response")
    is_error = bool(input_data.get("is_error"))

    error_detail = ""
    if isinstance(tool_response, dict):
        is_error = is_error or bool(tool_response.get("is_error") or tool_response.get("error"))
        error_detail = str(tool_response.get("error") or tool_response.get("result") or "")
    elif isinstance(tool_response, str):
        error_detail = tool_response

    if not is_error:
        return {}

    lower_detail = error_detail.lower()
    if any(
        marker in lower_detail
        for marker in (
            "no such tool available",
            "no such tool",
            "tool not available",
            "tool not found",
        )
    ):
        identity = parse_tool_identity(tool_name)
        schema = _match_schema(identity.tool_name) or _match_schema(tool_name)
        example = schema.example if schema else ""
        example_hint = f" Example: {example}" if example else ""
        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    "The last tool call failed because the tool name was invalid or malformed. "
                    "Use the exact tool name and pass arguments as JSON fields (no XML fragments)."
                    f"{example_hint}"
                ),
            },
        }


    schema = _match_schema(tool_name)
    example = schema.example if schema else ""
    example_hint = f" Example: {example}" if example else ""

    # NEW: Check for Composio-specific errors that masquerade as success
    if "No such tool available" in error_detail or "tool_use_error" in error_detail:
        # Try to extract the bad tool name if possible, or just default to current tool
        # In a multi-execute scenario, the error details usually contain the bad XML/string
        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    f"⚠️ The component '{tool_name}' reported a tool selection error: '{error_detail[:200]}...'\n"
                    "Validation Hint: Ensure you are using the correct tool name and passing arguments as a JSON object, not as XML/string concatenation.\n"
                    f"{example_hint}"
                ),
            },
        }

    # Standard missing field validation logic
    if not any(
        marker in lower_detail
        for marker in (
            "validation error",
            "field required",
            "inputvalidationerror",
            "required parameter",
            "missing required",
        )
    ):
        return {}

    schema = _match_schema(tool_name)
    example = schema.example if schema else ""
    example_hint = f" Example: {example}" if example else ""

    if logger is not None:
        logger.warning(
            "tool_validation_nudge",
            tool_name=tool_name,
            run_id=run_id,
            step_id=step_id,
            error_detail=error_detail[:200],
        )

    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                f"The last {tool_name} call failed validation."
                f" Reissue with required fields.{example_hint}"
            ),
        },
    }
