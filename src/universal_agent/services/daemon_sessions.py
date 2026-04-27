"""Persistent daemon runtimes for role-specific background execution.

Creates and maintains always-on daemon sessions for configured agents so
heartbeat supervision and Task Hub execution do not share the same runtime.

Each daemon session:
- Has a stable session ID (e.g. ``daemon_simone_heartbeat``)
- Gets a fresh workspace on each gateway startup
- Is exempt from idle timeout reaping
- Gets recycled after its own role-specific runs
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import shutil
import time
from typing import Any, Optional
import uuid

logger = logging.getLogger(__name__)

# ── Defaults ─────────────────────────────────────────────────────────────────

DAEMON_SESSION_PREFIX = "daemon_"
DEFAULT_DAEMON_AGENTS = ("simone",)  # Atlas & Cody are on-demand only; no proactive polling
DAEMON_ROLE_HEARTBEAT = "heartbeat"
DAEMON_ROLE_TODO = "todo"
_DEFAULT_AGENT_ROLE_MAP: dict[str, tuple[str, ...]] = {
    "simone": (DAEMON_ROLE_HEARTBEAT, DAEMON_ROLE_TODO),
}

_TRUTHY = {"1", "true", "yes", "on"}


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUTHY


def daemon_sessions_enabled(*, heartbeat_enabled: bool = False) -> bool:
    """Return True when daemon sessions should be created.

    Defaults to True when heartbeat is enabled.  Override with
    ``UA_DAEMON_SESSIONS_ENABLED``.
    """
    raw = os.getenv("UA_DAEMON_SESSIONS_ENABLED")
    if raw is not None:
        return _is_truthy(raw)
    return heartbeat_enabled


def configured_daemon_agents() -> list[str]:
    """Return the list of agent names that should get daemon sessions."""
    raw = (os.getenv("UA_DAEMON_SESSION_AGENTS") or "").strip()
    if raw:
        return [a.strip().lower() for a in raw.split(",") if a.strip()]
    return list(DEFAULT_DAEMON_AGENTS)


def is_daemon_session(session_id: str) -> bool:
    """Check whether a session ID belongs to a daemon session."""
    return str(session_id or "").startswith(DAEMON_SESSION_PREFIX)


# ── Lazy import to avoid circular deps ───────────────────────────────────────

def _session_role_for_daemon_role(role: str) -> str:
    role_norm = str(role or "").strip().lower()
    if role_norm == DAEMON_ROLE_TODO:
        return "todo_execution"
    return "heartbeat"


def _daemon_session_id(agent_name: str, role: str) -> str:
    return f"{DAEMON_SESSION_PREFIX}{agent_name}_{role}"


def configured_daemon_roles_for_agent(agent_name: str) -> tuple[str, ...]:
    agent_norm = str(agent_name or "").strip().lower()
    return _DEFAULT_AGENT_ROLE_MAP.get(agent_norm, (DAEMON_ROLE_HEARTBEAT,))


def _make_gateway_session(
    session_id: str,
    workspace_dir: str,
    agent_name: str,
    role: str,
) -> Any:
    """Create a lightweight GatewaySession for the daemon."""
    from universal_agent.gateway import GatewaySession

    return GatewaySession(
        session_id=session_id,
        user_id="daemon",
        workspace_dir=workspace_dir,
        metadata={
            "source": "daemon",
            "daemon_agent": agent_name,
            "daemon_role": role,
            "session_role": _session_role_for_daemon_role(role),
            "run_kind": _session_role_for_daemon_role(role),
            "created_at": time.time(),
            "last_activity_at": datetime.now(timezone.utc).isoformat(),
            "runtime": {
                "active_connections": 0,
                "active_runs": 0,
                "last_activity_at": datetime.now(timezone.utc).isoformat(),
            },
        },
    )


class DaemonSessionManager:
    """Manages persistent daemon sessions for always-on agents.

    Parameters
    ----------
    workspaces_dir : Path
        Root directory for agent workspaces (``AGENT_RUN_WORKSPACES``).
    heartbeat_service : HeartbeatService
        The singleton heartbeat service to register sessions with.
    agent_names : list[str] | None
        Names of agents to create daemon sessions for.
        Defaults to ``configured_daemon_agents()``.
    """

    def __init__(
        self,
        workspaces_dir: Path,
        heartbeat_service: Any,
        agent_names: list[str] | None = None,
    ):
        self.workspaces_dir = Path(workspaces_dir)
        self.heartbeat_service = heartbeat_service
        self.agent_names = agent_names or configured_daemon_agents()
        self._sessions: dict[str, Any] = {}
        self._session_ids: dict[tuple[str, str], str] = {}
        # Archive dir for recycled workspaces
        self._archive_dir = self.workspaces_dir / "_daemon_archives"

    @property
    def sessions(self) -> dict[str, Any]:
        """Return a copy of {session_id: session} for all daemon sessions."""
        return {sid: s for sid, s in self._sessions.items()}

    @property
    def session_ids(self) -> set[str]:
        return set(self._session_ids.values())

    def _cleanup_stale_workspaces(self) -> int:
        """Archive leftover daemon workspace dirs from previous server runs.

        On restart, old ``run_daemon_{agent}_{timestamp}_{uuid}`` directories may
        linger in the workspaces root.  Move them to ``_daemon_archives/``
        so that ``OpsService.list_sessions()`` doesn't treat them as separate
        live sessions.

        Returns the number of directories archived.
        """
        if not self.workspaces_dir.exists():
            return 0

        archived = 0
        for entry in self.workspaces_dir.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if not name.startswith(f"run_{DAEMON_SESSION_PREFIX}"):
                continue
            if name == "_daemon_archives":
                continue
            if any(
                name.startswith(f"run_{DAEMON_SESSION_PREFIX}{str(agent or '').strip().lower()}_")
                for agent in self.agent_names
            ):
                self._archive_workspace(entry)
                archived += 1

        if archived:
            logger.info(
                "🧹 Archived %d stale daemon workspace(s) from previous runs",
                archived,
            )
        return archived

    def ensure_daemon_sessions(self) -> list[str]:
        """Create and register daemon sessions for all configured agents.

        Returns list of created session IDs.
        """
        # Clean up leftover workspace dirs from previous server runs first
        self._cleanup_stale_workspaces()

        created: list[str] = []
        for agent_name in self.agent_names:
            for role in configured_daemon_roles_for_agent(agent_name):
                session_id = _daemon_session_id(agent_name, role)
                workspace = self._create_workspace(agent_name, role)
                session = _make_gateway_session(session_id, str(workspace), agent_name, role)
                self._sessions[session_id] = session
                self._session_ids[(agent_name.lower(), role)] = session_id
                if role == DAEMON_ROLE_HEARTBEAT:
                    self.heartbeat_service.register_session(session)
                created.append(session_id)
                logger.info(
                    "🤖 Daemon session created: %s → %s",
                    session_id,
                    workspace,
                )
        if created:
            logger.info(
                "🤖 Daemon sessions ready: %s",
                ", ".join(created),
            )
        return created

    def _create_workspace(self, agent_name: str, role: str) -> Path:
        """Create a fresh timestamped workspace for an agent."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_id = uuid.uuid4().hex[:8]
        workspace_name = f"run_{DAEMON_SESSION_PREFIX}{agent_name}_{role}_{ts}_{short_id}"
        workspace = self.workspaces_dir / workspace_name
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "work_products").mkdir(exist_ok=True)
        # Seed workspace bootstrap (HEARTBEAT.md, memory, etc.)
        try:
            from universal_agent.workspace import seed_workspace_bootstrap
            seed_workspace_bootstrap(str(workspace))
        except Exception as e:
            logger.warning("Daemon workspace bootstrap failed for %s: %s", agent_name, e)
        return workspace

    def recycle_session(self, session_id: str) -> Optional[str]:
        """Archive current workspace and create a fresh one for the next run.

        Returns the new workspace path, or None if session not found.
        """
        session = self._sessions.get(session_id)
        if session is None:
            logger.warning("Cannot recycle unknown daemon session: %s", session_id)
            return None

        agent_name = str(session.metadata.get("daemon_agent", "") or "").strip().lower()
        role = str(session.metadata.get("daemon_role", "") or "").strip().lower()
        if not agent_name:
            raw = session_id.removeprefix(DAEMON_SESSION_PREFIX)
            parts = raw.split("_", 1)
            agent_name = parts[0].strip().lower()
            role = parts[1].strip().lower() if len(parts) > 1 else DAEMON_ROLE_HEARTBEAT

        old_workspace = Path(session.workspace_dir)

        # Archive old workspace
        self._archive_workspace(old_workspace)

        # Create fresh workspace
        new_workspace = self._create_workspace(agent_name, role or DAEMON_ROLE_HEARTBEAT)
        session.workspace_dir = str(new_workspace)
        session.metadata["last_activity_at"] = datetime.now(timezone.utc).isoformat()
        session.metadata["created_at"] = time.time()
        if isinstance(session.metadata.get("runtime"), dict):
            session.metadata["runtime"]["last_activity_at"] = (
                datetime.now(timezone.utc).isoformat()
            )

        logger.info(
            "♻️ Daemon session %s recycled: %s → %s",
            session_id,
            old_workspace.name,
            new_workspace.name,
        )
        return str(new_workspace)

    def _archive_workspace(self, workspace: Path) -> None:
        """Move a completed daemon workspace to the archive directory."""
        if not workspace.exists():
            return
        try:
            self._archive_dir.mkdir(parents=True, exist_ok=True)
            dest = self._archive_dir / workspace.name
            if dest.exists():
                dest = self._archive_dir / f"{workspace.name}_{uuid.uuid4().hex[:6]}"
            shutil.move(str(workspace), str(dest))
            logger.debug("📦 Archived daemon workspace: %s", dest.name)
        except Exception as e:
            logger.warning("Failed to archive daemon workspace %s: %s", workspace.name, e)

    def get_session(self, session_id: str) -> Optional[Any]:
        """Get a daemon session by ID."""
        return self._sessions.get(session_id)

    def get_session_for_agent(self, agent_name: str, *, role: str | None = None) -> Optional[Any]:
        """Get the daemon session for a named agent."""
        agent_norm = str(agent_name or "").strip().lower()
        session_id = None
        if role:
            session_id = self._session_ids.get((agent_norm, str(role).strip().lower()))
        else:
            for preferred_role in (DAEMON_ROLE_TODO, DAEMON_ROLE_HEARTBEAT):
                session_id = self._session_ids.get((agent_norm, preferred_role))
                if session_id:
                    break
        if session_id:
            return self._sessions.get(session_id)
        return None

    def shutdown(self) -> None:
        """Unregister all daemon sessions from the heartbeat service."""
        for session_id in list(self._sessions.keys()):
            try:
                self.heartbeat_service.unregister_session(session_id)
            except Exception:
                pass
        self._sessions.clear()
        self._session_ids.clear()
        logger.info("🤖 Daemon sessions shut down")

    def cleanup_old_archives(self, max_age_hours: int = 48) -> int:
        """Remove archived daemon workspaces older than max_age_hours."""
        if not self._archive_dir.exists():
            return 0
        now = time.time()
        removed = 0
        for item in self._archive_dir.iterdir():
            if not item.is_dir():
                continue
            try:
                age_hours = (now - item.stat().st_mtime) / 3600
                if age_hours > max_age_hours:
                    shutil.rmtree(str(item), ignore_errors=True)
                    removed += 1
            except Exception:
                pass
        if removed:
            logger.info("🧹 Cleaned up %d old daemon workspace archives", removed)
        return removed
