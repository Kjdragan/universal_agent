"""Three-panel viewer — canonical identity + hydration for chat | logs | files.

This package centralizes the resolution of `session_id` / `run_id` /
`workspace_dir` into a single `SessionViewTarget` and assembles the
three-panel hydration payload server-side, replacing the ad-hoc
client-side URL building and log parsing that produced fragmented
behavior across the dashboard, Task Hub, calendar, and proactive views.

See `docs/three_panel_viewer_track_b_spec.md` for the full migration plan.
"""

from universal_agent.viewer.resolver import (
    SessionViewTarget,
    resolve_session_view_target,
)
from universal_agent.viewer.hydration import (
    HydrationResult,
    Readiness,
    hydrate,
)

__all__ = [
    "SessionViewTarget",
    "resolve_session_view_target",
    "HydrationResult",
    "Readiness",
    "hydrate",
]
