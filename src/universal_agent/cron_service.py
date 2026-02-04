import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, Iterable, Callable

from universal_agent.gateway import InProcessGateway, GatewayRequest, GatewaySession
from universal_agent.heartbeat_service import _parse_duration_seconds

logger = logging.getLogger(__name__)


@dataclass
class CronJob:
    job_id: str
    user_id: str
    workspace_dir: str
    command: str
    every_seconds: int
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
            enabled=bool(data.get("enabled", True)),
            created_at=float(data.get("created_at", time.time())),
            last_run_at=data.get("last_run_at"),
            next_run_at=data.get("next_run_at"),
            metadata=data.get("metadata", {}),
        )

    def schedule_next(self, now: float) -> None:
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
    ):
        self.gateway = gateway
        self.workspaces_dir = workspaces_dir
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self.jobs: Dict[str, CronJob] = {}
        self.running_jobs: set[str] = set()
        self.max_concurrency = int(os.getenv("UA_CRON_MAX_CONCURRENCY", "1"))
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self.event_sink = event_sink
        self.wake_callback = wake_callback

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
        logger.info("⏱️ Cron service started (%d jobs)", len(self.jobs))

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
        logger.info("🛑 Cron service stopped")

    def list_jobs(self) -> list[CronJob]:
        return list(self.jobs.values())

    def get_job(self, job_id: str) -> Optional[CronJob]:
        return self.jobs.get(job_id)

    def add_job(
        self,
        user_id: str,
        workspace_dir: Optional[str],
        command: str,
        every_raw: Optional[str],
        enabled: bool = True,
        metadata: Optional[dict[str, Any]] = None,
    ) -> CronJob:
        every_seconds = _parse_duration_seconds(every_raw, 0)
        if every_seconds <= 0:
            raise ValueError("every_seconds must be > 0")
        job_id = uuid.uuid4().hex[:10]
        workspace = workspace_dir or str(self.workspaces_dir / f"cron_{job_id}")
        job = CronJob(
            job_id=job_id,
            user_id=user_id or f"cron:{job_id}",
            workspace_dir=workspace,
            command=command,
            every_seconds=every_seconds,
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

    async def run_job_now(self, job_id: str, reason: str = "manual") -> CronRunRecord:
        job = self.jobs[job_id]
        return await self._run_job(job, scheduled_at=None, reason=reason)

    async def _scheduler_loop(self) -> None:
        while self.running:
            now = time.time()
            for job in list(self.jobs.values()):
                if not job.enabled:
                    continue
                if job.next_run_at is None:
                    job.schedule_next(now)
                if job.next_run_at and now >= job.next_run_at:
                    job.last_run_at = now
                    job.schedule_next(now)
                    self.store.save_jobs(self.jobs.values())
                    asyncio.create_task(self._run_job(job, scheduled_at=now, reason="schedule"))
            await asyncio.sleep(1)

    async def _run_job(self, job: CronJob, scheduled_at: Optional[float], reason: str) -> CronRunRecord:
        if job.job_id in self.running_jobs:
            logger.info("Cron job %s already running, skipping", job.job_id)
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
                    request = GatewayRequest(
                        user_input=job.command,
                        force_complex=False,
                        metadata={"source": "cron", "job_id": job.job_id, "reason": reason},
                    )
                    result = await self.gateway.run_query(session, request)
                    record.status = "success"
                    record.finished_at = time.time()
                    record.output_preview = (result.response_text or "")[:400]
            except Exception as exc:
                record.status = "error"
                record.finished_at = time.time()
                record.error = str(exc)
                logger.error("Cron job %s failed: %s", job.job_id, exc)
            finally:
                self.running_jobs.discard(job.job_id)
                self.store.append_run(record)
                self._emit_event({"type": "cron_run_completed", "run": record.to_dict(), "reason": reason})
                self._maybe_wake_heartbeat(job, reason)
            return record

    def _emit_event(self, payload: dict[str, Any]) -> None:
        if not self.event_sink:
            return
        try:
            self.event_sink(payload)
        except Exception as exc:
            logger.warning("Cron event sink failed: %s", exc)

    def _maybe_wake_heartbeat(self, job: CronJob, reason: str) -> None:
        if not self.wake_callback:
            return
        metadata = job.metadata or {}
        trigger = metadata.get("wake_heartbeat") or metadata.get("heartbeat_wake")
        if not trigger:
            return
        session_id = (
            metadata.get("wake_session_id")
            or metadata.get("session_id")
            or metadata.get("target_session_id")
        )
        if not session_id:
            logger.warning("Cron job %s requested heartbeat wake but no session_id provided", job.job_id)
            return
        mode = "next"
        if isinstance(trigger, str):
            mode = trigger
        elif metadata.get("wake_mode"):
            mode = str(metadata.get("wake_mode"))
        mode = mode.strip().lower()
        if mode not in {"now", "next"}:
            mode = "next"
        try:
            self.wake_callback(session_id, mode, f"cron:{job.job_id}:{reason}")
        except Exception as exc:
            logger.warning("Cron heartbeat wake failed for %s: %s", job.job_id, exc)
