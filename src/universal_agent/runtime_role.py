from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
import os
import socket
from typing import Optional

logger = logging.getLogger(__name__)


class FactoryRole(str, Enum):
    """Where this node sits in the factory topology.

    The role drives the default capability policy resolved by
    :func:`build_factory_runtime_policy`: whether the node runs the full
    gateway, publishes or listens for delegated work, and which services
    (CSI ingest, AgentMail inbox) it owns.
    """

    HEADQUARTERS = "HEADQUARTERS"
    LOCAL_WORKER = "LOCAL_WORKER"
    STANDALONE_NODE = "STANDALONE_NODE"


_ALLOWED_LLM_PROVIDER_OVERRIDES = {"ZAI", "ANTHROPIC", "OPENAI", "OLLAMA"}
_VALID_RUNTIME_STAGES = {"development", "staging", "local", "production"}


@dataclass(frozen=True)
class FactoryRuntimePolicy:
    """Resolved capability policy for the current factory node.

    Frozen dataclass produced by :func:`build_factory_runtime_policy`. Most
    fields are booleans or mode strings sourced from the :class:`FactoryRole`
    defaults and then optionally overridden by ``UA_CAPABILITY_*`` env vars.
    The ``can_*`` / ``is_*`` properties are the canonical way to ask
    capability questions so callers do not re-derive them from mode strings.
    """

    role: str
    gateway_mode: str  # full | health_only
    start_ui: bool
    enable_telegram_poll: bool
    heartbeat_scope: str  # global | local
    delegation_mode: str  # publish_and_listen | listen_only | disabled
    enable_csi_ingest: bool = True   # CSI signal ingestion (HQ-only)
    enable_agentmail: bool = True    # AgentMail inbox service (HQ-only)

    @property
    def can_publish_delegations(self) -> bool:
        """Whether this node may publish delegated work for others to claim.

        True only when ``delegation_mode == "publish_and_listen"`` (the
        headquarters default).
        """
        return self.delegation_mode == "publish_and_listen"

    @property
    def can_listen_delegations(self) -> bool:
        """Whether this node consumes delegated work from upstream publishers.

        True for ``publish_and_listen`` and ``listen_only`` modes; False for
        ``disabled``.
        """
        return self.delegation_mode in {"publish_and_listen", "listen_only"}

    @property
    def is_headquarters(self) -> bool:
        """Whether this node is running as the headquarters (``HEADQUARTERS``)."""
        return self.role == FactoryRole.HEADQUARTERS.value


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def resolve_runtime_stage(raw_stage: Optional[str] = None) -> Optional[str]:
    """Resolve the deployment stage (development/staging/local/production).

    Prefers an explicit ``raw_stage`` argument, then the ``UA_RUNTIME_STAGE``
    env var. Returns ``None`` when neither is set. Raises ``ValueError`` for
    an unrecognized value so an invalid stage never silently passes through.
    """
    raw = str(raw_stage or os.getenv("UA_RUNTIME_STAGE") or "").strip().lower()
    if not raw:
        return None
    if raw in _VALID_RUNTIME_STAGES:
        return raw
    raise ValueError(
        f"Unsupported UA_RUNTIME_STAGE={raw!r}; expected one of: "
        + ", ".join(sorted(_VALID_RUNTIME_STAGES))
    )


def resolve_machine_slug(raw_slug: Optional[str] = None) -> str:
    """Resolve a non-empty slug identifying this physical machine.

    Tries, in order: the ``raw_slug`` argument, ``UA_MACHINE_SLUG``,
    ``UA_FACTORY_ID``, ``INFISICAL_MACHINE_IDENTITY_NAME``, and finally the
    OS hostname as an always-present fallback. Used for machine-scoped state
    and Infisical environment mapping.
    """
    raw = (
        str(raw_slug or "").strip()
        or str(os.getenv("UA_MACHINE_SLUG") or "").strip()
        or str(os.getenv("UA_FACTORY_ID") or "").strip()
        or str(os.getenv("INFISICAL_MACHINE_IDENTITY_NAME") or "").strip()
        or socket.gethostname()
    )
    return raw


