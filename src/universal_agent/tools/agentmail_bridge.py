"""
AgentMail Bridge Tool.

Provides internal MCP tools for sending emails natively.
"""
import sqlite3
import re
from typing import Any, Dict
import json
import logging
from claude_agent_sdk import tool

logger = logging.getLogger(__name__)

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

def _ok(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=True)}]}

def _err(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": f"error: {message}"}]}

# To avoid circular imports or early init problems, we lazy load the agentmail service from the gateway.
def _get_agentmail_service() -> Any:
    # gateway_server initializes _agentmail_service on startup.
    import universal_agent.gateway_server as gw
    return getattr(gw, "_agentmail_service", None)


def _request_requires_single_final_response(user_input: str) -> bool:
    text = str(user_input or "").strip()
    return any(pattern.search(text) for pattern in _SINGLE_FINAL_RESPONSE_PATTERNS)


def _looks_like_receipt_ack(subject: str, body: str) -> bool:
    text = f"{subject}\n{body}".strip()
    if not text:
        return False
    if len(text) > 600:
        return False
    return sum(1 for pattern in _ACK_PATTERNS if pattern.search(text)) >= 2


def _resolve_email_mapping_from_runtime() -> tuple[Any, Any, dict[str, Any] | None, str]:
    from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
    from universal_agent.request_runtime import get_request_runtime
    from universal_agent.services.email_task_bridge import EmailTaskBridge

    runtime = get_request_runtime()
    if runtime is None:
        return None, None, None, ""

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

    return runtime, bridge, mapping, str(runtime.run_kind or "").strip().lower()

@tool(
    name="mcp__internal__send_agentmail",
    description="Send an email or create a draft via AgentMail. Usage: Use this to send outbound emails or draft messages strictly avoiding bash script execution.",
    input_schema={
        "to": str,
        "subject": str,
        "body": str,
        "cc": str,
        "bcc": str,
        "dry_run": bool,
    }
)
async def mcp__internal__send_agentmail(args: Dict[str, Any]) -> Dict[str, Any]:
    return await _send_agentmail_impl(args)


async def _send_agentmail_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    to = str(args.get("to") or "")
    if not to:
        return _err("'to' is required")
        
    subject = str(args.get("subject") or "")
    if not subject:
        return _err("'subject' is required")
        
    body = str(args.get("body") or "")
    if not body:
        return _err("'body' is required")

    cc = str(args.get("cc") or "")
    bcc = str(args.get("bcc") or "")
    dry_run = bool(args.get("dry_run", False))

    agentmail = _get_agentmail_service()
    if not agentmail:
        return _err("AgentMail service is not available or not configured.")

    runtime = None
    bridge = None
    conn = None
    mapping = None
    run_kind = ""
    try:
        runtime, bridge, mapping, run_kind = _resolve_email_mapping_from_runtime()
        conn = getattr(bridge, "_conn", None) if bridge is not None else None
        runtime_metadata = runtime.metadata if runtime and isinstance(runtime.metadata, dict) else {}
        claimed_task_ids = [
            str(task_id or "").strip()
            for task_id in (runtime_metadata.get("claimed_task_ids") or [])
            if str(task_id or "").strip()
        ]

        if mapping:
            thread_id = str(mapping.get("thread_id") or "").strip()
            if thread_id:
                if run_kind == "email_triage":
                    if _request_requires_single_final_response(getattr(runtime, "user_input", "")):
                        return _err(
                            "Receipt acknowledgement blocked: this email task requires exactly one final response."
                        )
                    if bridge.has_final_outbound(thread_id):
                        return _err("Final email already exists for this thread; duplicate hook acknowledgement blocked.")
                    if bridge.has_ack_outbound(thread_id):
                        return _err("Receipt acknowledgement already exists for this thread.")
                elif run_kind == "todo_execution":
                    if _looks_like_receipt_ack(subject, body):
                        return _err("Receipt-style acknowledgements are not allowed during canonical ToDo execution.")
                    if bridge.has_final_outbound(thread_id):
                        return _err("Final email or draft already exists for this thread; duplicate final delivery blocked.")
        if conn is not None and run_kind == "todo_execution":
            from universal_agent import task_hub

            for task_id in claimed_task_ids:
                if task_hub._email_side_effects_detected(conn, task_id):
                    return _err("Final email or draft already exists for this task; duplicate final delivery blocked.")

        result = await agentmail.send_email(
            to=to,
            subject=subject,
            text=body,      # pass body as text for simplicity
            html=body,      # also pass body as html just in case
            force_send=not dry_run
        )
        result_payload = result if isinstance(result, dict) else {}
        message_id = str(result_payload.get("message_id") or "").strip()
        draft_id = str(result_payload.get("draft_id") or "").strip()
        if mapping:
            thread_id = str(mapping.get("thread_id") or "").strip()
            if thread_id:
                if run_kind == "email_triage":
                    bridge.record_ack_outbound(thread_id, message_id=message_id, draft_id=draft_id)
                elif run_kind == "todo_execution":
                    bridge.record_final_outbound(thread_id, message_id=message_id, draft_id=draft_id)
        if conn is not None and run_kind == "todo_execution":
            from universal_agent import task_hub

            for task_id in claimed_task_ids:
                task_hub.record_task_outbound_delivery(
                    conn,
                    task_id=task_id,
                    channel="agentmail",
                    message_id=message_id,
                    draft_id=draft_id,
                )
        return _ok({"status": "success", "result": result})
    except Exception as e:
        logger.error(f"Failed to send agentmail: {e}", exc_info=True)
        return _err(str(e))
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
