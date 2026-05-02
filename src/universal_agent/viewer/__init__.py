"""Three-panel viewer — canonical identity resolution.

This package centralizes the resolution of `session_id` / `run_id` /
`workspace_dir` into a single `SessionViewTarget`, replacing ad-hoc
client-side URL building across the dashboard, Task Hub, calendar, and
proactive views.

The original Track B design also included a server-side `hydrate()` step
that assembled a three-panel payload (history/logs/files). That branch
was removed: the production three-panel UI lives in `app/page.tsx` and
already rehydrates from `trace.json` + `run.log` directly. Producers
call `resolve_session_view_target()` to normalize identity hints, then
navigate to `app/page.tsx?session_id=...&run_id=...` (see
`web-ui/lib/viewer/openViewer.ts`).
"""

from universal_agent.viewer.resolver import (
    SessionViewTarget,
    resolve_session_view_target,
)

__all__ = [
    "SessionViewTarget",
    "resolve_session_view_target",
]
