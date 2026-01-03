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
_POLICY_PATHS: Optional[tuple[str, ...]] = None
_POLICY_MTIMES: Optional[tuple[Optional[float], ...]] = None

VALID_SIDE_EFFECT_CLASSES = {"external", "memory", "local", "read_only"}
VALID_REPLAY_POLICIES = {REPLAY_EXACT, REPLAY_IDEMPOTENT, RELAUNCH}


@dataclass(frozen=True)
class ToolPolicy:
    name: str
    tool_namespace: Optional[str]
    patterns: list[Pattern[str]]
    replay_policy: Optional[str]
    side_effect_class: Optional[str]
    source_path: Optional[str] = None


def _load_yaml(path: str) -> dict[str, Any]:
    try:
        import yaml
    except Exception as exc:
        raise RuntimeError("PyYAML is required for tool policies.") from exc
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except Exception as exc:
        raise ValueError(f"Invalid tool policy YAML in {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Tool policy YAML must be a mapping: {path}")
    return data


def _extract_patterns(entry: dict[str, Any], path: str) -> list[str]:
    patterns = entry.get("patterns")
    if patterns is None:
        patterns = entry.get("tool_name_regex")
    if patterns is None:
        patterns = entry.get("tool_name_regexes")
    if patterns is None:
        raise ValueError(
            f"Tool policy entry missing patterns/tool_name_regex in {path}: {entry}"
        )
    if isinstance(patterns, str):
        return [patterns]
    if isinstance(patterns, list) and all(isinstance(item, str) for item in patterns):
        return patterns
    raise ValueError(
        f"Tool policy patterns must be string or list of strings in {path}: {entry}"
    )


def _validate_side_effect_class(value: Optional[str], path: str) -> None:
    if value is None:
        return
    if value not in VALID_SIDE_EFFECT_CLASSES:
        raise ValueError(
            f"Invalid side_effect_class '{value}' in {path}; "
            f"expected one of {sorted(VALID_SIDE_EFFECT_CLASSES)}"
        )


def _validate_replay_policy(value: Optional[str], path: str) -> None:
    if value is None:
        return
    if value not in VALID_REPLAY_POLICIES:
        raise ValueError(
            f"Invalid replay_policy '{value}' in {path}; "
            f"expected one of {sorted(VALID_REPLAY_POLICIES)}"
        )


def _load_tool_policies(path: str, *, required: bool) -> list[ToolPolicy]:
    if not os.path.exists(path):
        if required:
            raise FileNotFoundError(f"Tool policy file not found: {path}")
        return []
    data = _load_yaml(path)
    policies_raw = data.get("policies", [])
    if policies_raw is None:
        policies_raw = []
    if not isinstance(policies_raw, list):
        raise ValueError(f"Tool policy YAML 'policies' must be a list: {path}")

    policies: list[ToolPolicy] = []
    for entry in policies_raw:
        if not isinstance(entry, dict):
            raise ValueError(f"Tool policy entry must be a mapping in {path}: {entry}")
        name = str(entry.get("name") or "unnamed_policy")
        tool_namespace = entry.get("tool_namespace") or entry.get("namespace") or None
        if tool_namespace is not None:
            tool_namespace = str(tool_namespace)
        side_effect_class = entry.get("side_effect_class")
        replay_policy = entry.get("replay_policy")
        _validate_side_effect_class(side_effect_class, path)
        _validate_replay_policy(replay_policy, path)
        patterns_raw = _extract_patterns(entry, path)
        patterns: list[Pattern[str]] = []
        for pattern in patterns_raw:
            try:
                patterns.append(re.compile(pattern, re.IGNORECASE))
            except re.error as exc:
                raise ValueError(
                    f"Invalid regex '{pattern}' in {path}: {exc}"
                ) from exc
        policies.append(
            ToolPolicy(
                name=name,
                tool_namespace=tool_namespace,
                patterns=patterns,
                replay_policy=replay_policy,
                side_effect_class=side_effect_class,
                source_path=path,
            )
        )
    return policies


def _get_tool_policies() -> list[ToolPolicy]:
    global _POLICY_CACHE, _POLICY_PATHS, _POLICY_MTIMES
    base_path = os.getenv("UA_TOOL_POLICIES_PATH", DEFAULT_POLICY_PATH)
    overlay_env = os.getenv("UA_TOOL_POLICIES_OVERLAY_PATHS") or os.getenv(
        "UA_TOOL_POLICIES_OVERLAY_PATH"
    )
    overlay_paths: list[str] = []
    if overlay_env:
        overlay_paths = [path.strip() for path in overlay_env.split(",") if path.strip()]
    paths = tuple([base_path] + overlay_paths)
    mtimes: list[Optional[float]] = []
    for path in paths:
        try:
            mtimes.append(os.path.getmtime(path))
        except OSError:
            mtimes.append(None)

    if _POLICY_CACHE is None or _POLICY_PATHS != paths or _POLICY_MTIMES != tuple(mtimes):
        base_policies = _load_tool_policies(base_path, required=True)
        overlay_policies: list[ToolPolicy] = []
        for overlay_path in overlay_paths:
            overlay_policies.extend(_load_tool_policies(overlay_path, required=False))
        _POLICY_CACHE = overlay_policies + base_policies
        _POLICY_PATHS = paths
        _POLICY_MTIMES = tuple(mtimes)
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


def resolve_tool_policy(tool_name: str, tool_namespace: str) -> Optional[ToolPolicy]:
    return _resolve_tool_policy(tool_name, tool_namespace)


def _reset_tool_policy_cache() -> None:
    global _POLICY_CACHE, _POLICY_PATHS, _POLICY_MTIMES
    _POLICY_CACHE = None
    _POLICY_PATHS = None
    _POLICY_MTIMES = None


def validate_tool_policies() -> None:
    _get_tool_policies()

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
