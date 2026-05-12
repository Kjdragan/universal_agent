"""Guard test: the legacy regex-classifier `system_command` endpoint is gone.

The mission control bar used to POST `/api/v1/dashboard/system/commands`
which routed text through ~5 rule-based classifiers and inserted a
`source_kind="system_command"` Task Hub row. That endpoint was retired
when the bar was replaced with a Simone-chat dropdown; the new flow opens
a fresh chat session in a new tab via `window.open` and registers the work
under `source_kind="simone_chat"` via the websocket lifecycle hooks.

This test pins the removal so a re-introduction doesn't slip back in
unnoticed. Verifies both that:
  1. The route returns 404 (route is gone from the FastAPI app).
  2. The Pydantic request model and the regex helper functions are no
     longer importable from the gateway module.
"""

from __future__ import annotations

import importlib


def test_dashboard_system_command_route_is_removed() -> None:
    gateway_server = importlib.import_module("universal_agent.gateway_server")
    app = getattr(gateway_server, "app", None)
    assert app is not None, "gateway_server.app must exist"
    routes = {getattr(route, "path", None) for route in app.routes}
    assert "/api/v1/dashboard/system/commands" not in routes, (
        "Legacy regex-classifier endpoint must remain deleted — "
        "the mission control bar now spawns Simone chat sessions instead."
    )


def test_dashboard_system_command_helpers_are_removed() -> None:
    """Each deleted helper / model is no longer in the gateway module namespace."""
    gateway_server = importlib.import_module("universal_agent.gateway_server")
    for name in [
        "dashboard_system_command",
        "DashboardSystemCommandRequest",
        "_build_system_command_task_description",
        "_strip_system_command_prefix",
        "_extract_system_command_content_and_schedule",
        "_system_command_priority_from_text",
        "_system_command_is_status_query",
        "_system_command_is_personal_task",
        "_system_command_is_brainstorm_capture",
        "_system_command_task_id",
        "_park_duplicate_system_command_tasks",
        "_build_task_hub_execution_cron_command",
        "_normalize_source_context",
        "_system_context_session_id",
        "_system_context_run_id",
        "_system_context_attempt_id",
        "_source_context_snippet",
        "_SYSTEM_COMMAND_SCHEDULE_MARKERS",
    ]:
        assert not hasattr(gateway_server, name), (
            f"`{name}` should have been deleted alongside the regex pipeline."
        )
