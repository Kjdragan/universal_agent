import asyncio
import json
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


async def _run_heartbeat_flow(base_url: str, ws_url: str, workspace_dir: Path) -> dict:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "HEARTBEAT.md").write_text(
        "If nothing new, reply 'UA_HEARTBEAT_OK'.", encoding="utf-8"
    )

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{base_url}/api/v1/sessions",
            json={"user_id": "test_heartbeat", "workspace_dir": str(workspace_dir)},
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"create_session failed: {resp.status} {data}")
        session_id = data["session_id"]

        async with session.ws_connect(f"{ws_url}/api/v1/sessions/{session_id}/stream") as ws:
            await ws.receive()  # connected
            # Wait for heartbeat summary (scheduler tick)
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=20.0)
            except asyncio.TimeoutError as exc:
                raise AssertionError("Timed out waiting for heartbeat_summary") from exc

            if msg.get("type") != "heartbeat_summary":
                raise AssertionError(f"Expected heartbeat_summary, got {msg.get('type')}")
            return msg


def test_heartbeat_summary_broadcast(tmp_path):
    port = _get_free_port()
    base_url = f"http://127.0.0.1:{port}"
    ws_url = f"ws://127.0.0.1:{port}"
    env = {
        **os.environ,
        "UA_GATEWAY_PORT": str(port),
        "UA_ENABLE_HEARTBEAT": "1",
        "UA_HEARTBEAT_INTERVAL": "2",
        "UA_HEARTBEAT_MOCK_RESPONSE": "1",
        "UA_HB_SHOW_OK": "true",
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

        msg = asyncio.run(_run_heartbeat_flow(base_url, ws_url, tmp_path / "hb"))
        assert msg["data"]["text"] == "UA_HEARTBEAT_OK"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
