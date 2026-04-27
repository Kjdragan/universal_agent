from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import shutil
from typing import Any, Dict, List, Optional

from universal_agent.durable.db import connect_runtime_db, get_runtime_db_path
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import list_run_attempts
from universal_agent.feature_flags import sdk_session_history_enabled
from universal_agent.gateway import InProcessGateway
from universal_agent.memory.orchestrator import get_memory_orchestrator
from universal_agent.memory.paths import resolve_shared_memory_workspace
from universal_agent.run_catalog import RunCatalogService
from universal_agent.sdk import session_history_adapter
from universal_agent.security_paths import is_valid_session_id, validate_session_id
from universal_agent.services.daemon_sessions import DAEMON_SESSION_PREFIX

logger = logging.getLogger(__name__)

# Constants matching Clawdbot parity
DEFAULT_LOG_LIMIT = 500
DEFAULT_LOG_MAX_BYTES = 250_000
MAX_LOG_LIMIT = 5000
MAX_LOG_MAX_BYTES = 1_000_000

# Session directory UX: attempt to show a short "what was this session about?"
_SESSION_DESC_MAX_CHARS = 160
_SESSION_DESC_MAX_FILE_BYTES = 96_000

def clamp(value: int, min_val: int, max_val: int) -> int:
    return max(min_val, min(max_val, value))

