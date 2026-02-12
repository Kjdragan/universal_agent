import asyncio
import json
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, Iterable, Callable

import pytz
from croniter import croniter

from universal_agent.gateway import InProcessGateway, GatewayRequest, GatewaySession
from universal_agent.heartbeat_service import _parse_duration_seconds

logger = logging.getLogger(__name__)

MIN_CRON_TIMEOUT_SECONDS = 1
MAX_CRON_TIMEOUT_SECONDS = 7200

_CRON_MEDIA_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".svg",
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".mp3",
    ".wav",
    ".m4a",
    ".flac",
    ".ogg",
}
_CRON_WORK_PRODUCT_EXTENSIONS = {
    ".html",
    ".pdf",
    ".csv",
    ".json",
    ".md",
    ".txt",
    ".xlsx",
    ".xls",
    ".docx",
    ".pptx",
}
_CRON_WORKSPACE_KEEP_FILES = {
    "run.log",
    "transcript.md",
    "trace.json",
    "trace_catalog.md",
    "MEMORY.md",
}


def _normalize_timeout_seconds(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return max(MIN_CRON_TIMEOUT_SECONDS, min(MAX_CRON_TIMEOUT_SECONDS, seconds))


def _resolve_run_at_timezone(timezone_name: str | None) -> Any:
    name = (timezone_name or "").strip() or "UTC"
    try:
        return pytz.timezone(name)
    except Exception:
        local_tz = datetime.now().astimezone().tzinfo
        return local_tz or pytz.UTC


def _parse_time_of_day(raw: str) -> tuple[int, int] | None:
    text = raw.strip().lower()
    if not text:
        return None
    if text.startswith("at "):
        text = text[3:].strip()
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    meridiem = (match.group(3) or "").lower()
    if minute < 0 or minute > 59:
        return None
    if meridiem:
        if hour < 1 or hour > 12:
            return None
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
    elif hour > 23:
        return None
    return hour, minute


def _parse_natural_run_at(value: str, now_dt: datetime) -> float | None:
    text = value.strip().lower()
    if not text:
        return None

    if text in {"now", "right now", "asap"}:
        return now_dt.timestamp()

    if text.startswith("in "):
        duration_raw = text[3:].strip()
        duration = _parse_duration_seconds(duration_raw, 0)
        if duration <= 0:
            word_match = re.match(
                r"^(\d+)\s*(minute|minutes|min|hour|hours|day|days)$",
                duration_raw,
            )
            if word_match:
                amount = int(word_match.group(1))
                unit = word_match.group(2)
                if unit.startswith(("minute", "min")):
                    duration = amount * 60
                elif unit.startswith("hour"):
                    duration = amount * 3600
                elif unit.startswith("day"):
                    duration = amount * 86400
        if duration > 0:
            return now_dt.timestamp() + duration

    day_offset = 0
    explicit_day = False
    remainder = text
    if remainder.startswith("tomorrow"):
        explicit_day = True
        day_offset = 1
        remainder = remainder[len("tomorrow"):].strip()
    elif remainder.startswith("today"):
        explicit_day = True
        day_offset = 0
        remainder = remainder[len("today"):].strip()
    elif remainder.startswith("tonight"):
        explicit_day = True
        day_offset = 0
        remainder = remainder[len("tonight"):].strip()
        if not remainder:
            remainder = "8:00pm"

    time_parts = _parse_time_of_day(remainder or text)
    if not time_parts:
        return None

    hour, minute = time_parts
    candidate = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=day_offset)
    if candidate <= now_dt and not explicit_day:
        candidate += timedelta(days=1)
    elif candidate <= now_dt and explicit_day and day_offset == 0:
        candidate += timedelta(days=1)
    return candidate.timestamp()


