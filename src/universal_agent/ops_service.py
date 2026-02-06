import asyncio
import json
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from universal_agent.gateway import InProcessGateway, GatewaySessionSummary

logger = logging.getLogger(__name__)

# Constants matching Clawdbot parity
DEFAULT_LOG_LIMIT = 500
DEFAULT_LOG_MAX_BYTES = 250_000
MAX_LOG_LIMIT = 5000
MAX_LOG_MAX_BYTES = 1_000_000

def clamp(value: int, min_val: int, max_val: int) -> int:
    return max(min_val, min(max_val, value))

class OpsService:
    def __init__(self, gateway: InProcessGateway, workspaces_dir: Path):
        self.gateway = gateway
        self.workspaces_dir = workspaces_dir

    def list_sessions(self, status_filter: str = "all") -> List[Dict[str, Any]]:
        """List all sessions via the gateway/disk."""
        # We start with workspaces directory as source of truth for Ops
        session_dirs = [p for p in self.workspaces_dir.iterdir() if p.is_dir()]
        session_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        
        summaries = [self._build_session_summary(p) for p in session_dirs]
        
        if status_filter != "all":
            summaries = [s for s in summaries if s["status"] == status_filter]
            
        return summaries

    def _build_session_summary(self, session_path: Path) -> dict:
        session_id = session_path.name
        # Check active session status from gateway memory
        is_active = session_id in self.gateway._sessions
        
        status = "active" if is_active else "idle"
        last_modified = datetime.fromtimestamp(session_path.stat().st_mtime).isoformat()
        
        journal_path = session_path / "activity_journal.log"
        run_log_path = session_path / "run.log"
        last_activity = last_modified
        if journal_path.exists():
            last_activity = datetime.fromtimestamp(journal_path.stat().st_mtime).isoformat()
            
        summary = {
            "session_id": session_id,
            "workspace_dir": str(session_path),
            "status": status,
            "last_modified": last_modified,
            "last_activity": last_activity,
            "has_run_log": run_log_path.exists(),
            "has_activity_journal": journal_path.exists(),
            "has_memory": (session_path / "MEMORY.md").exists() or (session_path / "memory").exists(),
        }
        
        heartbeat_state = self._read_heartbeat_state(session_path)
        if heartbeat_state:
            summary["heartbeat_last"] = heartbeat_state.get("last_run")
            summary["heartbeat_summary"] = heartbeat_state.get("last_summary")
            
        return summary

    def _read_heartbeat_state(self, workspace_path: Path) -> Optional[dict]:
        state_path = workspace_path / "heartbeat_state.json"
        if not state_path.exists():
            return None
        try:
            return json.loads(state_path.read_text())
        except Exception:
            return None

    def get_session_details(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed info about a session."""
        workspace = self.workspaces_dir / session_id
        if not workspace.exists():
            return None
        return {"session": self._build_session_summary(workspace)}

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session's workspace directory and close its adapter."""
        await self.gateway.close_session(session_id)

        workspace = self.workspaces_dir / session_id
        if workspace.exists() and workspace.is_dir():
            shutil.rmtree(workspace)
            return True
        return False

    def tail_file(self, session_id: str, filename: str, cursor: Optional[int] = None, limit: int = DEFAULT_LOG_LIMIT, max_bytes: int = DEFAULT_LOG_MAX_BYTES) -> Dict[str, Any]:
        """Tail a specific log file in the session."""
        workspace = self.workspaces_dir / session_id
        file_path = workspace / filename
        
        if not file_path.exists():
             return {
                "file": filename,
                "cursor": 0,
                "size": 0,
                "lines": [],
                "truncated": False,
                "reset": False,
            }
            
        return self.read_log_slice(file_path, cursor, limit, max_bytes)

    def read_log_slice(self, file_path: Path, cursor: Optional[int], limit: int, max_bytes: int) -> Dict[str, Any]:
        try:
            stat = file_path.stat()
            size = stat.st_size
        except FileNotFoundError:
             return {"size": 0, "cursor": 0, "lines": [], "truncated": False, "reset": False}

        max_bytes = clamp(max_bytes, 1, MAX_LOG_MAX_BYTES)
        limit = clamp(limit, 1, MAX_LOG_LIMIT)
        
        reset = False
        truncated = False
        start = 0

        if cursor is None:
            # Default: tail last max_bytes
            start = max(0, size - max_bytes)
            truncated = start > 0
        else:
            cursor_val = int(cursor)
            # Logic from gateway_server.py _read_tail_lines match
            cursor_val = max(0, min(cursor_val, size))
            if cursor_val > size or size - cursor_val > max_bytes:
                reset = True
                start = max(0, size - max_bytes)
                truncated = start > 0
            else:
                start = cursor_val

        if size == 0 or size <= start:
             return {
                "cursor": size,
                "size": size,
                "lines": [],
                "truncated": truncated,
                "reset": reset,
            }

        with file_path.open("rb") as handle:
            prefix = b""
            if start > 0:
                handle.seek(start - 1)
                prefix = handle.read(1)
            
            handle.seek(start)
            blob = handle.read(size - start)

        text = blob.decode("utf-8", errors="replace")
        lines = text.split("\n")
        
        # Handle prefix logic (if we started mid-stream and didn't start at newline)
        if start > 0 and prefix != b"\n":
            lines = lines[1:]
            
        if lines and lines[-1] == "":
            lines = lines[:-1]
            
        if len(lines) > limit:
            lines = lines[-limit:]
            
        return {
            "cursor": size,
            "size": size,
            "lines": lines,
            "truncated": truncated,
            "reset": reset
        }
        
    def reset_session(self, session_id: str, clear_logs: bool = True, clear_memory: bool = False, clear_work_products: bool = False) -> Dict[str, Any]:
        session_path = self.workspaces_dir / session_id
        if not session_path.exists():
            return {"error": "Session not found"}
            
        archive_dir = session_path / "archive" / datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_dir.mkdir(parents=True, exist_ok=True)
        moved: list[str] = []

        def _move_if_exists(path: Path) -> None:
            if path.exists():
                name = path.name
                shutil.move(str(path), str(archive_dir / name))
                moved.append(name)

        if clear_logs:
            _move_if_exists(session_path / "run.log")
            _move_if_exists(session_path / "activity_journal.log")
        if clear_memory:
            _move_if_exists(session_path / "MEMORY.md")
            _move_if_exists(session_path / "memory")
        if clear_work_products:
            _move_if_exists(session_path / "work_products")

        return {"status": "reset", "archived": moved, "archive_dir": str(archive_dir)}

    def compact_session(self, session_id: str, max_lines: int, max_bytes: int) -> Dict[str, Any]:
        session_path = self.workspaces_dir / session_id
        if not session_path.exists():
             return {"error": "Session not found"}
             
        compacted = {}
        
        for filename in ["activity_journal.log", "run.log"]:
            path = session_path / filename
            if path.exists():
                # Read tail as "kept"
                result = self.read_log_slice(path, cursor=None, limit=max_lines, max_bytes=max_bytes)
                lines = result["lines"]
                
                # Overwrite file with kept lines
                # NOTE: This is destructive and not atomic, be careful in prod.
                path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
                compacted[filename] = len(lines)
                
        return {"status": "compacted", "files": compacted}
