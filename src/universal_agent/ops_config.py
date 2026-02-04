from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_ops_config_path() -> Path:
    env_path = os.getenv("UA_OPS_CONFIG_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return _project_root() / "AGENT_RUN_WORKSPACES" / "ops_config.json"


def load_ops_config() -> dict[str, Any]:
    path = resolve_ops_config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def write_ops_config(config: dict[str, Any]) -> Path:
    path = resolve_ops_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, sort_keys=True))
    return path


def ops_config_hash(config: dict[str, Any]) -> str:
    payload = json.dumps(config, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def ops_config_schema() -> dict[str, Any]:
    entry_schema = {
        "anyOf": [
            {"type": "boolean"},
            {
                "type": "object",
                "properties": {"enabled": {"type": "boolean"}},
                "additionalProperties": True,
            },
        ]
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "skills": {
                "type": "object",
                "properties": {"entries": {"type": "object", "additionalProperties": entry_schema}},
                "additionalProperties": True,
            },
            "channels": {
                "type": "object",
                "properties": {"entries": {"type": "object", "additionalProperties": entry_schema}},
                "additionalProperties": True,
            },
        },
        "additionalProperties": True,
    }


def apply_merge_patch(target: Any, patch: Any) -> Any:
    if patch is None:
        return target
    if not isinstance(patch, dict):
        return patch
    if not isinstance(target, dict):
        target = {}
    result = dict(target)
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = apply_merge_patch(result.get(key), value)
    return result
