import asyncio
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
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


async def _run_once(base_url: str, ws_url: str, workspace_dir: Path, heartbeat_content: str, timeout: float = 8.0):
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "HEARTBEAT.md").write_text(heartbeat_content, encoding="utf-8")

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{base_url}/api/v1/sessions",
            json={"user_id": "test_heartbeat_schedule", "workspace_dir": str(workspace_dir)},
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"create_session failed: {resp.status} {data}")
        session_id = data["session_id"]

        async with session.ws_connect(f"{ws_url}/api/v1/sessions/{session_id}/stream") as ws:
            await ws.receive()  # connected
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=timeout)
                return msg
            except asyncio.TimeoutError:
                return None


def _run_gateway(env_overrides: dict[str, str], workspace_root: Path):
    port = _get_free_port()
    base_url = f"http://127.0.0.1:{port}"
    ws_url = f"ws://127.0.0.1:{port}"
    env = {
        **os.environ,
        "UA_GATEWAY_PORT": str(port),
        "UA_WORKSPACES_DIR": str(workspace_root),
        "UA_ENABLE_HEARTBEAT": "1",
        "UA_HEARTBEAT_INTERVAL": "1",
        "UA_HEARTBEAT_MOCK_RESPONSE": "1",
        "UA_HB_SHOW_OK": "true",
        **env_overrides,
    }

    process = subprocess.Popen(
        [sys.executable, "-m", "universal_agent.gateway_server"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ready = asyncio.run(_wait_for_server(base_url))
    assert ready, "Gateway did not start in time"
    return process, base_url, ws_url


def _stop_gateway(process: subprocess.Popen):
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def _format_hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def test_active_hours_blocked(tmp_path):
    now = datetime.now(timezone.utc)
    start = _format_hhmm(now + timedelta(hours=1))
    end = _format_hhmm(now + timedelta(hours=2))
    process, base_url, ws_url = _run_gateway({
        "UA_HEARTBEAT_TIMEZONE": "UTC",
        "UA_HEARTBEAT_ACTIVE_START": start,
        "UA_HEARTBEAT_ACTIVE_END": end,
    }, tmp_path)
    try:
        msg = asyncio.run(
            _run_once(base_url, ws_url, tmp_path / "hb_active_blocked", "UA_HEARTBEAT_OK")
        )
        assert msg is None
    finally:
        _stop_gateway(process)


def test_empty_file_skips(tmp_path):
    now = datetime.now(timezone.utc)
    start = _format_hhmm(now - timedelta(hours=1))
    end = _format_hhmm(now + timedelta(hours=1))
    process, base_url, ws_url = _run_gateway({
        "UA_HEARTBEAT_TIMEZONE": "UTC",
        "UA_HEARTBEAT_ACTIVE_START": start,
        "UA_HEARTBEAT_ACTIVE_END": end,
    }, tmp_path)
    try:
        msg = asyncio.run(
            _run_once(base_url, ws_url, tmp_path / "hb_empty", "# Heartbeat\n\n")
        )
        assert msg is None
    finally:
        _stop_gateway(process)
