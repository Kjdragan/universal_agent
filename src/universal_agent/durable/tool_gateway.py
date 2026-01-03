from dataclasses import dataclass
from typing import Any, Optional

from .ledger import ToolCallLedger, LedgerReceipt

MALFORMED_TOOL_NAME_MARKERS = ("</arg_key>", "<arg_key>", "</arg_value>", "<arg_value>")


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
