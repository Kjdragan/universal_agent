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


def test_cron_auto_run():
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
                    json={"command": "cron schedule", "every": "2s"},
                ) as resp:
                    data = await resp.json()
                    assert resp.status == 200, data
                    job_id = data["job"]["job_id"]

                # Wait for scheduler to run at least once (poll to avoid timing flakes).
                deadline = time.time() + 15
                while True:
                    async with session.get(f"{base_url}/api/v1/cron/jobs/{job_id}/runs") as resp:
                        data = await resp.json()
                        assert resp.status == 200
                        if len(data["runs"]) >= 1:
                            break
                    if time.time() > deadline:
                        raise AssertionError("Timed out waiting for cron run")
                    await asyncio.sleep(0.5)

        asyncio.run(_run_flow())
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def test_cron_one_shot_runs_and_deletes():
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
                    json={"command": "one-shot test", "run_at": "3s", "delete_after_run": True},
                ) as resp:
                    data = await resp.json()
                    assert resp.status == 200, data
                    job_id = data["job"]["job_id"]

                deadline = time.time() + 20
                saw_success = False
                saw_deleted = False
                while time.time() < deadline:
                    async with session.get(f"{base_url}/api/v1/cron/runs") as rr:
                        runs_payload = await rr.json()
                        assert rr.status == 200
                    if any(r.get("job_id") == job_id and r.get("status") == "success" for r in (runs_payload.get("runs") or [])):
                        saw_success = True

                    async with session.get(f"{base_url}/api/v1/cron/jobs") as jr:
                        jobs_payload = await jr.json()
                        assert jr.status == 200
                    if not any(j.get("job_id") == job_id for j in (jobs_payload.get("jobs") or [])):
                        saw_deleted = True

                    if saw_success and saw_deleted:
                        break
                    await asyncio.sleep(0.5)

                assert saw_success, "One-shot cron run did not complete successfully"
                assert saw_deleted, "One-shot cron job was not deleted after success"

        asyncio.run(_run_flow())
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
