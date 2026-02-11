import asyncio
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

import aiohttp

from universal_agent.cron_service import parse_run_at


def _get_free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return port


async def _wait_for_server(base_url: str, timeout: float = 20.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{base_url}/api/v1/health") as resp:
                    if resp.status == 200:
                        return True
        except Exception:
            await asyncio.sleep(0.5)
    return False


def test_cron_job_crud_and_run():
    port = _get_free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "UA_GATEWAY_PORT": str(port),
        "UA_ENABLE_CRON": "1",
        "UA_CRON_MOCK_RESPONSE": "1",
    }

    process = subprocess.Popen(
        [sys.executable, "-m", "universal_agent.gateway_server"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        ready = asyncio.run(_wait_for_server(base_url))
        assert ready, "Gateway did not start in time"

        async def _run_flow():
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{base_url}/api/v1/cron/jobs",
                    json={"command": "cron test", "every": "2s"},
                ) as resp:
                    data = await resp.json()
                    assert resp.status == 200, data
                    job_id = data["job"]["job_id"]

                async with session.get(f"{base_url}/api/v1/cron/jobs") as resp:
                    data = await resp.json()
                    assert resp.status == 200
                    assert any(job["job_id"] == job_id for job in data["jobs"])

                async with session.put(
                    f"{base_url}/api/v1/cron/jobs/{job_id}",
                    json={"enabled": False},
                ) as resp:
                    data = await resp.json()
                    assert resp.status == 200
                    assert data["job"]["enabled"] is False

                async with session.post(f"{base_url}/api/v1/cron/jobs/{job_id}/run") as resp:
                    data = await resp.json()
                    assert resp.status == 200
                    assert data["run"]["status"] == "success"

                async with session.get(f"{base_url}/api/v1/cron/jobs/{job_id}/runs") as resp:
                    data = await resp.json()
                    assert resp.status == 200
                    assert len(data["runs"]) >= 1

                async with session.delete(f"{base_url}/api/v1/cron/jobs/{job_id}") as resp:
                    data = await resp.json()
                    assert resp.status == 200
                    assert data["status"] == "deleted"

                async with session.post(
                    f"{base_url}/api/v1/cron/jobs",
                    json={
                        "command": "natural run_at test",
                        "run_at": "tomorrow 9:15am",
                        "timezone": "America/Chicago",
                        "delete_after_run": True,
                    },
                ) as resp:
                    data = await resp.json()
                    assert resp.status == 200, data
                    assert data["job"]["run_at"] is not None

                async with session.post(
                    f"{base_url}/api/v1/cron/jobs",
                    json={
                        "command": "simple one-shot",
                        "schedule_time": "in 5 minutes",
                        "repeat": False,
                        "timeout_seconds": 120,
                    },
                ) as resp:
                    data = await resp.json()
                    assert resp.status == 200, data
                    assert data["job"]["run_at"] is not None
                    assert data["job"]["delete_after_run"] is True
                    assert data["job"]["timeout_seconds"] == 120

                async with session.post(
                    f"{base_url}/api/v1/cron/jobs",
                    json={
                        "command": "simple repeating interval",
                        "schedule_time": "in 15 minutes",
                        "repeat": True,
                    },
                ) as resp:
                    data = await resp.json()
                    assert resp.status == 200, data
                    assert int(data["job"]["every_seconds"]) == 900
                    assert data["job"]["run_at"] is None

                async with session.post(
                    f"{base_url}/api/v1/cron/jobs",
                    json={
                        "command": "simple repeating daily",
                        "schedule_time": "4:30 pm",
                        "repeat": True,
                        "timezone": "America/Chicago",
                    },
                ) as resp:
                    data = await resp.json()
                    assert resp.status == 200, data
                    assert data["job"]["cron_expr"] == "30 16 * * *"
                    assert data["job"]["run_at"] is None

        asyncio.run(_run_flow())
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def test_parse_run_at_natural_language():
    now = datetime(2026, 2, 8, 6, 0, tzinfo=timezone.utc).timestamp()  # 00:00 in America/Chicago

    ts_1am = parse_run_at("1am", now=now, timezone_name="America/Chicago")
    assert ts_1am is not None
    dt_1am = datetime.fromtimestamp(ts_1am, timezone.utc)
    assert dt_1am.hour == 7 and dt_1am.minute == 0

    ts_tomorrow = parse_run_at("tomorrow 9:15am", now=now, timezone_name="America/Chicago")
    assert ts_tomorrow is not None
    dt_tomorrow = datetime.fromtimestamp(ts_tomorrow, timezone.utc)
    assert dt_tomorrow.day == 9
    assert dt_tomorrow.hour == 15 and dt_tomorrow.minute == 15

    ts_in_words = parse_run_at("in 90 minutes", now=now, timezone_name="America/Chicago")
    assert ts_in_words is not None
    assert int(ts_in_words - now) == 5400
