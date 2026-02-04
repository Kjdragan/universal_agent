import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

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


async def _create_session(base_url: str, workspace_dir: Path) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{base_url}/api/v1/sessions",
            json={"user_id": "test_system_events", "workspace_dir": str(workspace_dir)},
        ) as resp:
            data = await resp.json()
            assert resp.status == 200, data
            return data["session_id"]


def test_system_event_queue_and_presence(tmp_path):
    port = _get_free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "UA_GATEWAY_PORT": str(port),
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

        workspace_dir = tmp_path / "sys_events"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        session_id = asyncio.run(_create_session(base_url, workspace_dir))

        async def _run_checks():
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{base_url}/api/v1/system/event",
                    json={
                        "session_id": session_id,
                        "event_type": "test_event",
                        "payload": {"hello": "world"},
                    },
                ) as resp:
                    payload = await resp.json()
                    assert resp.status == 200, payload
                    assert payload["count"] == 1

                async with session.get(
                    f"{base_url}/api/v1/system/events",
                    params={"session_id": session_id},
                ) as resp:
                    data = await resp.json()
                    assert resp.status == 200, data
                    assert len(data["events"]) == 1
                    assert data["events"][0]["type"] == "test_event"

                async with session.post(
                    f"{base_url}/api/v1/system/presence",
                    json={"node_id": "gateway", "status": "online", "reason": "test"},
                ) as resp:
                    presence = await resp.json()
                    assert resp.status == 200, presence
                    assert presence["presence"]["node_id"] == "gateway"

                async with session.get(f"{base_url}/api/v1/system/presence") as resp:
                    data = await resp.json()
                    assert resp.status == 200, data
                    assert len(data["nodes"]) >= 1

        asyncio.run(_run_checks())
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
