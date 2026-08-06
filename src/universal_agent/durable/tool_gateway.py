"""Tool-call identity parsing and malformed-name recovery for the durable ledger.

When a model hallucinates the tool-call wire format it sometimes concatenates the
arguments into the tool name as raw XML fragments, e.g.
``COMPOSIO_MULTI_EXECUTE_TOOLtools</arg_key><arg_value>...``. This module recovers a
best-effort ``(tool_name, namespace)`` identity from such garbled input and exposes the
predicate helpers the durable ledger (:mod:`universal_agent.durable.ledger`) and the
runtime hook layer rely on to record and de-duplicate tool calls.
"""
from dataclasses import dataclass
import json
from typing import Any, Optional

from .ledger import LedgerReceipt, ToolCallLedger

MALFORMED_TOOL_NAME_MARKERS = ("</arg_key>", "<arg_key>", "</arg_value>", "<arg_value>")
INVALID_TOOL_NAME_MARKERS = ("<", ">", "{", "}", "[", "]", "\"")


@dataclass
class ToolIdentity:
    """Parsed identity for a tool call: the cleaned tool name and its source namespace.

    ``tool_namespace`` is one of ``"mcp"`` (``mcp__server__tool`` names),
    ``"claude_code"`` (the built-in ``bash``/``task`` tools), or ``"composio"`` (the
    fallback for everything else).
    """
    tool_name: str
    tool_namespace: str


def parse_tool_identity(raw_name: str) -> ToolIdentity:
    """Return a :class:`ToolIdentity` for ``raw_name``, tolerating hallucinated garbage.

    Three normalization steps run in order:

    1. If the name carries XML arg fragments (see :func:`is_malformed_tool_name`),
       strip them via :func:`parse_malformed_tool_name` to recover a clean base name.
    2. Strip a trailing ``tools`` suffix (but not ``_tools``) when it looks like a
       hallucinated plural -- e.g. ``COMPOSIO_MULTI_EXECUTE_TOOLtools`` becomes
       ``COMPOSIO_MULTI_EXECUTE_TOOL`` -- provided the remainder is still a plausible
       (longer than 5 chars) name.
    3. Classify the namespace: an ``mcp__`` prefix maps to ``"mcp"``; ``BASH``/``TASK``
       (case-insensitive) map to ``"claude_code"``; anything else falls back to
       ``"composio"``.
    """
    # 1. Sanitize XML/Garbage Suffixes
    clean_name = raw_name
    if is_malformed_tool_name(raw_name):
        base, _, _ = parse_malformed_tool_name(raw_name)
        if base:
            clean_name = base

    # 2. Heuristic: Strip 'tools' suffix if present (Common hallucination)
    # e.g. COMPOSIO_MULTI_EXECUTE_TOOLtools -> COMPOSIO_MULTI_EXECUTE_TOOL
    if clean_name.lower().endswith("tools") and not clean_name.lower().endswith("_tools"):
        # Check if stripping 'tools' leaves a plausible name
        candidate = clean_name[:-5]
        # Only strip if it doesn't leave an empty proper name
        if len(candidate) > 5:
            clean_name = candidate

    # 3. Standard Parsing
    if clean_name.startswith("mcp__"):
        parts = clean_name.split("__")
        # Format: mcp__server__tool_name
        if len(parts) >= 3:
            return ToolIdentity(tool_name=parts[-1], tool_namespace="mcp")
    if clean_name.upper() == "BASH":
        return ToolIdentity(tool_name="bash", tool_namespace="claude_code")
    if clean_name.upper() == "TASK":
        return ToolIdentity(tool_name="task", tool_namespace="claude_code")
    return ToolIdentity(tool_name=clean_name, tool_namespace="composio")


def is_malformed_tool_name(raw_name: str) -> bool:
    """True if ``raw_name`` embeds the XML arg fragments a model emits when it concatenates arguments into the tool name.

    Detected markers: ``</arg_key>``, ``<arg_key>``, ``</arg_value>``, ``<arg_value>``.
    Empty input is treated as not-malformed.
    """
    if not raw_name:
        return False
    return any(marker in raw_name for marker in MALFORMED_TOOL_NAME_MARKERS)


def is_invalid_tool_name(raw_name: str) -> bool:
    """True if ``raw_name`` contains characters that never appear in a legal tool name.

    Flagged characters: ``< > { } [ ] "``. This is stricter than
    :func:`is_malformed_tool_name`: any such character marks the name structurally
    invalid rather than merely recoverable. Empty input is treated as not-invalid.
    """
    if not raw_name:
        return False
    return any(marker in raw_name for marker in INVALID_TOOL_NAME_MARKERS)


def parse_malformed_tool_name(raw_name: str) -> tuple[Optional[str], Optional[str], Optional[Any]]:
    """Recover ``(base_name, arg_key, arg_value)`` from a malformed tool name.

    No-ops (returning ``(None, None, None)``) unless :func:`is_malformed_tool_name`
    is true for ``raw_name``.

    * ``base_name`` -- the tool name with everything from the first XML fragment onward
      stripped (trailing ``-``, ``:`` and a duplicated trailing key removed).
    * ``arg_key`` -- the argument key extracted from a ``<arg_key>...</arg_key>`` block,
      or a heuristic fallback when only the closing tag is present.
    * ``arg_value`` -- the parsed JSON value from a ``<arg_value>...</arg_value>``
      block when present and parseable, else ``None``.
    """
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
    """Outcome of :func:`prepare_tool_call`.

    ``deduped`` is true when the ledger suppressed the call as a repeat; ``receipt``
    is the recorded :class:`~universal_agent.durable.ledger.LedgerReceipt` (``None``
    when the call was de-duplicated); ``idempotency_key`` is the key bound to the call.
    """
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
    """Resolve ``raw_tool_name`` to an identity and stage the call on ``ledger``.

    Parses the (possibly malformed) tool name into a clean :class:`ToolIdentity`,
    records the pending call on ``ledger`` (honoring ``allow_duplicate`` and the
    optional ``idempotency_nonce``), and returns the resulting :class:`ToolDecision`.
    """
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
