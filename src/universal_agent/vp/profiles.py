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
        ),
        "vp.general.primary": VpProfile(
            vp_id="vp.general.primary",
            display_name="GENERALIST",
            runtime_id="runtime.general.external",
            client_kind="claude_generalist",
            workspace_root=_resolve_workspace_root(
                configured_root=vp_general_workspace_root(default=""),
                fallback=(base / "vp_general_primary_external"),
            ),
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
