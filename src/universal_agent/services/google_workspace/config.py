from __future__ import annotations

from dataclasses import dataclass

from universal_agent.feature_flags import (
    google_direct_allow_composio_fallback,
    google_direct_enabled,
    google_workspace_events_enabled,
)


@dataclass(frozen=True)
class GoogleDirectConfig:
    direct_enabled: bool
    allow_composio_fallback: bool
    workspace_events_enabled: bool


def load_google_direct_config() -> GoogleDirectConfig:
    return GoogleDirectConfig(
        direct_enabled=google_direct_enabled(default=False),
        allow_composio_fallback=google_direct_allow_composio_fallback(default=True),
        workspace_events_enabled=google_workspace_events_enabled(default=False),
    )