class OpsService:
    def __init__(self, gateway: InProcessGateway, workspaces_dir: Path):
        self.gateway = gateway
        self.workspaces_dir = workspaces_dir
        self.run_catalog = RunCatalogService()

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
        enabled = memory.get("enabled", True)
        session_enabled = memory.get("sessionMemory", True)
        scope = str(memory.get("scope", "direct_only")).strip().lower()
        if not bool(enabled):
            return "off"
        if not bool(session_enabled):
            return "memory_only"
        if scope in {"direct_only", "all"}:
            return scope
        return None

    def _infer_source(self, session_id: str, owner: Optional[str]) -> str:
        sid = session_id.lower()
        owner_norm = (owner or "").lower()
        if sid.startswith(DAEMON_SESSION_PREFIX):
            return "daemon"
        if sid.startswith("tg_") or owner_norm.startswith("telegram_"):
            return "telegram"
        if sid.startswith("session_"):
            return "chat"
        if sid.startswith("api_"):
            return "api"
        return "local"

    @staticmethod
    def _classify_channel(
        session_id: str,
        source: str,
        run_kind: str = "",
        trigger_source: str = "",
        last_run_source: str = "",
    ) -> str:
        """Classify a session into a UI channel group.

        Uses all available signals — not just the session_id prefix — to
        produce a richer channel classification for the dashboard inbox.
        """
        sid = (session_id or "").lower()
        rk = (run_kind or "").lower()
        ts = (trigger_source or "").lower()
        lrs = (last_run_source or "").lower()
        src = (source or "").lower()

        # Infrastructure / daemon
        if sid.startswith(DAEMON_SESSION_PREFIX) or rk == "heartbeat":
            return "infrastructure"

        # VP missions
        if (
            sid.startswith("vp_")
            or "vp" in rk
            or src.startswith("vp")
            or "vp" in lrs
        ):
            return "vp_mission"

        # Email
        if rk == "email_triage" or "agentmail" in ts or "email" in rk:
            return "email"

        # Scheduled / Cron
        if rk == "cron" or sid.startswith("cron_") or "cron" in ts or "cron" in lrs:
            return "scheduled"

        # Proactive signals
        if (
            "proactive" in rk
            or "signal" in rk
            or ts == "dashboard_signal"
            or "proactive" in lrs
        ):
            return "proactive"

        # Discord
        if sid.startswith("discord_") or "discord" in src or "discord" in ts:
            return "discord"

        # Interactive chats (websocket, local, telegram, api, chat)
        if src in ("chat", "local", "websocket", "api") and not rk:
            return "interactive"
        if sid.startswith(("session_", "tg_")) and not rk:
            return "interactive"

        return "system"

    def _normalize_session_description(self, text: str) -> str:
        # Collapse whitespace/newlines, trim, and cap length for UI cards.
        cleaned = re.sub(r"\s+", " ", (text or "").strip())
        if len(cleaned) > _SESSION_DESC_MAX_CHARS:
            cleaned = cleaned[: _SESSION_DESC_MAX_CHARS - 1].rstrip() + "…"
        return cleaned

    def _read_text_prefix(self, path: Path, max_bytes: int = _SESSION_DESC_MAX_FILE_BYTES) -> Optional[str]:
        try:
            if not path.exists() or not path.is_file():
                return None
            raw = path.open("rb").read(max_bytes)
            return raw.decode("utf-8", errors="ignore")
        except Exception:
            return None

    def _try_read_checkpoint_description(self, session_path: Path) -> Optional[str]:
        # Preferred: run/session checkpoint carries a clean "original_request".
        for checkpoint_name in ("run_checkpoint.json", "session_checkpoint.json"):
            checkpoint_path = session_path / checkpoint_name
            if not checkpoint_path.exists():
                continue
            try:
                payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    for key in ("original_request", "query", "prompt", "task", "description", "title"):
                        val = payload.get(key)
                        if isinstance(val, str) and val.strip():
                            return val.strip()
            except Exception:
                pass

        # Fallback: run/session checkpoint markdown has a "### Original Request" block quote.
        md = None
        for checkpoint_md_name in ("run_checkpoint.md", "session_checkpoint.md"):
            checkpoint_md_path = session_path / checkpoint_md_name
            md = self._read_text_prefix(checkpoint_md_path)
            if md:
                break
        if not md:
            return None

        # Look for the first blockquote after the "Original Request" heading.
        m = re.search(r"^###\s+Original Request\s*\n(?P<body>(?:>.*\n)+)", md, flags=re.MULTILINE)
        if not m:
            return None
        body = m.group("body")
        # Strip leading '> ' markers and join.
        lines = []
        for ln in body.splitlines():
            if not ln.lstrip().startswith(">"):
                continue
            lines.append(ln.lstrip()[1:].lstrip())
        joined = " ".join(lines).strip()
        return joined or None

    def _try_read_trace_description(self, session_path: Path) -> Optional[str]:
        trace_path = session_path / "trace.json"
        if not trace_path.exists():
            return None
        try:
            payload = json.loads(trace_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                q = payload.get("query")
                if isinstance(q, str) and q.strip():
                    return q.strip()
        except Exception:
            return None
        return None

    def _try_read_transcript_description(self, session_path: Path) -> Optional[str]:
        # Transcript is larger; only read prefix and extract "User Request" quote.
        transcript_path = session_path / "transcript.md"
        md = self._read_text_prefix(transcript_path)
        if not md:
            return None
        m = re.search(r"^###\s+👤\s+User Request\s*\n>\\s*(?P<req>.+?)\\s*(?:\n\n|\\n---|$)", md, flags=re.MULTILINE | re.DOTALL)
        if not m:
            # Older format: "### User Request"
            m = re.search(r"^###\s+User Request\s*\n>\\s*(?P<req>.+?)\\s*(?:\n\n|\\n---|$)", md, flags=re.MULTILINE | re.DOTALL)
        if not m:
            return None
        req = m.group("req")
        # If blockquote spans multiple lines, strip '>' and join.
        req_lines = []
        for ln in req.splitlines():
            req_lines.append(ln.lstrip(">").strip())
        return " ".join([l for l in req_lines if l]).strip() or None

    def _try_read_explicit_description(self, session_path: Path) -> Optional[str]:
        desc_path = session_path / "description.txt"
        return self._read_text_prefix(desc_path)

    def _try_read_context_brief_title(self, session_path: Path) -> Optional[str]:
        """Extract the first heading from context_brief.md as a description fallback."""
        brief_path = session_path / "context_brief.md"
        text = self._read_text_prefix(brief_path, max_bytes=2048)
        if not text:
            return None
        # Look for first markdown heading
        m = re.search(r"^#{1,3}\s+(.+)$", text, flags=re.MULTILINE)
        if m:
            return m.group(1).strip()
        # Fallback: first non-empty line
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("---"):
                return stripped
        return None

    def _derive_session_description(self, session_path: Path) -> Optional[str]:
        raw = (
            self._try_read_explicit_description(session_path)
            or self._try_read_context_brief_title(session_path)
            or self._try_read_checkpoint_description(session_path)
            or self._try_read_trace_description(session_path)
            or self._try_read_transcript_description(session_path)
        )
        if not raw:
            return None
        normalized = self._normalize_session_description(raw)
        return normalized or None

    def _parse_iso_utc(self, value: Any) -> Optional[datetime]:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        normalized = text
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except Exception:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _extract_daemon_agent(session_id: str) -> Optional[str]:
        """Extract agent name from a daemon workspace directory name.

        ``daemon_simone_20260322_051942_f38ff5bf`` → ``simone``
        ``daemon_atlas`` → ``atlas``
        Returns None for non-daemon session IDs.
        """
        if not session_id.startswith(DAEMON_SESSION_PREFIX):
            return None
        suffix = session_id[len(DAEMON_SESSION_PREFIX):]  # "simone_20260322_..."
        parts = suffix.split("_", 1)
        return parts[0].lower() if parts else None

    def _deduplicate_daemon_sessions(
        self, summaries: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Keep only the newest daemon workspace per agent.

        When multiple ``daemon_{agent}_*`` workspace directories exist (from
        server restarts), only the most recently modified one is returned.
        The rest are silently dropped from the listing.
        """
        # Partition into daemon workspaces and everything else
        daemon_groups: Dict[str, List[Dict[str, Any]]] = {}  # agent -> [summaries]
        result: List[Dict[str, Any]] = []

        for s in summaries:
            agent = self._extract_daemon_agent(str(s.get("session_id", "")))
            if agent:
                daemon_groups.setdefault(agent, []).append(s)
            else:
                result.append(s)

        # For each agent, keep only the newest by last_modified
        for agent, group in daemon_groups.items():
            # Sort by mtime descending; pick newest
            group.sort(
                key=lambda g: g.get("last_modified") or g.get("created_at") or "",
                reverse=True,
            )
            result.append(group[0])

        return result

    def list_sessions(
        self,
        status_filter: str = "all",
        source_filter: str = "all",
        owner_filter: Optional[str] = None,
        memory_mode_filter: str = "all",
    ) -> List[Dict[str, Any]]:
        """List all sessions via the gateway/disk."""
        # We start with workspaces directory as source of truth for Ops
        session_dirs = [p for p in self.workspaces_dir.iterdir() if p.is_dir() and is_valid_session_id(p.name)]
        session_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        
        summaries = [self._build_session_summary(p) for p in session_dirs]

        # Daemon workspace dedup: multiple daemon_{agent}_* dirs may exist
        # from server restarts; only keep the newest per agent.
        summaries = self._deduplicate_daemon_sessions(summaries)

        if sdk_session_history_enabled(default=False):
            try:
                sdk_rows = session_history_adapter.list_session_summaries_for_workspace(
                    self.workspaces_dir,
                    limit=200,
                )
                existing = {str(item.get("session_id", "")) for item in summaries}
                for row in sdk_rows:
                    session_id = str(row.get("session_id", "") or "").strip()
                    if not session_id:
                        continue
                    if session_id in existing:
                        for item in summaries:
                            if str(item.get("session_id", "")) == session_id:
                                item["sdk_history"] = row
                                break
                        continue
                    summaries.append(
                        {
                            "session_id": session_id,
                            "status": "history_only",
                            "workspace_dir": str(row.get("workspace_dir") or ""),
                            "source": "sdk_history",
                            "owner": "",
                            "memory_mode": "unknown",
                            "active_runs": 0,
                            "active_connections": 0,
                            "last_modified": row.get("last_modified"),
                            "last_activity": row.get("last_modified"),
                            "sdk_history": row,
                        }
                    )
                    existing.add(session_id)
            except Exception as exc:
                logger.warning("OpsService SDK history augmentation failed: %s", exc)
        
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
        memory_mode = self._read_policy_memory_mode(session_path) or "direct_only"

        source = self._infer_source(session_id, owner)
        lifecycle_state = str(runtime.get("lifecycle_state") or "").strip().lower()
        active_connections = int(runtime.get("active_connections", 0) or 0)
        active_runs = int(runtime.get("active_runs", 0) or 0)
        if lifecycle_state == "terminal":
            status = "terminal"
        elif active_runs > 0:
            status = "running"
        elif active_connections > 0:
            status = "active"
        else:
            status = "idle"
        stat_info = session_path.stat()
        last_modified_dt = datetime.fromtimestamp(stat_info.st_mtime, tz=timezone.utc)
        last_modified = last_modified_dt.isoformat()
        created_dt = datetime.fromtimestamp(stat_info.st_ctime, tz=timezone.utc)
        created_at = created_dt.isoformat()
        
        journal_path = session_path / "activity_journal.log"
        run_log_path = session_path / "run.log"
        last_activity_dt = last_modified_dt
        runtime_last_activity = runtime.get("last_activity_at")
        parsed_runtime_last_activity = self._parse_iso_utc(runtime_last_activity)
        if parsed_runtime_last_activity is not None:
            last_activity_dt = parsed_runtime_last_activity
        if journal_path.exists():
            log_activity_dt = datetime.fromtimestamp(journal_path.stat().st_mtime, tz=timezone.utc)
            if log_activity_dt > last_activity_dt:
                last_activity_dt = log_activity_dt
        last_activity = last_activity_dt.isoformat()
            
        description = self._derive_session_description(session_path)
        summary = {
            "session_id": session_id,
            "workspace_dir": str(session_path),
            "status": status,
            "source": source,
            "channel": source,  # placeholder — enriched below once run catalog info is available
            "owner": owner or "unknown",
            "memory_mode": memory_mode,
            "description": description,
            "created_at": created_at,
            "last_modified": last_modified,
            "last_activity": last_activity,
            "active_connections": active_connections,
            "active_runs": active_runs,
            "last_event_seq": int(runtime.get("last_event_seq", 0) or 0),
            "terminal_reason": runtime.get("terminal_reason"),
            "has_run_log": run_log_path.exists(),
            "has_activity_journal": journal_path.exists(),
            "has_memory": (session_path / "MEMORY.md").exists() or (session_path / "memory").exists(),
            "last_run_source": str(runtime.get("last_run_source") or ""),
            "has_context_brief": (session_path / "context_brief.md").exists(),
        }

        run_summary = self.run_catalog.find_run_for_workspace(session_path)
        if run_summary is None:
            run_summary = self.run_catalog.get_run(session_id)
        if run_summary:
            summary["run_id"] = run_summary["run_id"]
            summary["run_status"] = run_summary["status"]
            summary["run_kind"] = run_summary.get("run_kind")
            summary["trigger_source"] = run_summary.get("trigger_source")
            summary["run_policy"] = run_summary.get("run_policy")
            summary["interrupt_policy"] = run_summary.get("interrupt_policy")
            summary["attempt_count"] = int(run_summary.get("attempt_count") or 0)
            summary["latest_attempt_id"] = run_summary.get("latest_attempt_id")
            summary["canonical_attempt_id"] = run_summary.get("canonical_attempt_id")
            summary["last_success_attempt_id"] = run_summary.get("last_success_attempt_id")
            if run_summary.get("terminal_reason"):
                summary["terminal_reason"] = run_summary.get("terminal_reason")
            if run_summary.get("external_origin"):
                summary["external_origin"] = run_summary.get("external_origin")
            if run_summary.get("external_origin_id"):
                summary["external_origin_id"] = run_summary.get("external_origin_id")

        # Enrich channel classification with run catalog signals
        summary["channel"] = self._classify_channel(
            session_id,
            source,
            run_kind=str(run_summary.get("run_kind", "")) if run_summary else "",
            trigger_source=str(run_summary.get("trigger_source", "")) if run_summary else "",
            last_run_source=str(runtime.get("last_run_source", "")),
        )

        # Packet 15: checkpoint diagnostics and rehydrate readiness
        checkpoint_diag = self._read_checkpoint_diagnostics(session_path)
        summary["has_checkpoint"] = checkpoint_diag["has_checkpoint"]
        summary["checkpoint_age_seconds"] = checkpoint_diag.get("age_seconds")
        summary["checkpoint_tasks_completed"] = checkpoint_diag.get("tasks_completed", 0)
        summary["checkpoint_artifacts_count"] = checkpoint_diag.get("artifacts_count", 0)
        summary["checkpoint_original_request"] = checkpoint_diag.get("original_request")

        rehydrate_ready, rehydrate_reason = self._assess_rehydrate_readiness(
            session_path=session_path,
            has_run_log=summary["has_run_log"],
            has_checkpoint=checkpoint_diag["has_checkpoint"],
            has_memory=summary["has_memory"],
            memory_mode=memory_mode,
        )
        summary["rehydrate_ready"] = rehydrate_ready
        summary["rehydrate_reason"] = rehydrate_reason

        heartbeat_state = self._read_heartbeat_state(session_path)
        if heartbeat_state:
            summary["heartbeat_last"] = heartbeat_state.get("last_run")
            summary["heartbeat_summary"] = heartbeat_state.get("last_summary")
            
        return summary

    def list_runs(
        self,
        status_filter: str = "all",
        run_kind_filter: str = "all",
        trigger_source_filter: str = "all",
        limit: int = 1000,
        include_workspace_summary: bool = True,
    ) -> List[Dict[str, Any]]:
        runs = self.run_catalog.list_runs(limit=max(1, int(limit)))

        if status_filter != "all":
            status_norm = status_filter.strip().lower()
            runs = [
                item
                for item in runs
                if str(item.get("status", "")).strip().lower() == status_norm
            ]

        if run_kind_filter != "all":
            kind_norm = run_kind_filter.strip().lower()
            runs = [
                item
                for item in runs
                if str(item.get("run_kind", "")).strip().lower() == kind_norm
            ]

        if trigger_source_filter != "all":
            trigger_norm = trigger_source_filter.strip().lower()
            runs = [
                item
                for item in runs
                if str(item.get("trigger_source", "")).strip().lower() == trigger_norm
            ]

        summaries: List[Dict[str, Any]] = []
        for item in runs:
            enriched = dict(item)
            workspace_dir = str(item.get("workspace_dir") or "").strip()
            workspace_path = Path(workspace_dir) if workspace_dir else None
            workspace_exists = bool(
                workspace_path and workspace_path.exists() and workspace_path.is_dir()
            )
            enriched["workspace_exists"] = workspace_exists
            if include_workspace_summary and workspace_exists and workspace_path is not None:
                try:
                    enriched["workspace_summary"] = self._build_session_summary(workspace_path)
                except Exception:
                    logger.warning(
                        "Failed to enrich run summary from workspace: %s",
                        workspace_path,
                        exc_info=True,
                    )
            summaries.append(enriched)
        return summaries

    def _read_checkpoint_diagnostics(self, workspace_path: Path) -> dict:
        """Packet 15: read checkpoint file and return diagnostics."""
        checkpoint_path = None
        for checkpoint_name in ("run_checkpoint.json", "session_checkpoint.json"):
            candidate = workspace_path / checkpoint_name
            if candidate.exists():
                checkpoint_path = candidate
                break
        if checkpoint_path is None:
            return {"has_checkpoint": False}
        try:
            data = json.loads(checkpoint_path.read_text())
            age_seconds: Optional[float] = None
            ts = data.get("timestamp")
            if ts:
                try:
                    ckpt_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    age_seconds = (datetime.now(timezone.utc) - ckpt_dt).total_seconds()
                except Exception:
                    pass
            tasks = data.get("completed_tasks")
            artifacts = data.get("artifacts")
            original_request = str(data.get("original_request") or "").strip()
            return {
                "has_checkpoint": True,
                "age_seconds": age_seconds,
                "tasks_completed": len(tasks) if isinstance(tasks, list) else 0,
                "artifacts_count": len(artifacts) if isinstance(artifacts, list) else 0,
                "original_request": original_request[:200] if original_request else None,
            }
        except Exception:
            return {"has_checkpoint": False}

    @staticmethod
    def _assess_rehydrate_readiness(
        *,
        session_path: Path,
        has_run_log: bool,
        has_checkpoint: bool,
        has_memory: bool,
        memory_mode: str,
    ) -> tuple:
        """Packet 15: determine if a session can be rehydrated and why not."""
        if has_checkpoint:
            return True, "checkpoint_available"
        if has_run_log and has_memory:
            return True, "run_log_and_memory_available"
        reasons = []
        if not has_run_log:
            reasons.append("no_run_log")
        if not has_checkpoint:
            reasons.append("no_checkpoint")
        if not has_memory:
            reasons.append("no_memory_file")
        if memory_mode == "direct_only":
            reasons.append("memory_mode_direct_only")
        reason = "; ".join(reasons) if reasons else "unknown"
        return False, reason

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

    def get_run_details(self, run_id: str) -> Optional[Dict[str, Any]]:
        run = self.run_catalog.get_run(run_id)
        if not run:
            return None
        details = {"run": dict(run)}
        workspace_dir = str(run.get("workspace_dir") or "").strip()
        if workspace_dir:
            workspace_path = Path(workspace_dir)
            if workspace_path.exists() and workspace_path.is_dir():
                details["workspace"] = self._build_session_summary(workspace_path)
        return details

    def list_run_attempt_details(self, run_id: str) -> List[Dict[str, Any]]:
        conn = connect_runtime_db(get_runtime_db_path())
        try:
            ensure_schema(conn)
            rows = list_run_attempts(conn, run_id)
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def _capture_session_transition_memory(
        self,
        *,
        session_id: str,
        workspace: Path,
        trigger: str,
    ) -> dict[str, Any]:
        try:
            if not workspace.exists():
                return {"captured": False, "reason": "workspace_missing"}
            shared_root = resolve_shared_memory_workspace(str(workspace))
            broker = get_memory_orchestrator(workspace_dir=shared_root)
            summary = self._derive_session_description(workspace) or ""
            return broker.capture_session_rollover(
                session_id=session_id,
                trigger=trigger,
                transcript_path=str(workspace / "transcript.md"),
                run_log_path=str(workspace / "run.log"),
                summary=summary,
            )
        except Exception as exc:
            logger.warning(
                "Session transition memory capture failed (session=%s, trigger=%s): %s",
                session_id,
                trigger,
                exc,
            )
            return {"captured": False, "reason": "capture_error", "error": str(exc)}

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session's workspace directory and close its adapter."""
        safe_session_id = validate_session_id(session_id)
        workspace = self._session_workspace(safe_session_id)
        self._capture_session_transition_memory(
            session_id=safe_session_id,
            workspace=workspace,
            trigger="ops_delete",
        )
        await self.gateway.close_session(safe_session_id)
        if workspace.exists() and workspace.is_dir():
            shutil.rmtree(workspace)
            return True
        return False

    def update_session_description(self, session_id: str, description: str) -> bool:
        """Explicitly set a session description via description.txt"""
        safe_session_id = validate_session_id(session_id)
        workspace = self._session_workspace(safe_session_id)
        if not workspace.exists() or not workspace.is_dir():
            return False
        
        desc_path = workspace / "description.txt"
        desc_path.write_text(description.strip(), encoding="utf-8")
        return True

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
        memory_capture = self._capture_session_transition_memory(
            session_id=session_id,
            workspace=session_path,
            trigger="ops_reset",
        )
            
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

        return {
            "status": "reset",
            "archived": moved,
            "archive_dir": str(archive_dir),
            "memory_capture": memory_capture,
        }

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

    def get_session_context_brief(self, session_id: str) -> Optional[str]:
        """Read the context_brief.md for a session, if it exists."""
        session_path = self._session_workspace(session_id)
        brief_path = session_path / "context_brief.md"
        if not brief_path.exists():
            return None
        try:
            return brief_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to read context_brief.md for %s: %s", session_id, exc)
            return None

    def bulk_delete_sessions(
        self,
        older_than_days: int,
        channels: Optional[List[str]] = None,
        exclude_active: bool = True,
    ) -> Dict[str, Any]:
        """Delete session workspaces matching the given criteria.

        Args:
            older_than_days: Delete sessions older than this many days.
            channels: Only delete sessions in these channel types. If None, delete all.
            exclude_active: If True, never delete sessions that are currently running.

        Returns:
            Dict with 'deleted', 'skipped', and 'errors' counts.
        """
        import time as _time

        cutoff = _time.time() - (older_than_days * 86400)
        active_ids = set(self.gateway._sessions.keys()) if exclude_active else set()

        deleted = 0
        skipped = 0
        errors = 0

        for session_dir in sorted(self.workspaces_dir.iterdir()):
            if not session_dir.is_dir():
                continue
            if session_dir.name.startswith("."):
                continue
            if session_dir.name in active_ids:
                skipped += 1
                continue

            try:
                mtime = session_dir.stat().st_mtime
            except Exception:
                continue

            if mtime >= cutoff:
                skipped += 1
                continue

            # Channel filter
            if channels:
                source = self._infer_source(session_dir.name, None)
                channel = self._classify_channel(session_dir.name, source)
                if channel not in channels:
                    skipped += 1
                    continue

            try:
                shutil.rmtree(session_dir)
                deleted += 1
            except Exception as exc:
                logger.warning("Failed to delete session %s: %s", session_dir.name, exc)
                errors += 1

        logger.info(
            "Bulk delete: deleted=%d skipped=%d errors=%d (older_than_days=%d, channels=%s)",
            deleted, skipped, errors, older_than_days, channels,
        )
        return {"deleted": deleted, "skipped": skipped, "errors": errors}

    def get_daily_activity_digest(self, since_hours: int = 24) -> str:
        """Aggregate context_brief.md files from recent sessions into a daily digest.

        Returns a markdown document grouping session dossiers by channel type,
        suitable for incorporation into Simone's daily memory updates.
        """
        import time as _time

        cutoff = _time.time() - (since_hours * 3600)
        now_str = datetime.now(tz=timezone.utc).strftime("%B %d, %Y")

        channel_groups: Dict[str, List[tuple]] = {}  # channel -> [(session_id, brief)]

        for session_dir in sorted(self.workspaces_dir.iterdir()):
            if not session_dir.is_dir() or session_dir.name.startswith("."):
                continue

            try:
                mtime = session_dir.stat().st_mtime
            except Exception:
                continue

            if mtime < cutoff:
                continue

            brief_path = session_dir / "context_brief.md"
            if not brief_path.exists():
                continue

            try:
                brief_text = brief_path.read_text(encoding="utf-8")
            except Exception:
                continue

            source = self._infer_source(session_dir.name, None)
            channel = self._classify_channel(session_dir.name, source)

            # Skip infrastructure sessions from the digest
            if channel == "infrastructure":
                continue

            channel_groups.setdefault(channel, []).append((session_dir.name, brief_text))

        # Build the digest
        channel_labels = {
            "interactive": "💬 Interactive Chats",
            "vp_mission": "🤖 VP Missions",
            "email": "📧 Email",
            "scheduled": "⏰ Scheduled / Cron",
            "proactive": "📡 Proactive Signals",
            "discord": "🎮 Discord",
            "system": "⚙️ System",
        }

        total = sum(len(briefs) for briefs in channel_groups.values())
        lines = [
            f"# Daily Activity Digest — {now_str}",
            "",
            f"## Sessions Completed: {total}",
            "",
        ]

        for channel, label in channel_labels.items():
            briefs = channel_groups.get(channel)
            if not briefs:
                continue
            lines.append(f"### {label} ({len(briefs)})")
            lines.append("")
            for session_id, brief_text in briefs:
                lines.append(f"#### Session: `{session_id}`")
                lines.append("")
                lines.append(brief_text.strip())
                lines.append("")
                lines.append("---")
                lines.append("")

        return "\n".join(lines)
