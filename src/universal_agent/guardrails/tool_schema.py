"""Tool schema guardrails for core tools.

This module provides a curated schema map for known tools and hook helpers
that block malformed tool calls while injecting an inline schema/example hint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

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
    # Curated Composio tools (outer tool validation only)
    "composio_multi_execute_tool": ToolSchema(
        required=("tools",),
        example=(
            "COMPOSIO_MULTI_EXECUTE_TOOL({session_id: '...', tools: [{tool_slug: 'GMAIL_SEND_EMAIL', arguments: {...}}]})"
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


def _format_missing_fields(missing: Iterable[str]) -> str:
    normalized = []
    for field in missing:
        if field.startswith("one_of:"):
            normalized.append("one of: " + field.split(":", 1)[1])
        else:
            normalized.append(field)
    return ", ".join(normalized)


async def pre_tool_use_schema_guardrail(
    input_data: dict,
    run_id: Optional[str] = None,
    step_id: Optional[str] = None,
    logger: Optional[object] = None,
    skip_guardrail: bool = False,
) -> dict:
    if skip_guardrail:
        return {}
    tool_name = str(input_data.get("tool_name", "") or "")
    tool_input = input_data.get("tool_input", {}) or {}

    # 1. Sanitize and Validate Tool Name
    # If the tool name is malformed (e.g. contains XML or 'tools' suffix),
    # we REJECT it immediately and tell the agent the correct name.
    identity = parse_tool_identity(tool_name)
    if identity.tool_name != tool_name and identity.tool_name != tool_name.split("__")[-1]:
         # The parser had to clean it up significantly (beyond just stripping namespace)
         # e.g. "COMPOSIO_MULTI_EXECUTE_TOOLtools..." -> "COMPOSIO_MULTI_EXECUTE_TOOL"
         return {
            "systemMessage": (
                f"⚠️ Tool name '{tool_name}' appears malformed/hallucinated. "
                f"Did you mean '{identity.tool_name}'? "
                "Please retry with the correct tool name."
            ),
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Malformed tool name. Suggested: {identity.tool_name}",
            },
        }

    # 2. Schema Validation
    # Use the VALIDATED/CLEAN identity for schema matching, just in case
    # (Though if we triggered above, we returned. If we didn't, name is likely fine-ish)
    is_valid, missing, schema = validate_tool_input(identity.tool_name, tool_input)
    if is_valid:
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

    if "validation error" not in error_detail.lower() and "field required" not in error_detail.lower():
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
