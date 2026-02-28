from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class FactoryRole(str, Enum):
    HEADQUARTERS = "HEADQUARTERS"
    LOCAL_WORKER = "LOCAL_WORKER"
    STANDALONE_NODE = "STANDALONE_NODE"


_ALLOWED_LLM_PROVIDER_OVERRIDES = {"ZAI", "ANTHROPIC", "OPENAI", "OLLAMA"}


@dataclass(frozen=True)
class FactoryRuntimePolicy:
    role: str
    gateway_mode: str  # full | health_only
    start_ui: bool
    enable_telegram_poll: bool
    heartbeat_scope: str  # global | local
    delegation_mode: str  # publish_and_listen | listen_only | disabled

    @property
    def can_publish_delegations(self) -> bool:
        return self.delegation_mode == "publish_and_listen"

    @property
    def can_listen_delegations(self) -> bool:
        return self.delegation_mode in {"publish_and_listen", "listen_only"}


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def resolve_factory_role(raw_role: Optional[str] = None) -> FactoryRole:
    raw = str(raw_role or os.getenv("FACTORY_ROLE") or FactoryRole.HEADQUARTERS.value).strip().upper()
    try:
        return FactoryRole(raw)
    except ValueError:
        logger.critical(
            "Unknown FACTORY_ROLE=%s; falling back to LOCAL_WORKER fail-safe mode",
            raw,
        )
        return FactoryRole.LOCAL_WORKER


def build_factory_runtime_policy(raw_role: Optional[str] = None) -> FactoryRuntimePolicy:
    role = resolve_factory_role(raw_role)

    if role is FactoryRole.HEADQUARTERS:
        return FactoryRuntimePolicy(
            role=role.value,
            gateway_mode="full",
            start_ui=True,
            enable_telegram_poll=True,
            heartbeat_scope="global",
            delegation_mode="publish_and_listen",
        )

    if role is FactoryRole.LOCAL_WORKER:
        return FactoryRuntimePolicy(
            role=role.value,
            gateway_mode="health_only",
            start_ui=False,
            enable_telegram_poll=False,
            heartbeat_scope="local",
            delegation_mode="listen_only",
        )

    # STANDALONE_NODE
    return FactoryRuntimePolicy(
        role=role.value,
        gateway_mode="full",
        start_ui=True,
        enable_telegram_poll=_env_flag("UA_STANDALONE_ENABLE_TELEGRAM_POLL", default=False),
        heartbeat_scope="local",
        delegation_mode="disabled",
    )


def normalize_llm_provider_override() -> Optional[str]:
    raw = str(os.getenv("LLM_PROVIDER_OVERRIDE") or "").strip()
    if not raw:
        return None

    normalized = raw.upper()
    if normalized not in _ALLOWED_LLM_PROVIDER_OVERRIDES:
        logger.warning(
            "Ignoring unsupported LLM_PROVIDER_OVERRIDE=%s (allowed=%s)",
            raw,
            ",".join(sorted(_ALLOWED_LLM_PROVIDER_OVERRIDES)),
        )
        os.environ.pop("LLM_PROVIDER_OVERRIDE", None)
        return None

    os.environ["LLM_PROVIDER_OVERRIDE"] = normalized
    return normalized
