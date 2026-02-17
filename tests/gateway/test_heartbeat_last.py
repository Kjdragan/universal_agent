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


async def _create_session(base_url: str, workspace_dir: Path) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{base_url}/api/v1/sessions",
            json={"user_id": "test_heartbeat_last", "workspace_dir": str(workspace_dir)},
        ) as resp:
            data = await resp.json()
            assert resp.status == 200, data
            return data["session_id"]


def _wait_for_summary(state_path: Path, timeout: float = 8.0) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        if state_path.exists():
            try:
                data = json.loads(state_path.read_text())
            except Exception:
                data = {}
            if data.get("last_summary"):
                return data
        time.sleep(0.2)
    return {}


def test_heartbeat_last_endpoint(tmp_path):
    port = _get_free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "PYTHONPATH": "src",
        "UA_DEPLOYMENT_PROFILE": "local_workstation",
        "UA_INTERNAL_API_TOKEN": "",
        "UA_OPS_TOKEN": "",
        "UA_GATEWAY_PORT": str(port),
        "UA_WORKSPACES_DIR": str(tmp_path),
        "UA_ENABLE_HEARTBEAT": "1",
        "UA_HEARTBEAT_INTERVAL": "2",
        "UA_HEARTBEAT_MIN_INTERVAL_SECONDS": "1",
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

        workspace_dir = tmp_path / "hb_last"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / "HEARTBEAT.md").write_text("UA_HEARTBEAT_OK", encoding="utf-8")

        session_id = asyncio.run(_create_session(base_url, workspace_dir))

        async def _trigger_and_fetch():
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{base_url}/api/v1/heartbeat/wake",
                    json={"session_id": session_id, "mode": "now"},
                ) as resp:
                    assert resp.status == 200

                state_path = workspace_dir / "heartbeat_state.json"
                data = _wait_for_summary(state_path)
                assert data.get("last_summary"), "Heartbeat summary not recorded"

                async with session.get(
                    f"{base_url}/api/v1/heartbeat/last",
                    params={"session_id": session_id},
                ) as resp:
                    payload = await resp.json()
                    assert resp.status == 200, payload
                    assert payload.get("last_summary"), payload
                    summary = payload["last_summary"]
                    assert summary.get("ok_only") is True
                    assert "UA_HEARTBEAT_OK" in summary.get("text", "")

        asyncio.run(_trigger_and_fetch())
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
