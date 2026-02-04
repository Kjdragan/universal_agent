import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Optional

from universal_agent.durable.db import connect_runtime_db
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import (
    acquire_run_lease,
    heartbeat_run_lease,
    list_runs_with_status,
    release_run_lease,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _build_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", "src")
    env.setdefault("UV_CACHE_DIR", os.path.join(_repo_root(), ".uv-cache"))
    return env


def _run_once(
    lease_owner: str, lease_ttl_seconds: int, heartbeat_seconds: int
) -> bool:
    conn = connect_runtime_db()
    ensure_schema(conn)
    candidates = list_runs_with_status(conn, ("queued", "running"), limit=10)
    if not candidates:
        return False
    for row in candidates:
        run_id = row["run_id"]
        if not acquire_run_lease(conn, run_id, lease_owner, lease_ttl_seconds):
            continue
        print(f"✅ Acquired lease for run {run_id}")
        env = _build_env()
        command = [
            sys.executable,
            "-m",
            "universal_agent.main",
            "--resume",
            "--run-id",
            run_id,
        ]
        process = subprocess.Popen(command, cwd=_repo_root(), env=env)
        try:
            while process.poll() is None:
                heartbeat_run_lease(conn, run_id, lease_owner, lease_ttl_seconds)
                time.sleep(heartbeat_seconds)
        finally:
            release_run_lease(conn, run_id, lease_owner)
        exit_code = process.returncode
        print(f"✅ Run {run_id} completed with exit code {exit_code}")
        return True
    return False


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Universal Agent worker")
    parser.add_argument("--poll", action="store_true", help="Poll for runs continuously")
    parser.add_argument("--once", action="store_true", help="Process a single run then exit")
    parser.add_argument(
        "--poll-interval-sec",
        type=int,
        default=_get_env_int("UA_WORKER_POLL_SEC", 5),
        help="Seconds between polls when idle",
    )
    parser.add_argument(
        "--heartbeat-sec",
        type=int,
        default=_get_env_int("UA_WORKER_HEARTBEAT_SEC", 10),
        help="Seconds between lease heartbeats",
    )
    parser.add_argument(
        "--lease-ttl-sec",
        type=int,
        default=_get_env_int("UA_WORKER_LEASE_TTL_SEC", 30),
        help="Lease TTL in seconds",
    )
    args = parser.parse_args(argv)

    lease_owner = f"{os.uname().nodename}:{os.getpid()}:{_now_iso()}"
    should_poll = args.poll or not args.once

    while True:
        claimed = _run_once(lease_owner, args.lease_ttl_sec, args.heartbeat_sec)
        if args.once:
            break
        if not should_poll:
            break
        if not claimed:
            time.sleep(args.poll_interval_sec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
