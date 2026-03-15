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
        return str(os.getenv("CSI_INSTANCE_ID") or self.raw.get("csi", {}).get("instance_id") or "csi-local")

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

    @property
    def ua_maintenance_mode(self) -> bool:
        if os.getenv("CSI_UA_MAINTENANCE_MODE", "").strip().lower() in ("1", "true", "yes"):
            return True
        flag_path = Path(os.getenv("CSI_UA_MAINTENANCE_FLAG", "/tmp/ua_maintenance_mode"))
        return flag_path.exists()

    @property
    def threads_app_id(self) -> str:
        return (os.getenv("THREADS_APP_ID") or "").strip()

    @property
    def threads_app_secret(self) -> str:
        return (os.getenv("THREADS_APP_SECRET") or "").strip()

    @property
    def threads_user_id(self) -> str:
        return (os.getenv("THREADS_USER_ID") or "").strip()

    @property
    def threads_access_token(self) -> str:
        return (os.getenv("THREADS_ACCESS_TOKEN") or "").strip()

    @property
    def threads_token_expires_at(self) -> str:
        return (os.getenv("THREADS_TOKEN_EXPIRES_AT") or "").strip()

    # ── Batch brief delivery ────────────────────────────────────────────
    @property
    def batch_interval_seconds(self) -> int:
        env = (os.getenv("CSI_BATCH_INTERVAL_SECONDS") or "").strip()
        if env:
            return max(60, int(env))
        raw_delivery = self.raw.get("delivery", {})
        if isinstance(raw_delivery, dict):
            val = raw_delivery.get("batch_interval_seconds")
            if val is not None:
                return max(60, int(val))
        return 7200  # default 2 hours

    @property
    def batch_min_events(self) -> int:
        env = (os.getenv("CSI_BATCH_MIN_EVENTS") or "").strip()
        if env:
            return max(1, int(env))
        raw_delivery = self.raw.get("delivery", {})
        if isinstance(raw_delivery, dict):
            val = raw_delivery.get("batch_min_events")
            if val is not None:
                return max(1, int(val))
        return 3

    @property
    def gemini_api_key(self) -> str:
        return (os.getenv("CSI_GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()

    @property
    def gemini_model(self) -> str:
        """Gemini model for batch briefs.

        Default: gemini-3-flash-preview
        Low-cost alternative: gemini-3.1-flash-lite-preview (set via CSI_GEMINI_MODEL)
        """
        env = (os.getenv("CSI_GEMINI_MODEL") or "").strip()
        if env:
            return env
        raw_delivery = self.raw.get("delivery", {})
        if isinstance(raw_delivery, dict):
            val = raw_delivery.get("gemini_model")
            if val:
                return str(val).strip()
        return "gemini-3-flash-preview"


def load_config(config_path: str | None = None) -> CSIConfig:
    path = Path(config_path or os.getenv("CSI_CONFIG_PATH") or "config/config.yaml")
    if not path.exists():
        return CSIConfig(raw={})
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"config file must parse to object: {path}")
    return CSIConfig(raw=_expand_tree(payload))
