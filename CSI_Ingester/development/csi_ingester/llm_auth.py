"""Shared CSI LLM auth lane resolver."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping


def _resolve_setting(keys: list[str], env_values: Mapping[str, str]) -> str:
    for key in keys:
        env_val = (os.getenv(key) or "").strip()
        if env_val:
            return env_val
        file_val = (env_values.get(key) or "").strip()
        if file_val:
            return file_val
    return ""


def _resolve_mode(env_values: Mapping[str, str]) -> int:
    raw = _resolve_setting(["CSI_LLM_AUTH_MODE"], env_values)
    if not raw:
        return 0
    if raw not in {"0", "1"}:
        raise ValueError("CSI_LLM_AUTH_MODE must be '0' or '1'")
    return int(raw)


@dataclass(frozen=True, slots=True)
class LLMAuthSettings:
    mode: int
    lane: str
    api_key: str
    base_url: str


def resolve_csi_llm_auth(
    env_values: Mapping[str, str],
    *,
    default_base_url: str = "https://api.anthropic.com",
) -> LLMAuthSettings:
    mode = _resolve_mode(env_values)
    lane = "shared_ua" if mode == 0 else "csi_dedicated"

    if mode == 0:
        api_key = _resolve_setting(
            ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ZAI_API_KEY"],
            env_values,
        )
        base_url = _resolve_setting(["ANTHROPIC_BASE_URL"], env_values) or default_base_url
    else:
        api_key = _resolve_setting(
            ["CSI_ANTHROPIC_API_KEY", "CSI_ANTHROPIC_AUTH_TOKEN", "CSI_ZAI_API_KEY"],
            env_values,
        )
        if not api_key:
            raise ValueError(
                "CSI dedicated auth lane is enabled (CSI_LLM_AUTH_MODE=1), but no dedicated key was configured. "
                "Set CSI_ANTHROPIC_API_KEY or CSI_ANTHROPIC_AUTH_TOKEN or CSI_ZAI_API_KEY."
            )
        base_url = (
            _resolve_setting(["CSI_ANTHROPIC_BASE_URL", "CSI_ZAI_BASE_URL"], env_values)
            or default_base_url
        )

    return LLMAuthSettings(
        mode=mode,
        lane=lane,
        api_key=api_key.strip(),
        base_url=base_url.strip().rstrip("/"),
    )

