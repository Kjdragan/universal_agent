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
            json={"user_id": "test_heartbeat_wake", "workspace_dir": str(workspace_dir)},
        ) as resp:
            data = await resp.json()
            assert resp.status == 200, data
            return data["session_id"]


def _write_state(workspace_dir: Path, last_run: float) -> None:
    state = {"last_run": last_run, "last_message_hash": None, "last_message_ts": 0.0}
    (workspace_dir / "heartbeat_state.json").write_text(json.dumps(state), encoding="utf-8")


def test_heartbeat_wake_now_and_next(tmp_path):
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

        workspace_dir = tmp_path / "hb_wake"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / "HEARTBEAT.md").write_text("UA_HEARTBEAT_OK", encoding="utf-8")
        _write_state(workspace_dir, time.time())

        session_id = asyncio.run(_create_session(base_url, workspace_dir))

        async def _run_flow():
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(f"{ws_url}/api/v1/sessions/{session_id}/stream") as ws:
                    await ws.receive()

                    async with session.post(
                        f"{base_url}/api/v1/heartbeat/wake",
                        json={"session_id": session_id, "mode": "now"},
                    ) as resp:
                        assert resp.status == 200

                    msg = await asyncio.wait_for(ws.receive_json(), timeout=6.0)
                    assert msg.get("type") in {"heartbeat_summary", "heartbeat_indicator"}

                    _write_state(workspace_dir, time.time())

                    async with session.post(
                        f"{base_url}/api/v1/heartbeat/wake",
                        json={"session_id": session_id, "mode": "next"},
                    ) as resp:
                        assert resp.status == 200

                    try:
                        await asyncio.wait_for(ws.receive_json(), timeout=1.0)
                        assert False, "Unexpected immediate heartbeat on wake-next"
                    except asyncio.TimeoutError:
                        pass

                    msg_next = await asyncio.wait_for(ws.receive_json(), timeout=5.0)
                    assert msg_next.get("type") in {"heartbeat_summary", "heartbeat_indicator"}

        asyncio.run(_run_flow())
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
