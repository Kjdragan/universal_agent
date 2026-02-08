import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from universal_agent.gateway import InProcessGateway
from universal_agent.security_paths import validate_session_id

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

    def _session_workspace(self, session_id: str) -> Path:
        safe_session_id = validate_session_id(session_id)
        workspace = (self.workspaces_dir / safe_session_id).resolve()
        try:
            workspace.relative_to(self.workspaces_dir.resolve())
        except Exception as exc:
            raise ValueError("Session path escapes workspace root") from exc
        return workspace

    def _read_policy_owner(self, session_path: Path) -> Optional[str]:
        policy_path = session_path / "session_policy.json"
        if not policy_path.exists():
            return None
        try:
            payload = json.loads(policy_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        owner = payload.get("user_id")
        if isinstance(owner, str) and owner.strip():
            return owner.strip()
        return None

    def _read_policy_memory_mode(self, session_path: Path) -> Optional[str]:
        policy_path = session_path / "session_policy.json"
        if not policy_path.exists():
            return None
        try:
            payload = json.loads(policy_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        memory = payload.get("memory")
        if not isinstance(memory, dict):
            return None
        mode = memory.get("mode")
        if isinstance(mode, str) and mode.strip():
            return mode.strip().lower()
        return None

    def _infer_source(self, session_id: str, owner: Optional[str]) -> str:
        sid = session_id.lower()
        owner_norm = (owner or "").lower()
        if sid.startswith("tg_") or owner_norm.startswith("telegram_"):
            return "telegram"
        if sid.startswith("session_"):
            return "chat"
        if sid.startswith("api_"):
            return "api"
        return "local"

    def list_sessions(
        self,
        status_filter: str = "all",
        source_filter: str = "all",
        owner_filter: Optional[str] = None,
        memory_mode_filter: str = "all",
    ) -> List[Dict[str, Any]]:
        """List all sessions via the gateway/disk."""
        # We start with workspaces directory as source of truth for Ops
        session_dirs = [p for p in self.workspaces_dir.iterdir() if p.is_dir()]
        session_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        
        summaries = [self._build_session_summary(p) for p in session_dirs]
        
        if status_filter != "all":
            normalized = status_filter.strip().lower()
            accepted = {normalized}
            if normalized == "active":
                accepted.add("running")
            elif normalized == "complete":
                accepted.add("terminal")
            summaries = [s for s in summaries if str(s.get("status", "")).lower() in accepted]

        if source_filter != "all":
            source_norm = source_filter.strip().lower()
            summaries = [s for s in summaries if str(s.get("source", "")).lower() == source_norm]

        if owner_filter:
            owner_norm = owner_filter.strip().lower()
            summaries = [s for s in summaries if str(s.get("owner", "")).lower() == owner_norm]

        if memory_mode_filter != "all":
            mode_norm = memory_mode_filter.strip().lower()
            summaries = [s for s in summaries if str(s.get("memory_mode", "")).lower() == mode_norm]
            
        return summaries

    def _build_session_summary(self, session_path: Path) -> dict:
        session_id = session_path.name
        active_session = self.gateway._sessions.get(session_id)
        runtime = {}
        if active_session:
            runtime = active_session.metadata.get("runtime", {}) or {}
            if not isinstance(runtime, dict):
                runtime = {}

        owner = None
        if active_session and active_session.user_id:
            owner = active_session.user_id
        if not owner:
            owner = self._read_policy_owner(session_path)
        if not owner and session_id.startswith("tg_"):
            owner = f"telegram_{session_id[3:]}"
        memory_mode = self._read_policy_memory_mode(session_path) or "session_only"

        source = self._infer_source(session_id, owner)
        status = str(runtime.get("lifecycle_state") or ("active" if active_session else "idle"))
        last_modified = datetime.fromtimestamp(session_path.stat().st_mtime).isoformat()
        
        journal_path = session_path / "activity_journal.log"
        run_log_path = session_path / "run.log"
        last_activity = last_modified
        runtime_last_activity = runtime.get("last_activity_at")
        if isinstance(runtime_last_activity, str) and runtime_last_activity:
            last_activity = runtime_last_activity
        if journal_path.exists():
            log_activity = datetime.fromtimestamp(journal_path.stat().st_mtime).isoformat()
            if log_activity > last_activity:
                last_activity = log_activity
            
        summary = {
            "session_id": session_id,
            "workspace_dir": str(session_path),
            "status": status,
            "source": source,
            "channel": source,
            "owner": owner or "unknown",
            "memory_mode": memory_mode,
            "last_modified": last_modified,
            "last_activity": last_activity,
            "active_connections": int(runtime.get("active_connections", 0) or 0),
            "active_runs": int(runtime.get("active_runs", 0) or 0),
            "last_event_seq": int(runtime.get("last_event_seq", 0) or 0),
            "terminal_reason": runtime.get("terminal_reason"),
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
        workspace = self._session_workspace(session_id)
        if not workspace.exists():
            return None
        return {"session": self._build_session_summary(workspace)}

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session's workspace directory and close its adapter."""
        safe_session_id = validate_session_id(session_id)
        await self.gateway.close_session(safe_session_id)

        workspace = self._session_workspace(safe_session_id)
        if workspace.exists() and workspace.is_dir():
            shutil.rmtree(workspace)
            return True
        return False

    def tail_file(self, session_id: str, filename: str, cursor: Optional[int] = None, limit: int = DEFAULT_LOG_LIMIT, max_bytes: int = DEFAULT_LOG_MAX_BYTES) -> Dict[str, Any]:
        """Tail a specific log file in the session."""
        workspace = self._session_workspace(session_id)
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
            file_path.resolve().relative_to(self.workspaces_dir.resolve())
        except Exception as exc:
            raise ValueError("Log path escapes workspace root") from exc
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
        session_path = self._session_workspace(session_id)
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

    def archive_session(
        self,
        session_id: str,
        clear_memory: bool = False,
        clear_work_products: bool = False,
    ) -> Dict[str, Any]:
        result = self.reset_session(
            session_id,
            clear_logs=True,
            clear_memory=clear_memory,
            clear_work_products=clear_work_products,
        )
        if "error" in result:
            return result
        result["status"] = "archived"
        return result

    def compact_session(self, session_id: str, max_lines: int, max_bytes: int) -> Dict[str, Any]:
        session_path = self._session_workspace(session_id)
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
