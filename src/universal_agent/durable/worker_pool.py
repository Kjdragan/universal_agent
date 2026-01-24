"""
Worker Pool Manager for Distributed Execution with Durable Leases.

Stage 6 implementation: Enables multiple workers to process runs with
lease-based coordination and automatic failover.

Architecture:
- Workers register with unique IDs and heartbeat to stay alive
- Runs are queued and assigned to workers via lease acquisition
- If a worker dies (lease expires), another worker can take over
- Progress is checkpointed so work can resume from last known state
"""

import asyncio
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Awaitable

from .state import (
    acquire_run_lease,
    heartbeat_run_lease,
    release_run_lease,
    list_runs_with_status,
    get_run,
    update_run_status,
)
from .db import connect_runtime_db

logger = logging.getLogger(__name__)


class WorkerStatus(str, Enum):
    IDLE = "idle"
    PROCESSING = "processing"
    DRAINING = "draining"
    STOPPED = "stopped"


@dataclass
class WorkerConfig:
    """Configuration for a worker in the pool."""
    worker_id: str = field(default_factory=lambda: f"worker_{uuid.uuid4().hex[:8]}")
    lease_ttl_seconds: int = 60
    heartbeat_interval_seconds: int = 15
    poll_interval_seconds: int = 5
    max_concurrent_runs: int = 1
    gateway_url: Optional[str] = None  # If set, use external gateway


@dataclass
class PoolConfig:
    """Configuration for the worker pool."""
    db_path: str = "runtime.db"
    min_workers: int = 1
    max_workers: int = 4
    scale_up_threshold: int = 5  # Queue size to trigger scale up
    scale_down_idle_seconds: int = 300  # Idle time before scale down


@dataclass
class WorkerState:
    """Runtime state of a worker."""
    worker_id: str
    status: WorkerStatus = WorkerStatus.IDLE
    current_run_id: Optional[str] = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_heartbeat: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    runs_completed: int = 0
    runs_failed: int = 0


