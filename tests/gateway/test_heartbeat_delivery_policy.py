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


async def _run_once(base_url: str, ws_url: str, workspace_dir: Path, instruction: str, timeout: float = 8.0):
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "HEARTBEAT.md").write_text(instruction, encoding="utf-8")

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
            await ws.receive()
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=timeout)
                return msg
            except asyncio.TimeoutError:
                return None


def _run_gateway(env_overrides: dict[str, str]):
    port = _get_free_port()
    base_url = f"http://127.0.0.1:{port}"
    ws_url = f"ws://127.0.0.1:{port}"
    env = {
        **os.environ,
        "UA_GATEWAY_PORT": str(port),
        "UA_ENABLE_HEARTBEAT": "1",
        "UA_HEARTBEAT_INTERVAL": "1",
        "UA_HEARTBEAT_MOCK_RESPONSE": "1",
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


def test_show_ok_suppressed():
    process, base_url, ws_url = _run_gateway({
        "UA_HB_SHOW_OK": "false",
        "UA_HB_USE_INDICATOR": "false",
        "UA_HB_DELIVERY_MODE": "last",
    })
    try:
        msg = asyncio.run(
            _run_once(base_url, ws_url, Path("/tmp/hb_show_ok_off"), "UA_HEARTBEAT_OK")
        )
        assert msg is None
    finally:
        _stop_gateway(process)


def test_show_ok_indicator():
    process, base_url, ws_url = _run_gateway({
        "UA_HB_SHOW_OK": "false",
        "UA_HB_USE_INDICATOR": "true",
        "UA_HB_DELIVERY_MODE": "last",
    })
    try:
        msg = asyncio.run(
            _run_once(base_url, ws_url, Path("/tmp/hb_show_ok_indicator"), "UA_HEARTBEAT_OK")
        )
        assert msg is not None
        assert msg.get("type") == "heartbeat_indicator"
    finally:
        _stop_gateway(process)


def test_show_alerts_suppressed():
    process, base_url, ws_url = _run_gateway({
        "UA_HB_SHOW_ALERTS": "false",
        "UA_HB_DELIVERY_MODE": "last",
    })
    try:
        msg = asyncio.run(
            _run_once(base_url, ws_url, Path("/tmp/hb_alerts_off"), "ALERT_TEST_A")
        )
        assert msg is None
    finally:
        _stop_gateway(process)


def test_dedupe_alert():
    process, base_url, ws_url = _run_gateway({
        "UA_HB_SHOW_ALERTS": "true",
        "UA_HB_DELIVERY_MODE": "last",
        "UA_HB_DEDUPE_WINDOW": "3600",
    })
    try:
        workspace = Path("/tmp/hb_dedupe")
        msg_first = asyncio.run(
            _run_once(base_url, ws_url, workspace, "ALERT_TEST_A")
        )
        assert msg_first is not None
        assert msg_first.get("type") == "heartbeat_summary"
        time.sleep(2)
        msg_second = asyncio.run(
            _run_once(base_url, ws_url, workspace, "ALERT_TEST_A")
        )
        assert msg_second is None
    finally:
        _stop_gateway(process)


def test_explicit_delivery_current():
    process, base_url, ws_url = _run_gateway({
        "UA_HB_DELIVERY_MODE": "explicit",
        "UA_HB_EXPLICIT_SESSION_IDS": "CURRENT",
        "UA_HB_SHOW_OK": "true",
    })
    try:
        msg = asyncio.run(
            _run_once(base_url, ws_url, Path("/tmp/hb_explicit_current"), "UA_HEARTBEAT_OK")
        )
        assert msg is not None
        assert msg.get("type") in {"heartbeat_summary", "heartbeat_indicator"}
    finally:
        _stop_gateway(process)