def resolve_factory_role(raw_role: Optional[str] = None) -> FactoryRole:
    """Resolve the node's :class:`FactoryRole` with a fail-safe default.

    Prefers an explicit ``raw_role`` argument, then the ``FACTORY_ROLE`` env
    var, defaulting to headquarters. An unrecognized value logs ``critical``
    and falls back to :attr:`FactoryRole.LOCAL_WORKER` so a misconfigured node
    never runs as a full headquarters by accident.
    """
    raw = str(raw_role or os.getenv("FACTORY_ROLE") or FactoryRole.HEADQUARTERS.value).strip().upper()
    try:
        return FactoryRole(raw)
    except ValueError:
        logger.critical(
            "Unknown FACTORY_ROLE=%s; falling back to LOCAL_WORKER fail-safe mode",
            raw,
        )
        return FactoryRole.LOCAL_WORKER


from typing import Any, Optional


def build_factory_runtime_policy(raw_role: Optional[str] = None) -> FactoryRuntimePolicy:
    """Build the :class:`FactoryRuntimePolicy` for this node.

    Starts from role-based defaults (headquarters runs everything; a local
    worker runs a health-only gateway and only listens; a standalone node runs
    the full stack but does not delegate), then applies explicit
    ``UA_CAPABILITY_*`` env-var overrides for the boolean and mode fields.
    Only explicitly-set env vars override -- absent vars leave the role
    default in place.
    """
    role = resolve_factory_role(raw_role)
    kwargs: dict[str, Any] = {}

    # Base defaults determined by role
    if role is FactoryRole.HEADQUARTERS:
        kwargs = {
            "gateway_mode": "full",
            "start_ui": True,
            "enable_telegram_poll": True,
            "heartbeat_scope": "global",
            "delegation_mode": "publish_and_listen",
            "enable_csi_ingest": True,
            "enable_agentmail": True,
        }
    elif role is FactoryRole.LOCAL_WORKER:
        kwargs = {
            "gateway_mode": "health_only",
            "start_ui": False,
            "enable_telegram_poll": False,
            "heartbeat_scope": "local",
            "delegation_mode": "listen_only",
            "enable_csi_ingest": False,
            "enable_agentmail": False,
        }
    else:
        # STANDALONE_NODE
        kwargs = {
            "gateway_mode": "full",
            "start_ui": True,
            "enable_telegram_poll": _env_flag("UA_STANDALONE_ENABLE_TELEGRAM_POLL", default=False),
            "heartbeat_scope": "local",
            "delegation_mode": "disabled",
            "enable_csi_ingest": True,
            "enable_agentmail": True,
        }

    # Allow explicit UA_CAPABILITY_* flag overrides for boolean fields
    bool_fields = {
        "start_ui": "UA_CAPABILITY_START_UI",
        "enable_telegram_poll": "UA_CAPABILITY_TELEGRAM_POLL",
        "enable_csi_ingest": "UA_CAPABILITY_CSI_INGEST",
        "enable_agentmail": "UA_CAPABILITY_AGENTMAIL",
    }
    
    for field, env_var in bool_fields.items():
        base_val = bool(kwargs[field])
        # _env_flag treats empty as default, so we only override if explicitly set
        raw = str(os.getenv(env_var, "")).strip()
        if raw:
            kwargs[field] = _env_flag(env_var, base_val)

    # Allow explicit overrides for enum/string fields
    str_fields = {
        "gateway_mode": "UA_CAPABILITY_GATEWAY_MODE",
        "heartbeat_scope": "UA_CAPABILITY_HEARTBEAT_SCOPE",
        "delegation_mode": "UA_CAPABILITY_DELEGATION_MODE",
    }
    
    for field, env_var in str_fields.items():
        val = str(os.getenv(env_var, "")).strip()
        if val:
            kwargs[field] = val

    return FactoryRuntimePolicy(role=role.value, **kwargs)


def normalize_llm_provider_override() -> Optional[str]:
    """Normalize and validate ``LLM_PROVIDER_OVERRIDE`` in place.

    Upper-cases the value and, if it is one of the allowed providers
    (ZAI/ANTHROPIC/OPENAI/OLLAMA), writes the normalized form back to the env
    and returns it. An unsupported value is logged, removed from the
    environment, and yields ``None`` so downstream routing falls back to its
    own default.
    """
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