class Worker:
    """
    A single worker that processes runs from the queue.
    
    Uses lease-based coordination to ensure only one worker
    processes each run at a time.
    """

    def __init__(
        self,
        config: WorkerConfig,
        conn: sqlite3.Connection,
        run_handler: Callable[[str, str], Awaitable[bool]],
    ):
        self.config = config
        self.conn = conn
        self.run_handler = run_handler
        self.state = WorkerState(worker_id=config.worker_id)
        self._shutdown_event = asyncio.Event()
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._process_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the worker."""
        logger.info(f"Worker {self.config.worker_id} starting...")
        self.state.status = WorkerStatus.IDLE
        
        # Start background tasks
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._process_task = asyncio.create_task(self._process_loop())
        
        logger.info(f"Worker {self.config.worker_id} started")

    async def stop(self, drain: bool = True) -> None:
        """Stop the worker, optionally draining current work first."""
        logger.info(f"Worker {self.config.worker_id} stopping (drain={drain})...")
        
        if drain:
            self.state.status = WorkerStatus.DRAINING
            # Wait for current run to complete
            while self.state.current_run_id:
                await asyncio.sleep(1)
        
        self._shutdown_event.set()
        self.state.status = WorkerStatus.STOPPED
        
        # Cancel background tasks
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        
        if self._process_task:
            self._process_task.cancel()
            try:
                await self._process_task
            except asyncio.CancelledError:
                pass
        
        # Release any held leases
        if self.state.current_run_id:
            release_run_lease(self.conn, self.state.current_run_id, self.config.worker_id)
        
        logger.info(f"Worker {self.config.worker_id} stopped")

    async def _heartbeat_loop(self) -> None:
        """Periodically send heartbeats to maintain lease."""
        while not self._shutdown_event.is_set():
            try:
                if self.state.current_run_id:
                    success = heartbeat_run_lease(
                        self.conn,
                        self.state.current_run_id,
                        self.config.worker_id,
                        self.config.lease_ttl_seconds,
                    )
                    if not success:
                        logger.warning(
                            f"Worker {self.config.worker_id} lost lease for run {self.state.current_run_id}"
                        )
                        self.state.current_run_id = None
                    else:
                        self.state.last_heartbeat = datetime.now(timezone.utc)
                
                await asyncio.sleep(self.config.heartbeat_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {self.config.worker_id} heartbeat error: {e}")
                await asyncio.sleep(5)

    async def _process_loop(self) -> None:
        """Main loop: poll for work, acquire lease, process, release."""
        while not self._shutdown_event.is_set():
            try:
                if self.state.status == WorkerStatus.DRAINING:
                    await asyncio.sleep(1)
                    continue
                
                # Poll for queued runs
                queued_runs = list_runs_with_status(self.conn, ["queued"], limit=10)
                
                for run_row in queued_runs:
                    if self._shutdown_event.is_set():
                        break
                    
                    run_id = run_row["run_id"]
                    
                    # Try to acquire lease
                    acquired = acquire_run_lease(
                        self.conn,
                        run_id,
                        self.config.worker_id,
                        self.config.lease_ttl_seconds,
                    )
                    
                    if acquired:
                        logger.info(f"Worker {self.config.worker_id} acquired run {run_id}")
                        self.state.current_run_id = run_id
                        self.state.status = WorkerStatus.PROCESSING
                        
                        try:
                            # Get run details
                            run = get_run(self.conn, run_id)
                            workspace_dir = run["run_spec_json"] if run else "{}"
                            
                            # Process the run
                            success = await self.run_handler(run_id, workspace_dir)
                            
                            if success:
                                update_run_status(self.conn, run_id, "completed")
                                self.state.runs_completed += 1
                                logger.info(f"Worker {self.config.worker_id} completed run {run_id}")
                            else:
                                update_run_status(self.conn, run_id, "failed")
                                self.state.runs_failed += 1
                                logger.warning(f"Worker {self.config.worker_id} failed run {run_id}")
                        
                        except Exception as e:
                            logger.error(f"Worker {self.config.worker_id} error processing run {run_id}: {e}")
                            update_run_status(self.conn, run_id, "failed")
                            self.state.runs_failed += 1
                        
                        finally:
                            release_run_lease(self.conn, run_id, self.config.worker_id)
                            self.state.current_run_id = None
                            self.state.status = WorkerStatus.IDLE
                        
                        break  # Process one run at a time
                
                await asyncio.sleep(self.config.poll_interval_seconds)
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {self.config.worker_id} process loop error: {e}")
                await asyncio.sleep(5)


class WorkerPoolManager:
    """
    Manages a pool of workers for distributed run execution.
    
    Features:
    - Dynamic scaling based on queue depth
    - Health monitoring with automatic worker restart
    - Graceful shutdown with work draining
    - Integration with gateway for execution
    """

    def __init__(
        self,
        pool_config: PoolConfig,
        worker_config: Optional[WorkerConfig] = None,
        run_handler: Optional[Callable[[str, str], Awaitable[bool]]] = None,
    ):
        self.pool_config = pool_config
        self.worker_config_template = worker_config or WorkerConfig()
        self.run_handler = run_handler or self._default_run_handler
        self.workers: Dict[str, Worker] = {}
        self.conn: Optional[sqlite3.Connection] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        """Start the worker pool."""
        logger.info("Worker pool starting...")
        
        # Get database connection
        self.conn = connect_runtime_db(self.pool_config.db_path)
        
        # Start minimum number of workers
        for i in range(self.pool_config.min_workers):
            await self._spawn_worker()
        
        # Start monitor task
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        
        logger.info(f"Worker pool started with {len(self.workers)} workers")

    async def stop(self, drain: bool = True) -> None:
        """Stop the worker pool."""
        logger.info("Worker pool stopping...")
        self._shutdown_event.set()
        
        # Stop monitor
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        
        # Stop all workers
        stop_tasks = [worker.stop(drain=drain) for worker in self.workers.values()]
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)
        
        self.workers.clear()
        logger.info("Worker pool stopped")

    async def _spawn_worker(self) -> Worker:
        """Spawn a new worker."""
        config = WorkerConfig(
            worker_id=f"worker_{uuid.uuid4().hex[:8]}",
            lease_ttl_seconds=self.worker_config_template.lease_ttl_seconds,
            heartbeat_interval_seconds=self.worker_config_template.heartbeat_interval_seconds,
            poll_interval_seconds=self.worker_config_template.poll_interval_seconds,
            max_concurrent_runs=self.worker_config_template.max_concurrent_runs,
            gateway_url=self.worker_config_template.gateway_url,
        )
        
        worker = Worker(config, self.conn, self.run_handler)
        await worker.start()
        self.workers[config.worker_id] = worker
        
        logger.info(f"Spawned worker {config.worker_id}")
        return worker

    async def _remove_worker(self, worker_id: str) -> None:
        """Remove and stop a worker."""
        if worker_id in self.workers:
            worker = self.workers.pop(worker_id)
            await worker.stop(drain=True)
            logger.info(f"Removed worker {worker_id}")

    async def _monitor_loop(self) -> None:
        """Monitor queue depth and worker health, scale as needed."""
        while not self._shutdown_event.is_set():
            try:
                # Check queue depth
                queued_runs = list_runs_with_status(self.conn, ["queued"], limit=100)
                queue_depth = len(queued_runs)
                
                active_workers = len([
                    w for w in self.workers.values()
                    if w.state.status in (WorkerStatus.IDLE, WorkerStatus.PROCESSING)
                ])
                
                # Scale up if needed
                if (
                    queue_depth >= self.pool_config.scale_up_threshold
                    and active_workers < self.pool_config.max_workers
                ):
                    await self._spawn_worker()
                    logger.info(f"Scaled up to {len(self.workers)} workers (queue={queue_depth})")
                
                # Scale down if idle
                elif queue_depth == 0 and active_workers > self.pool_config.min_workers:
                    idle_workers = [
                        w for w in self.workers.values()
                        if w.state.status == WorkerStatus.IDLE
                        and (datetime.now(timezone.utc) - w.state.last_heartbeat).total_seconds()
                        > self.pool_config.scale_down_idle_seconds
                    ]
                    if idle_workers:
                        await self._remove_worker(idle_workers[0].config.worker_id)
                        logger.info(f"Scaled down to {len(self.workers)} workers")
                
                # Health check: restart dead workers
                for worker_id, worker in list(self.workers.items()):
                    if worker.state.status == WorkerStatus.STOPPED:
                        await self._remove_worker(worker_id)
                        if len(self.workers) < self.pool_config.min_workers:
                            await self._spawn_worker()
                
                await asyncio.sleep(10)
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker pool monitor error: {e}")
                await asyncio.sleep(10)

    async def _default_run_handler(self, run_id: str, workspace_dir: str) -> bool:
        """Default run handler using gateway."""
        try:
            from universal_agent.gateway import InProcessGateway, ExternalGateway, GatewayRequest
            
            gateway_url = self.worker_config_template.gateway_url
            if gateway_url:
                gateway = ExternalGateway(base_url=gateway_url)
            else:
                gateway = InProcessGateway()
            
            # Get run details
            run = get_run(self.conn, run_id)
            if not run:
                logger.error(f"Run {run_id} not found")
                return False
            
            import json
            run_spec = json.loads(run["run_spec_json"] or "{}")
            job_prompt = run.get("last_job_prompt") or run_spec.get("prompt", "")
            
            if not job_prompt:
                logger.error(f"Run {run_id} has no prompt")
                return False
            
            # Create session and execute
            session = await gateway.create_session(
                user_id=f"worker_{run_id}",
                workspace_dir=run_spec.get("workspace_dir"),
            )
            
            request = GatewayRequest(user_input=job_prompt)
            result = await gateway.run_query(session, request)
            
            logger.info(f"Run {run_id} completed: {result.tool_calls} tool calls")
            return True
        
        except Exception as e:
            logger.error(f"Run handler error for {run_id}: {e}")
            return False

    def get_pool_stats(self) -> Dict[str, Any]:
        """Get current pool statistics."""
        return {
            "total_workers": len(self.workers),
            "idle_workers": len([w for w in self.workers.values() if w.state.status == WorkerStatus.IDLE]),
            "processing_workers": len([w for w in self.workers.values() if w.state.status == WorkerStatus.PROCESSING]),
            "total_completed": sum(w.state.runs_completed for w in self.workers.values()),
            "total_failed": sum(w.state.runs_failed for w in self.workers.values()),
            "workers": {
                w.config.worker_id: {
                    "status": w.state.status.value,
                    "current_run": w.state.current_run_id,
                    "completed": w.state.runs_completed,
                    "failed": w.state.runs_failed,
                }
                for w in self.workers.values()
            },
        }


def queue_run(
    conn: sqlite3.Connection,
    run_id: str,
    prompt: str,
    workspace_dir: Optional[str] = None,
    max_iterations: Optional[int] = None,
) -> None:
    """Queue a run for processing by the worker pool."""
    from .state import upsert_run
    
    run_spec = {
        "prompt": prompt,
        "workspace_dir": workspace_dir,
    }
    
    upsert_run(
        conn,
        run_id=run_id,
        entrypoint="worker_pool",
        run_spec=run_spec,
        status="queued",
        last_job_prompt=prompt,
        max_iterations=max_iterations,
    )
    
    logger.info(f"Queued run {run_id}")


async def run_worker_pool(
    db_path: str = "runtime.db",
    min_workers: int = 1,
    max_workers: int = 4,
    gateway_url: Optional[str] = None,
) -> None:
    """Convenience function to run the worker pool."""
    pool_config = PoolConfig(
        db_path=db_path,
        min_workers=min_workers,
        max_workers=max_workers,
    )
    worker_config = WorkerConfig(gateway_url=gateway_url)
    
    pool = WorkerPoolManager(pool_config, worker_config)
    
    try:
        await pool.start()
        
        # Wait for shutdown signal
        while True:
            await asyncio.sleep(60)
            stats = pool.get_pool_stats()
            logger.info(f"Pool stats: {stats}")
    
    except asyncio.CancelledError:
        pass
    finally:
        await pool.stop()
