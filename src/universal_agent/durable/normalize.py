import hashlib
import json
from typing import Any


def _normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _normalize_value(value[k]) for k in sorted(value.keys())}
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_value(item) for item in value]
    if isinstance(value, set):
        return sorted([_normalize_value(item) for item in value])
    return value


def normalize_json(value: Any) -> str:
    normalized = _normalize_value(value)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)


def hash_normalized_json(value: Any) -> str:
    payload = normalize_json(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def deterministic_task_key(tool_input: dict[str, Any]) -> str:
    base = {k: v for k, v in tool_input.items() if k != "task_key"}
    return f"task:{hash_normalized_json(base)}"
