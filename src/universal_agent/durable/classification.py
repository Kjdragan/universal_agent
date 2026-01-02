import re
from typing import Any


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
    normalized_name = tool_name.lower()
    normalized_namespace = tool_namespace.lower()

    if normalized_namespace == "claude_code" and normalized_name == "task":
        return RELAUNCH

    side_effect_class = classify_tool(tool_name, tool_namespace, metadata)
    if side_effect_class == "read_only":
        return REPLAY_IDEMPOTENT
    return REPLAY_EXACT