def parse_run_at(
    value: str | float | None,
    now: float | None = None,
    timezone_name: str | None = None,
) -> float | None:
    """Parse run_at value to absolute timestamp.
    
    Accepts:
    - None: returns None
    - float: absolute timestamp (returned as-is)
    - str: relative duration like "20m", "2h", "1d"
    - str: absolute ISO timestamp (with or without timezone)
    - str: natural phrases like "1am", "tomorrow 9:15am", "in 90 minutes"
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    
    value = value.strip()
    if not value:
        return None
    
    tz = _resolve_run_at_timezone(timezone_name)
    now_ts = now if now is not None else time.time()
    now_dt = datetime.fromtimestamp(now_ts, tz)
    
    # Try relative duration first (e.g., "20m", "2h")
    duration = _parse_duration_seconds(value, 0)
    if duration > 0:
        return now_ts + duration

    # Unix timestamp as string
    if re.match(r"^\d{9,}(?:\.\d+)?$", value):
        try:
            return float(value)
        except Exception:
            pass
    
    # Try ISO timestamp
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = tz.localize(dt) if hasattr(tz, "localize") else dt.replace(tzinfo=tz)
        return dt.timestamp()
    except Exception:
        pass

    # Try natural language forms
    return _parse_natural_run_at(value, now_dt)

@dataclass
class CronJob:
    job_id: str
    user_id: str
    workspace_dir: str
    command: str
    every_seconds: int = 0  # Simple interval (mutually exclusive with cron_expr)
    cron_expr: Optional[str] = None  # 5-field cron expression (e.g., "0 7 * * 1")
    timezone: str = "UTC"  # Timezone for cron expression
    run_at: Optional[float] = None  # One-shot: absolute timestamp to run at
    delete_after_run: bool = False  # One-shot: delete job after successful run
    model: Optional[str] = None  # Model override for this job
    timeout_seconds: Optional[int] = None  # Per-job execution timeout
    enabled: bool = True
    created_at: float = field(default_factory=lambda: time.time())
    last_run_at: Optional[float] = None
    next_run_at: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "user_id": self.user_id,
            "workspace_dir": self.workspace_dir,
            "command": self.command,
            "every_seconds": self.every_seconds,
            "cron_expr": self.cron_expr,
            "timezone": self.timezone,
            "run_at": self.run_at,
            "delete_after_run": self.delete_after_run,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "last_run_at": self.last_run_at,
            "next_run_at": self.next_run_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CronJob":
        return cls(
            job_id=data["job_id"],
            user_id=data.get("user_id", f"cron:{data['job_id']}"),
            workspace_dir=data["workspace_dir"],
            command=data["command"],
            every_seconds=int(data.get("every_seconds", 0)),
            cron_expr=data.get("cron_expr"),
            timezone=data.get("timezone", "UTC"),
            run_at=data.get("run_at"),
            delete_after_run=bool(data.get("delete_after_run", False)),
            model=data.get("model"),
            timeout_seconds=_normalize_timeout_seconds(
                data.get("timeout_seconds")
                if data.get("timeout_seconds") is not None
                else data.get("metadata", {}).get("timeout_seconds")
            ),
            enabled=bool(data.get("enabled", True)),
            created_at=float(data.get("created_at", time.time())),
            last_run_at=data.get("last_run_at"),
            next_run_at=data.get("next_run_at"),
            metadata=data.get("metadata", {}),
        )

    def schedule_next(self, now: float) -> None:
        """Calculate next run time based on scheduling type."""
        # One-shot: run_at is the only run time (no rescheduling)
        if self.run_at is not None:
            if self.last_run_at is None:
                self.next_run_at = self.run_at
            else:
                # Already ran, no next run
                self.next_run_at = None
            return

        # Cron expression takes precedence over interval
        if self.cron_expr:
            try:
                tz = pytz.timezone(self.timezone)
                base_dt = datetime.fromtimestamp(now, tz)
                cron = croniter(self.cron_expr, base_dt)
                next_dt = cron.get_next(datetime)
                self.next_run_at = next_dt.timestamp()
            except Exception as exc:
                logger.warning("Invalid chron expression '%s': %s", self.cron_expr, exc)
                # Fall back to interval if cron fails
                if self.every_seconds > 0:
                    self.next_run_at = now + self.every_seconds
                else:
                    self.next_run_at = None
            return

        # Simple interval scheduling
        if self.every_seconds <= 0:
            self.next_run_at = None
            return
        base = self.last_run_at or self.created_at
        candidate = base + self.every_seconds
        if candidate <= now:
            candidate = now + self.every_seconds
        self.next_run_at = candidate



@dataclass
class CronRunRecord:
    run_id: str
    job_id: str
    status: str
    scheduled_at: Optional[float]
    started_at: float
    finished_at: Optional[float] = None
    error: Optional[str] = None
    output_preview: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "job_id": self.job_id,
            "status": self.status,
            "scheduled_at": self.scheduled_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "output_preview": self.output_preview,
        }


class CronStore:
    def __init__(self, jobs_path: Path, runs_path: Path):
        self.jobs_path = jobs_path
        self.runs_path = runs_path
        self.jobs_path.parent.mkdir(parents=True, exist_ok=True)

    def load_jobs(self) -> dict[str, CronJob]:
        if not self.jobs_path.exists():
            return {}
        try:
            payload = json.loads(self.jobs_path.read_text())
        except Exception as exc:
            logger.error("Failed to read cron jobs: %s", exc)
            return {}
        jobs = {}
        for item in payload.get("jobs", []):
            try:
                job = CronJob.from_dict(item)
                jobs[job.job_id] = job
            except Exception as exc:
                logger.warning("Skipping invalid cron job: %s", exc)
        return jobs

    def save_jobs(self, jobs: Iterable[CronJob]) -> None:
        data = {"jobs": [job.to_dict() for job in jobs]}
        self.jobs_path.write_text(json.dumps(data, indent=2))

    def append_run(self, record: CronRunRecord) -> None:
        self.runs_path.parent.mkdir(parents=True, exist_ok=True)
        with self.runs_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict()) + "\n")

    def read_runs(self, job_id: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
        if not self.runs_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            with self.runs_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        data = json.loads(line.strip())
                    except json.JSONDecodeError:
                        continue
                    if job_id and data.get("job_id") != job_id:
                        continue
                    rows.append(data)
        except Exception as exc:
            logger.error("Failed reading cron runs: %s", exc)
        return rows[-limit:]


class CronService:
    def __init__(
        self,
        gateway: InProcessGateway,
        workspaces_dir: Path,
        event_sink: Optional[Callable[[dict[str, Any]], None]] = None,
        wake_callback: Optional[Callable[[str, str, str], None]] = None,
        system_event_callback: Optional[Callable[[str, dict[str, Any]], None]] = None,
    ):
        self.gateway = gateway
        self.workspaces_dir = workspaces_dir
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self.jobs: Dict[str, CronJob] = {}
        self.running_jobs: set[str] = set()
        self.running_job_scheduled_at: dict[str, float] = {}
        self.max_concurrency = int(os.getenv("UA_CRON_MAX_CONCURRENCY", "1"))
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self.event_sink = event_sink
        self.wake_callback = wake_callback
        self.system_event_callback = system_event_callback

        jobs_path = workspaces_dir / "cron_jobs.json"
        runs_path = workspaces_dir / "cron_runs.jsonl"
        self.store = CronStore(jobs_path, runs_path)
        self.jobs = self.store.load_jobs()
        for job in self.jobs.values():
            if job.next_run_at is None:
                job.schedule_next(time.time())

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.task = asyncio.create_task(self._scheduler_loop())
        logger.info("⏱️ Chron service started (%d jobs)", len(self.jobs))

    async def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 Chron service stopped")

    def list_jobs(self) -> list[CronJob]:
        return list(self.jobs.values())

    def get_job(self, job_id: str) -> Optional[CronJob]:
        return self.jobs.get(job_id)

    def add_job(
        self,
        user_id: str,
        workspace_dir: Optional[str],
        command: str,
        every_raw: Optional[str] = None,
        cron_expr: Optional[str] = None,
        timezone: str = "UTC",
        run_at: Optional[float] = None,
        delete_after_run: bool = False,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        enabled: bool = True,
        metadata: Optional[dict[str, Any]] = None,
    ) -> CronJob:
        every_seconds = _parse_duration_seconds(every_raw, 0) if every_raw else 0
        
        # Validate scheduling - must have at least one method
        if every_seconds <= 0 and not cron_expr and run_at is None:
            raise ValueError("Must provide at least one of: every, cron_expr, or run_at")
        
        # Validate cron expression if provided
        if cron_expr:
            try:
                croniter(cron_expr)
            except Exception as exc:
                raise ValueError(f"Invalid chron expression '{cron_expr}': {exc}")
        
        # Validate timezone
        try:
            pytz.timezone(timezone)
        except Exception:
            raise ValueError(f"Invalid timezone '{timezone}'")
        
        job_id = uuid.uuid4().hex[:10]
        workspace = workspace_dir or str(self.workspaces_dir / f"cron_{job_id}")
        job = CronJob(
            job_id=job_id,
            user_id=user_id or f"cron:{job_id}",
            workspace_dir=workspace,
            command=command,
            every_seconds=every_seconds,
            cron_expr=cron_expr,
            timezone=timezone,
            run_at=run_at,
            delete_after_run=delete_after_run,
            model=model,
            timeout_seconds=_normalize_timeout_seconds(timeout_seconds),
            enabled=enabled,
            metadata=metadata or {},
        )
        job.schedule_next(time.time())
        self.jobs[job_id] = job
        self.store.save_jobs(self.jobs.values())
        self._emit_event({"type": "cron_job_created", "job": job.to_dict()})
        return job

    def update_job(self, job_id: str, updates: dict[str, Any]) -> CronJob:
        job = self.jobs[job_id]
        if "command" in updates:
            job.command = updates["command"]
        if "enabled" in updates:
            job.enabled = bool(updates["enabled"])
        if "every" in updates or "every_seconds" in updates:
            raw = updates.get("every") or updates.get("every_seconds")
            job.every_seconds = _parse_duration_seconds(str(raw), job.every_seconds)
        if "cron_expr" in updates:
            cron_expr = updates["cron_expr"]
            if cron_expr:
                try:
                    croniter(cron_expr)
                except Exception as exc:
                    raise ValueError(f"Invalid chron expression '{cron_expr}': {exc}")
            job.cron_expr = cron_expr
        if "timezone" in updates:
            tz = updates["timezone"]
            try:
                pytz.timezone(tz)
            except Exception:
                raise ValueError(f"Invalid timezone '{tz}'")
            job.timezone = tz
        if "run_at" in updates:
            job.run_at = updates["run_at"]
        if "delete_after_run" in updates:
            job.delete_after_run = bool(updates["delete_after_run"])
        if "model" in updates:
            job.model = updates["model"]
        if "timeout_seconds" in updates:
            job.timeout_seconds = _normalize_timeout_seconds(updates["timeout_seconds"])
        if "workspace_dir" in updates:
            job.workspace_dir = updates["workspace_dir"]
        if "user_id" in updates:
            job.user_id = updates["user_id"]
        if "metadata" in updates and isinstance(updates["metadata"], dict):
            job.metadata.update(updates["metadata"])
        job.schedule_next(time.time())
        self.store.save_jobs(self.jobs.values())
        self._emit_event({"type": "cron_job_updated", "job": job.to_dict()})
        return job

    def delete_job(self, job_id: str) -> None:
        if job_id in self.jobs:
            self._emit_event({"type": "cron_job_deleted", "job_id": job_id})
            del self.jobs[job_id]
            self.store.save_jobs(self.jobs.values())

    def list_runs(self, job_id: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
        return self.store.read_runs(job_id=job_id, limit=limit)

    async def run_job_now(
        self,
        job_id: str,
        reason: str = "manual",
        scheduled_at: Optional[float] = None,
    ) -> CronRunRecord:
        job = self.jobs[job_id]
        return await self._run_job(job, scheduled_at=scheduled_at, reason=reason)

    async def _scheduler_loop(self) -> None:
        while self.running:
            now = time.time()
            for job in list(self.jobs.values()):
                if not job.enabled:
                    continue
                if job.job_id in self.running_jobs:
                    # Prevent repeated "already running" skips while a prior dispatch is active.
                    continue
                if job.next_run_at is None:
                    job.schedule_next(now)
                if job.next_run_at and now >= job.next_run_at:
                    scheduled_at = float(job.next_run_at)
                    if job.run_at is not None:
                        # One-shot jobs should only be consumed after a real run attempt starts.
                        # Keep them retriable across short restarts by moving next_run forward
                        # until _run_job finalizes state.
                        job.next_run_at = now + 5.0
                    else:
                        job.last_run_at = now
                        job.schedule_next(now)
                    self.store.save_jobs(self.jobs.values())
                    asyncio.create_task(self._run_job(job, scheduled_at=scheduled_at, reason="schedule"))
            await asyncio.sleep(1)

    async def _run_job(self, job: CronJob, scheduled_at: Optional[float], reason: str) -> CronRunRecord:
        if job.job_id in self.running_jobs:
            logger.info("Chron job %s already running, skipping", job.job_id)
            record = CronRunRecord(
                run_id=uuid.uuid4().hex[:12],
                job_id=job.job_id,
                status="skipped",
                scheduled_at=scheduled_at,
                started_at=time.time(),
                finished_at=time.time(),
                error="already running",
            )
            self.store.append_run(record)
            return record

        async with self._semaphore:
            self.running_jobs.add(job.job_id)
            record = CronRunRecord(
                run_id=uuid.uuid4().hex[:12],
                job_id=job.job_id,
                status="running",
                scheduled_at=scheduled_at,
                started_at=time.time(),
            )
            self._emit_event({"type": "cron_run_started", "run": record.to_dict(), "reason": reason})
            timeout_seconds = self._resolve_job_timeout_seconds(job)
            scheduled_marker = float(scheduled_at) if scheduled_at is not None else float(record.started_at)
            self.running_job_scheduled_at[job.job_id] = scheduled_marker
            try:
                if os.getenv("UA_CRON_MOCK_RESPONSE", "0").lower() in {"1", "true", "yes"}:
                    record.status = "success"
                    record.finished_at = time.time()
                    record.output_preview = "CRON_OK"
                else:
                    session = await self.gateway.create_session(
                        user_id=job.user_id,
                        workspace_dir=job.workspace_dir,
                    )
                    # Build request metadata with optional model override
                    request_metadata: dict[str, Any] = {
                        "source": "cron",
                        "job_id": job.job_id,
                        "reason": reason,
                    }
                    if job.model:
                        request_metadata["model"] = job.model
                    
                    request = GatewayRequest(
                        user_input=job.command,
                        force_complex=False,
                        metadata=request_metadata,
                    )
                    run_coro = self.gateway.run_query(session, request)
                    if timeout_seconds is not None:
                        result = await asyncio.wait_for(run_coro, timeout=timeout_seconds)
                    else:
                        result = await run_coro
                    record.status = "success"
                    record.finished_at = time.time()
                    record.output_preview = (result.response_text or "")[:400]
            except asyncio.TimeoutError:
                record.status = "error"
                record.finished_at = time.time()
                timeout_label = timeout_seconds if timeout_seconds is not None else "configured"
                record.error = f"execution timed out after {timeout_label}s"
                logger.error("Chron job %s timed out after %ss", job.job_id, timeout_label)
            except Exception as exc:
                record.status = "error"
                record.finished_at = time.time()
                record.error = str(exc)
                logger.error("Chron job %s failed: %s", job.job_id, exc)
            finally:
                self.running_jobs.discard(job.job_id)
                self.running_job_scheduled_at.pop(job.job_id, None)
                moved_outputs = self._organize_workspace_outputs(job.workspace_dir)
                # Finalize one-shot schedule consumption only after run actually started.
                if reason == "schedule" and job.run_at is not None:
                    job.last_run_at = record.started_at or time.time()
                    job.schedule_next(job.last_run_at)
                    self.store.save_jobs(self.jobs.values())
                self.store.append_run(record)
                self._emit_event({"type": "cron_run_completed", "run": record.to_dict(), "reason": reason})
                if moved_outputs:
                    logger.info(
                        "Chron job %s moved %d root output(s) into work_products: %s",
                        job.job_id,
                        len(moved_outputs),
                        ", ".join(moved_outputs[:5]),
                    )
                
                # Enqueue system event for target session (if wake_heartbeat configured)
                metadata = job.metadata or {}
                target_session = metadata.get("target_session") or metadata.get("session_id")
                if target_session:
                    event_text = f"Chron job '{job.command[:50]}...' completed with status={record.status}"
                    if record.error:
                        event_text += f", error: {record.error[:100]}"
                    elif record.output_preview:
                        event_text += f", output: {record.output_preview[:100]}..."
                    self._emit_system_event(target_session, {
                        "type": "cron_completed",
                        "job_id": job.job_id,
                        "status": record.status,
                        "output_preview": record.output_preview,
                        "error": record.error,
                        "text": event_text,
                    })
                
                self._maybe_wake_heartbeat(job, reason)
                
                # One-shot: delete job after successful run if configured
                if job.delete_after_run and record.status == "success":
                    logger.info("Deleting one-shot chron job %s after successful run", job.job_id)
                    self.delete_job(job.job_id)
            return record

    def _organize_workspace_outputs(self, workspace_dir: str) -> list[str]:
        """Move common deliverables from workspace root into work_products."""
        try:
            workspace = Path(workspace_dir).resolve()
            if not workspace.exists():
                return []

            work_products_dir = workspace / "work_products"
            media_dir = work_products_dir / "media"
            work_products_dir.mkdir(parents=True, exist_ok=True)
            media_dir.mkdir(parents=True, exist_ok=True)

            moved: list[str] = []
            for entry in workspace.iterdir():
                if not entry.is_file():
                    continue
                if entry.name.startswith("."):
                    continue
                if entry.name in _CRON_WORKSPACE_KEEP_FILES:
                    continue
                suffix = entry.suffix.lower()
                if suffix in _CRON_MEDIA_EXTENSIONS:
                    target = self._dedupe_destination(media_dir / entry.name)
                elif suffix in _CRON_WORK_PRODUCT_EXTENSIONS:
                    target = self._dedupe_destination(work_products_dir / entry.name)
                else:
                    continue
                shutil.move(str(entry), str(target))
                moved.append(str(target.relative_to(workspace)))
            return moved
        except Exception as exc:
            logger.warning("Failed organizing chron workspace outputs for %s: %s", workspace_dir, exc)
            return []

    def _dedupe_destination(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        for index in range(2, 1000):
            candidate = parent / f"{stem}_{index}{suffix}"
            if not candidate.exists():
                return candidate
        return parent / f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"

    def _resolve_job_timeout_seconds(self, job: CronJob) -> Optional[int]:
        if job.timeout_seconds is not None:
            return _normalize_timeout_seconds(job.timeout_seconds)
        metadata = job.metadata or {}
        return _normalize_timeout_seconds(metadata.get("timeout_seconds"))

    def _emit_event(self, payload: dict[str, Any]) -> None:
        if not self.event_sink:
            return
        try:
            self.event_sink(payload)
        except Exception as exc:
            logger.warning("Chron event sink failed: %s", exc)

    def _emit_system_event(self, session_id: str, event: dict[str, Any]) -> None:
        """Enqueue a system event for the given session (surfaced in next heartbeat)."""
        if not self.system_event_callback:
            return
        try:
            self.system_event_callback(session_id, event)
        except Exception as exc:
            logger.warning("Chron system event callback failed: %s", exc)

    def _maybe_wake_heartbeat(self, job: CronJob, reason: str) -> None:
        if not self.wake_callback:
            return
        metadata = job.metadata or {}
        trigger = metadata.get("wake_heartbeat") or metadata.get("heartbeat_wake")
        session_id = (
            metadata.get("wake_session_id")
            or metadata.get("session_id")
            or metadata.get("target_session_id")
            or metadata.get("target_session")
        )
        if not session_id:
            return
        mode = "next"
        if isinstance(trigger, bool):
            if not trigger:
                return
            mode = str(metadata.get("wake_mode") or mode)
        elif isinstance(trigger, str):
            trigger_mode = trigger.strip().lower()
            if trigger_mode in {"", "0", "false", "off", "none", "disable", "disabled"}:
                return
            if trigger_mode in {"auto", "default"}:
                mode = "next"
            else:
                mode = trigger_mode
        elif trigger is None:
            # For session-bound cron jobs, wake next heartbeat by default so due-work
            # transitions reliably surface in the target session without extra polling.
            mode = str(metadata.get("wake_mode") or mode)
        else:
            mode = str(metadata.get("wake_mode") or mode)
        mode = mode.strip().lower()
        if mode not in {"now", "next"}:
            mode = "next"
        try:
            self.wake_callback(session_id, mode, f"cron:{job.job_id}:{reason}")
        except Exception as exc:
            logger.warning("Chron heartbeat wake failed for %s: %s", job.job_id, exc)
