"""Handlers for system-level delegation missions.

System missions (prefixed ``system:``) are handled inline by the bridge
process rather than being inserted into the VP SQLite queue.  This keeps
the VP worker loop focused on agent work while allowing infrastructure
operations like self-update to be delegated via the same Redis bus.
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class SystemMissionResult:
    """Result of a system mission execution."""
    status: str             # "SUCCESS" or "FAILED"
    result: dict[str, Any]  # payload data
    error: str = ""         # error message if FAILED
    restart_requested: bool = False  # signal bridge to exit for systemd restart
    pause_requested: bool = False    # signal bridge to pause consumption
    resume_requested: bool = False   # signal bridge to resume consumption


# ---------------------------------------------------------------------------
# system:update_factory
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 300  # 5 minutes for git pull + uv sync


def handle_update_factory(
    context: dict[str, Any],
    *,
    factory_dir: Optional[str] = None,
) -> SystemMissionResult:
    """Execute the factory self-update script.

    The script pulls latest code and syncs dependencies.  After returning
    SUCCESS with ``restart_requested=True``, the bridge should ack the
    mission, exit cleanly, and let systemd restart it with the new code.
    """
    factory_dir = factory_dir or os.getenv("UA_FACTORY_DIR", "")
    if not factory_dir:
        # Infer from script location: src/universal_agent/delegation/ → repo root
        factory_dir = str(Path(__file__).resolve().parent.parent.parent.parent)

    update_script = Path(factory_dir) / "scripts" / "update_factory.sh"
    branch = str(context.get("branch", "main")).strip() or "main"

    if not update_script.exists():
        return SystemMissionResult(
            status="FAILED",
            result={},
            error=f"Update script not found: {update_script}",
        )

    logger.info(
        "system:update_factory starting branch=%s script=%s",
        branch, update_script,
    )

    try:
        proc = subprocess.run(
            ["bash", str(update_script), "--branch", branch],
            cwd=factory_dir,
            capture_output=True,
            text=True,
            timeout=_DEFAULT_TIMEOUT,
            check=False,
        )

        if proc.returncode != 0:
            stderr_tail = (proc.stderr or "")[-500:]
            stdout_tail = (proc.stdout or "")[-1000:]
            logger.error(
                "system:update_factory script failed (exit %d): %s",
                proc.returncode, stderr_tail,
            )
            return SystemMissionResult(
                status="FAILED",
                result={"stdout": stdout_tail, "stderr": stderr_tail, "exit_code": proc.returncode},
                error=f"Update script failed (exit {proc.returncode}): {stderr_tail}",
            )

        # Extract new commit from stdout (last line of script output)
        stdout_lines = (proc.stdout or "").strip().split("\n")
        last_line = stdout_lines[-1] if stdout_lines else ""
        new_commit = last_line.split()[-1] if last_line else "unknown"

        logger.info("system:update_factory completed. new_commit=%s", new_commit)

        return SystemMissionResult(
            status="SUCCESS",
            result={
                "updated_to": new_commit,
                "branch": branch,
                "restart_scheduled": True,
                "stdout_tail": (proc.stdout or "")[-500:],
            },
            restart_requested=True,
        )

    except subprocess.TimeoutExpired:
        return SystemMissionResult(
            status="FAILED",
            result={},
            error=f"Update script timed out after {_DEFAULT_TIMEOUT}s",
        )
    except Exception as exc:
        return SystemMissionResult(
            status="FAILED",
            result={},
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# system:pause_factory / system:resume_factory
# ---------------------------------------------------------------------------

def handle_pause_factory(
    context: dict[str, Any],
    **kwargs: Any,
) -> SystemMissionResult:
    """Signal the bridge to pause mission consumption.

    The bridge stays running (heartbeat continues) but stops pulling
    new missions from Redis.  The heartbeat reports status as 'paused'.
    """
    logger.info("system:pause_factory requested")
    return SystemMissionResult(
        status="SUCCESS",
        result={"action": "pause", "note": "Bridge will pause mission consumption"},
        pause_requested=True,
    )


def handle_resume_factory(
    context: dict[str, Any],
    **kwargs: Any,
) -> SystemMissionResult:
    """Signal the bridge to resume mission consumption."""
    logger.info("system:resume_factory requested")
    return SystemMissionResult(
        status="SUCCESS",
        result={"action": "resume", "note": "Bridge will resume mission consumption"},
        resume_requested=True,
    )


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

SYSTEM_HANDLERS: dict[str, Any] = {
    "system:update_factory": handle_update_factory,
    "system:pause_factory": handle_pause_factory,
    "system:resume_factory": handle_resume_factory,
}


def is_system_mission(mission_kind: str) -> bool:
    """Check if a mission kind is a system mission."""
    return mission_kind in SYSTEM_HANDLERS


def dispatch_system_mission(
    mission_kind: str,
    context: dict[str, Any],
    **kwargs: Any,
) -> SystemMissionResult:
    """Dispatch a system mission to its handler."""
    handler = SYSTEM_HANDLERS.get(mission_kind)
    if handler is None:
        return SystemMissionResult(
            status="FAILED",
            result={},
            error=f"Unknown system mission kind: {mission_kind}",
        )
    return handler(context, **kwargs)
