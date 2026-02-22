"""CSI configuration loading."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(value: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        return os.getenv(match.group(1), "")

    return _ENV_PATTERN.sub(_replace, value)


def _expand_tree(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _expand_tree(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_expand_tree(v) for v in node]
    if isinstance(node, str):
        return _expand_env(node)
    return node


@dataclass(slots=True)
class CSIConfig:
    raw: dict[str, Any]

    @property
    def instance_id(self) -> str:
        return str(self.raw.get("csi", {}).get("instance_id") or os.getenv("CSI_INSTANCE_ID") or "csi-local")

    @property
    def db_path(self) -> Path:
        override = (os.getenv("CSI_DB_PATH") or "").strip()
        if override:
            return Path(override).expanduser()
        configured = str(self.raw.get("storage", {}).get("db_path") or "var/csi.db")
        return Path(configured).expanduser()

    @property
    def ua_endpoint(self) -> str:
        return str(self.raw.get("delivery", {}).get("ua_endpoint") or os.getenv("CSI_UA_ENDPOINT") or "").strip()

    @property
    def ua_shared_secret(self) -> str:
        return (os.getenv("CSI_UA_SHARED_SECRET") or "").strip()


def load_config(config_path: str | None = None) -> CSIConfig:
    path = Path(config_path or os.getenv("CSI_CONFIG_PATH") or "config/config.yaml")
    if not path.exists():
        return CSIConfig(raw={})
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"config file must parse to object: {path}")
    return CSIConfig(raw=_expand_tree(payload))

