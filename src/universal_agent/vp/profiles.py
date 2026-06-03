from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from universal_agent.feature_flags import (
    coder_vp_display_name,
    coder_vp_id,
    vp_coder_workspace_root,
    vp_enabled_ids,
    vp_general_workspace_root,
)


@dataclass(frozen=True)
class VpProfile:
    vp_id: str
    display_name: str
    runtime_id: str
    client_kind: str
    workspace_root: Path
    soul_file: str = "SOUL.md"
    cli_capable: bool = True
    # Default inference backend for this VP — "anthropic" (real Anthropic
    # Max plan via workspace OAuth) or "zai" (ZAI/GLM proxy). The agent
    # defines its own inference, not the dispatching function: CODIE
    # (coder) defaults to "anthropic" because demo/coding work may rely on
    # Anthropic-specific features; ATLAS (generalist) and any other VP
    # default to "zai" to keep autonomous synthesis/research cheap and off
    # the scarce Max 5-hour-window credits. This is only the DEFAULT —
    # ``services/cody_mode.resolve_cody_mode`` still lets a per-task
    # ``cody_mode``, the operator DB setting, or ``UA_CODY_DEFAULT_MODE``
    # override it (e.g. flip CODIE to "zai" to save cost). Field default is
    # the cheap option so a newly-added VP is opt-in to Max, never silent.
    inference_mode: str = "zai"


def resolve_vp_profiles(workspace_base: Optional[Path | str] = None) -> dict[str, VpProfile]:
    base = Path(workspace_base).resolve() if workspace_base else Path("AGENT_RUN_WORKSPACES").resolve()
    default_profiles = {
        "vp.coder.primary": VpProfile(
            vp_id=coder_vp_id(),
            display_name=coder_vp_display_name(default="CODIE"),
            runtime_id="runtime.coder.external",
            client_kind="claude_code",
            workspace_root=_resolve_workspace_root(
                configured_root=vp_coder_workspace_root(default=""),
                fallback=(base / "vp_coder_primary_external"),
            ),
            soul_file="CODIE_SOUL.md",
            # CODIE builds runnable demos/coding artifacts that may use
            # Anthropic-specific features — default to the real Max plan.
            inference_mode="anthropic",
        ),
        "vp.general.primary": VpProfile(
            vp_id="vp.general.primary",
            display_name="ATLAS",
            runtime_id="runtime.general.external",
            client_kind="claude_generalist",
            workspace_root=_resolve_workspace_root(
                configured_root=vp_general_workspace_root(default=""),
                fallback=(base / "vp_general_primary_external"),
            ),
            soul_file="ATLAS_SOUL.md",
            # ATLAS does research / intel-brief synthesis / general reasoning
            # — runs on ZAI by default; Max is reserved for coding (CODIE).
            inference_mode="zai",
        ),
    }

    enabled = set(vp_enabled_ids(default=("vp.coder.primary", "vp.general.primary")))
    return {key: value for key, value in default_profiles.items() if key in enabled}


def get_vp_profile(vp_id: str, workspace_base: Optional[Path | str] = None) -> Optional[VpProfile]:
    profiles = resolve_vp_profiles(workspace_base=workspace_base)
    return profiles.get(vp_id)


def _resolve_workspace_root(configured_root: str, fallback: Path) -> Path:
    raw = str(configured_root or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return fallback.resolve()
