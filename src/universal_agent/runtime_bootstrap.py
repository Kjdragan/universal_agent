from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from universal_agent.infisical_loader import SecretBootstrapResult, initialize_runtime_secrets
from universal_agent.runtime_role import FactoryRuntimePolicy, build_factory_runtime_policy, normalize_llm_provider_override
from universal_agent.utils.env_aliases import apply_xai_key_aliases


@dataclass(frozen=True)
class RuntimeBootstrapResult:
    secrets: SecretBootstrapResult
    policy: FactoryRuntimePolicy
    llm_provider_override: Optional[str]


def bootstrap_runtime_environment(*, profile: str | None = None) -> RuntimeBootstrapResult:
    secrets = initialize_runtime_secrets(profile=profile)
    apply_xai_key_aliases()
    llm_provider_override = normalize_llm_provider_override()
    policy = build_factory_runtime_policy()
    return RuntimeBootstrapResult(
        secrets=secrets,
        policy=policy,
        llm_provider_override=llm_provider_override,
    )
