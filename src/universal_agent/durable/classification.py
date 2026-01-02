import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional, Pattern


SIDE_EFFECT_KEYWORDS = re.compile(
    r"(SEND|CREATE|UPDATE|DELETE|PATCH|POST|MERGE|UPLOAD|INVITE|PUBLISH|COMMENT|REPLY|FORWARD|ARCHIVE|LABEL|MOVE|MARK|ASSIGN)",
    re.IGNORECASE,
)
READ_ONLY_KEYWORDS = re.compile(
    r"(GET|LIST|SEARCH|READ|FETCH|RETRIEVE)",
    re.IGNORECASE,
)

REPLAY_EXACT = "REPLAY_EXACT"
REPLAY_IDEMPOTENT = "REPLAY_IDEMPOTENT"
RELAUNCH = "RELAUNCH"

logger = logging.getLogger(__name__)

DEFAULT_POLICY_PATH = os.path.join(os.path.dirname(__file__), "tool_policies.yaml")
_POLICY_CACHE: Optional[list["ToolPolicy"]] = None
_POLICY_PATH: Optional[str] = None
_POLICY_MTIME: Optional[float] = None


@dataclass(frozen=True)
class ToolPolicy:
    name: str
    tool_namespace: Optional[str]
    patterns: list[Pattern[str]]
    replay_policy: Optional[str]
    side_effect_class: Optional[str]


def _load_tool_policies(path: str) -> list[ToolPolicy]:
    if not os.path.exists(path):
        return []
    try:
        import yaml
    except Exception:
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    policies = []
    for entry in data.get("policies", []) or []:
        patterns = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in (entry.get("patterns") or [])
        ]
        policies.append(
            ToolPolicy(
                name=str(entry.get("name") or "unnamed_policy"),
                tool_namespace=(entry.get("tool_namespace") or None),
                patterns=patterns,
                replay_policy=entry.get("replay_policy"),
                side_effect_class=entry.get("side_effect_class"),
            )
        )
    return policies


def _get_tool_policies() -> list[ToolPolicy]:
    global _POLICY_CACHE, _POLICY_PATH, _POLICY_MTIME
    path = os.getenv("UA_TOOL_POLICIES_PATH", DEFAULT_POLICY_PATH)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    if _POLICY_CACHE is None or _POLICY_PATH != path or _POLICY_MTIME != mtime:
        _POLICY_CACHE = _load_tool_policies(path)
        _POLICY_PATH = path
        _POLICY_MTIME = mtime
    return _POLICY_CACHE


def _resolve_tool_policy(
    tool_name: str, tool_namespace: str
) -> Optional[ToolPolicy]:
    policies = _get_tool_policies()
    namespace = tool_namespace.lower()
    for policy in policies:
        if policy.tool_namespace and policy.tool_namespace.lower() != namespace:
            continue
        for pattern in policy.patterns:
            if pattern.search(tool_name):
                logger.debug(
                    "tool_policy_match name=%s tool_name=%s tool_namespace=%s "
                    "replay_policy=%s side_effect_class=%s",
                    policy.name,
                    tool_name,
                    tool_namespace,
                    policy.replay_policy,
                    policy.side_effect_class,
                )
                return policy
    return None


def _reset_tool_policy_cache() -> None:
    global _POLICY_CACHE, _POLICY_PATH, _POLICY_MTIME
    _POLICY_CACHE = None
    _POLICY_PATH = None
    _POLICY_MTIME = None

KNOWN_MCP_SIDE_EFFECTS = {
    "workbench_upload": "external",
    "upload_to_composio": "external",
    "core_memory_replace": "memory",
    "core_memory_append": "memory",
    "archival_memory_insert": "memory",
    "write_local_file": "local",
    "compress_files": "local",
    "finalize_research": "local",
    "generate_image": "local",
    "preview_image": "local",
}

KNOWN_MCP_READ_ONLY = {
    "read_local_file",
    "read_research_files",
    "list_directory",
    "archival_memory_search",
    "get_core_memory_blocks",
    "describe_image",
    "crawl_parallel",
}


def classify_tool(tool_name: str, tool_namespace: str, metadata: dict[str, Any] | None = None) -> str:
    """
    Classify tool into side_effect_class: external|memory|local|read_only.
    Defaults conservative unless confidently read-only.
    """
    policy = _resolve_tool_policy(tool_name, tool_namespace)
    if policy and policy.side_effect_class:
        return policy.side_effect_class
    normalized = tool_name.lower()

    if tool_namespace == "mcp":
        if normalized in KNOWN_MCP_SIDE_EFFECTS:
            return KNOWN_MCP_SIDE_EFFECTS[normalized]
        if normalized in KNOWN_MCP_READ_ONLY:
            return "read_only"
        # Default to local side effect for unknown mcp tools
        return "local"

    upper = tool_name.upper()
    if SIDE_EFFECT_KEYWORDS.search(upper):
        return "external"
    if READ_ONLY_KEYWORDS.search(upper):
        return "read_only"

    # Default to external to be conservative
    return "external"


def classify_replay_policy(
    tool_name: str, tool_namespace: str, metadata: dict[str, Any] | None = None
) -> str:
    """
    Classify tool into replay policy: REPLAY_EXACT | REPLAY_IDEMPOTENT | RELAUNCH.
    """
    raw_tool_name = ""
    if metadata:
        raw_tool_name = str(metadata.get("raw_tool_name") or "")
    if raw_tool_name.lower() in ("taskoutput", "taskresult"):
        return RELAUNCH
    normalized_name = tool_name.lower()
    normalized_namespace = tool_namespace.lower()

    if normalized_namespace == "claude_code" and normalized_name == "task":
        return RELAUNCH
    if normalized_name in ("taskoutput", "taskresult"):
        return RELAUNCH

    policy = _resolve_tool_policy(tool_name, tool_namespace)
    if policy and policy.replay_policy:
        return policy.replay_policy

    side_effect_class = classify_tool(tool_name, tool_namespace, metadata)
    if side_effect_class == "read_only":
        return REPLAY_IDEMPOTENT
    return REPLAY_EXACT
