from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Any, Optional

from universal_agent.runtime_role import build_factory_runtime_policy

AGENTMAIL_MCP_SERVER_NAMES = {"agentmail", "AgentMail"}
AGENTMAIL_MCP_DELIVERY_TOOLS = {
    "send_message",
    "reply_to_message",
    "forward_message",
    "create_draft",
    "send_draft",
}
AGENTMAIL_MCP_DEFAULT_TOOLS = [
    "send_message",
    "reply_to_message",
    "forward_message",
    "create_draft",
    "list_drafts",
    "get_draft",
    "update_draft",
    "send_draft",
    "delete_draft",
    "list_threads",
    "get_thread",
    "get_attachment",
    "list_inboxes",
    "get_inbox",
    "update_message",
]

_SINGLE_FINAL_RESPONSE_PATTERNS = (
    re.compile(r"\bone\s+final\s+response\s+only\b", re.IGNORECASE),
    re.compile(r"\bexactly\s+one\s+final\b", re.IGNORECASE),
    re.compile(r"\bdo\s+not\s+send\s+multiple\b", re.IGNORECASE),
    re.compile(r"\bsend\s+one\s+final\s+response\s+only\b", re.IGNORECASE),
)
_ACK_PATTERNS = (
    re.compile(r"\breceived\b", re.IGNORECASE),
    re.compile(r"\bstarting\b", re.IGNORECASE),
    re.compile(r"\bwill\s+respond\b", re.IGNORECASE),
    re.compile(r"\bwill\s+send\b", re.IGNORECASE),
    re.compile(r"\bshortly\b", re.IGNORECASE),
)


def build_agentmail_mcp_server_config() -> Optional[dict[str, Any]]:
    """Return the official AgentMail MCP server config when enabled."""
    if str(os.getenv("UA_AGENTMAIL_MCP_ENABLED", "1")).strip().lower() in {"0", "false", "no", "off"}:
        return None
    if not build_factory_runtime_policy().enable_agentmail:
        return None
    api_key = str(os.getenv("AGENTMAIL_API_KEY") or "").strip()
    if not api_key:
        return None

    args = ["-y", "agentmail-mcp", "--tools", ",".join(AGENTMAIL_MCP_DEFAULT_TOOLS)]
    env = {"AGENTMAIL_API_KEY": api_key}
    base_url = str(os.getenv("AGENTMAIL_BASE_URL") or "").strip()
    if base_url:
        env["AGENTMAIL_BASE_URL"] = base_url

    return {
        "type": "stdio",
        "command": "npx",
        "args": args,
        "env": env,
    }


def is_agentmail_mcp_tool(raw_tool_name: str) -> bool:
    raw = str(raw_tool_name or "").strip()
    if not raw.startswith("mcp__"):
        return False
    parts = raw.split("__", 2)
    if len(parts) < 3:
        return False
    return parts[1] in AGENTMAIL_MCP_SERVER_NAMES


def is_agentmail_delivery_tool(raw_tool_name: str) -> bool:
    if not is_agentmail_mcp_tool(raw_tool_name):
        return False
    tool_name = str(raw_tool_name or "").split("__")[-1].strip().lower()
    return tool_name in AGENTMAIL_MCP_DELIVERY_TOOLS


def extract_agentmail_delivery_fields(tool_input: Any) -> dict[str, str]:
    payload = tool_input if isinstance(tool_input, dict) else {}
    subject = str(payload.get("subject") or "").strip()
    to_value = payload.get("to")
    if isinstance(to_value, list):
        to = ", ".join(str(item or "").strip() for item in to_value if str(item or "").strip())
    else:
        to = str(to_value or payload.get("recipient_email") or "").strip()
    body = str(
        payload.get("text")
        or payload.get("html")
        or payload.get("body")
        or payload.get("message")
        or ""
    ).strip()
    return {"to": to, "subject": subject, "body": body}


def extract_agentmail_result_ids(tool_result: Any) -> tuple[str, str]:
    """Best-effort extraction of message/draft identifiers from MCP results."""

    def _maybe_json_load(value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return value
        return value

    def _walk(value: Any) -> tuple[str, str]:
        value = _maybe_json_load(value)
        if isinstance(value, dict):
            message_id = str(value.get("message_id") or value.get("messageId") or "").strip()
            draft_id = str(value.get("draft_id") or value.get("draftId") or "").strip()
            if message_id or draft_id:
                return message_id, draft_id
            for child in value.values():
                found_message_id, found_draft_id = _walk(child)
                if found_message_id or found_draft_id:
                    return found_message_id, found_draft_id
            return "", ""
        if isinstance(value, list):
            for child in value:
                found_message_id, found_draft_id = _walk(child)
                if found_message_id or found_draft_id:
                    return found_message_id, found_draft_id
        return "", ""

    return _walk(tool_result)


def request_requires_single_final_response(user_input: str) -> bool:
    text = str(user_input or "").strip()
    return any(pattern.search(text) for pattern in _SINGLE_FINAL_RESPONSE_PATTERNS)


def looks_like_receipt_ack(subject: str, body: str) -> bool:
    text = f"{subject}\n{body}".strip()
    if not text or len(text) > 600:
        return False
    return sum(1 for pattern in _ACK_PATTERNS if pattern.search(text)) >= 2


def resolve_email_tracking_from_runtime() -> tuple[Any, Any, dict[str, Any] | None, str, list[str], sqlite3.Connection | None]:
    from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
    from universal_agent.request_runtime import get_request_runtime
    from universal_agent.services.email_task_bridge import EmailTaskBridge

    runtime = get_request_runtime()
    if runtime is None:
        return None, None, None, "", [], None

    conn = connect_runtime_db(get_activity_db_path())
    conn.row_factory = sqlite3.Row
    bridge = EmailTaskBridge(db_conn=conn)
    metadata = runtime.metadata if isinstance(runtime.metadata, dict) else {}

    mapping = None
    for task_id in metadata.get("claimed_task_ids") or []:
        mapping = bridge.get_mapping_for_task_id(str(task_id or "").strip())
        if mapping:
            break

    if mapping is None:
        session_key = str(
            metadata.get("hook_session_key")
            or metadata.get("session_key")
            or ""
        ).strip()
        if session_key:
            mapping = bridge.get_mapping_for_session_key(session_key)

    claimed_task_ids = [
        str(task_id or "").strip()
        for task_id in (metadata.get("claimed_task_ids") or [])
        if str(task_id or "").strip()
    ]
    return runtime, bridge, mapping, str(runtime.run_kind or "").strip().lower(), claimed_task_ids, conn
