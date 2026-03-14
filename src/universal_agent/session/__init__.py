"""Session lifecycle management."""

from universal_agent.session.reaper import cleanup_stale_workspaces

__all__ = ["cleanup_stale_workspaces"]
