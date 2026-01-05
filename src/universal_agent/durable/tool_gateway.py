from dataclasses import dataclass
import json
from typing import Any, Optional

from .ledger import ToolCallLedger, LedgerReceipt

MALFORMED_TOOL_NAME_MARKERS = ("</arg_key>", "<arg_key>", "</arg_value>", "<arg_value>")
INVALID_TOOL_NAME_MARKERS = ("<", ">", "{", "}", "[", "]", "\"")


@dataclass
class ToolIdentity:
    tool_name: str
    tool_namespace: str


def parse_tool_identity(raw_name: str) -> ToolIdentity:
    if raw_name.startswith("mcp__"):
        parts = raw_name.split("__")
        # Format: mcp__server__tool_name
        if len(parts) >= 3:
            return ToolIdentity(tool_name=parts[-1], tool_namespace="mcp")
    if raw_name.upper() == "BASH":
        return ToolIdentity(tool_name="bash", tool_namespace="claude_code")
    if raw_name.upper() == "TASK":
        return ToolIdentity(tool_name="task", tool_namespace="claude_code")
    return ToolIdentity(tool_name=raw_name, tool_namespace="composio")


def is_malformed_tool_name(raw_name: str) -> bool:
    if not raw_name:
        return False
    return any(marker in raw_name for marker in MALFORMED_TOOL_NAME_MARKERS)


def is_invalid_tool_name(raw_name: str) -> bool:
    if not raw_name:
        return False
    return any(marker in raw_name for marker in INVALID_TOOL_NAME_MARKERS)


def parse_malformed_tool_name(raw_name: str) -> tuple[Optional[str], Optional[str], Optional[Any]]:
    if not raw_name or not is_malformed_tool_name(raw_name):
        return None, None, None

    key = None
    value = None

    if "<arg_key>" in raw_name and "</arg_key>" in raw_name:
        key = raw_name.split("<arg_key>", 1)[1].split("</arg_key>", 1)[0].strip()
    elif "</arg_key>" in raw_name:
        before_key = raw_name.split("</arg_key>", 1)[0]
        if "-" in before_key:
            key = before_key.rsplit("-", 1)[-1].strip()

    if "<arg_value>" in raw_name and "</arg_value>" in raw_name:
        raw_value = raw_name.split("<arg_value>", 1)[1].split("</arg_value>", 1)[0].strip()
        if raw_value:
            try:
                value = json.loads(raw_value)
            except Exception:
                value = None

    base = raw_name
    if "<arg_key>" in raw_name:
        base = raw_name.split("<arg_key>", 1)[0]
    elif "</arg_key>" in raw_name:
        base = raw_name.split("</arg_key>", 1)[0]
    if "<arg_value>" in base:
        base = base.split("<arg_value>", 1)[0]
    base = base.rstrip(" -:")
    if key and base.endswith(key):
        base = base[: -len(key)].rstrip(" -:")

    return (base or None), key, value


@dataclass
class ToolDecision:
    deduped: bool
    receipt: Optional[LedgerReceipt]
    idempotency_key: str


def prepare_tool_call(
    ledger: ToolCallLedger,
    *,
    tool_call_id: str,
    run_id: str,
    step_id: str,
    raw_tool_name: str,
    tool_input: dict[str, Any],
    allow_duplicate: bool = False,
    idempotency_nonce: Optional[str] = None,
) -> ToolDecision:
    identity = parse_tool_identity(raw_tool_name)
    receipt, idempotency_key = ledger.prepare_tool_call(
        tool_call_id=tool_call_id,
        run_id=run_id,
        step_id=step_id,
        tool_name=identity.tool_name,
        tool_namespace=identity.tool_namespace,
        raw_tool_name=raw_tool_name,
        tool_input=tool_input,
        metadata={"raw_tool_name": raw_tool_name},
        allow_duplicate=allow_duplicate,
        idempotency_nonce=idempotency_nonce,
    )
    return ToolDecision(
        deduped=receipt is not None,
        receipt=receipt,
        idempotency_key=idempotency_key,
    )
