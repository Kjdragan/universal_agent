"""
AgentMail Bridge Tool.

Provides internal MCP tools for sending emails natively.
"""
from typing import Any, Dict
import json
import logging
from claude_agent_sdk import tool

logger = logging.getLogger(__name__)

def _ok(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=True)}]}

def _err(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": f"error: {message}"}]}

# To avoid circular imports or early init problems, we lazy load the agentmail service from the gateway.
def _get_agentmail_service() -> Any:
    # gateway_server initializes _agentmail_service on startup.
    import universal_agent.gateway_server as gw
    return getattr(gw, "_agentmail_service", None)

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

    try:
        result = await agentmail.send_email(
            to=to,
            subject=subject,
            text=body,      # pass body as text for simplicity
            html=body,      # also pass body as html just in case
            force_send=not dry_run
        )
        return _ok({"status": "success", "result": result})
    except Exception as e:
        logger.error(f"Failed to send agentmail: {e}", exc_info=True)
        return _err(str(e))

