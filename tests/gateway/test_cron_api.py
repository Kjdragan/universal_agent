import asyncio
import os
import socket
import subprocess
import sys
import time

import aiohttp


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

        asyncio.run(_run_flow())
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
