import asyncio
import os
import json
import logging
import sys
import time
import subprocess
import aiohttp
import uuid
import shutil
import signal
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("test_delivery")

GATEWAY_PORT = 8031
BASE_URL = f"http://127.0.0.1:{GATEWAY_PORT}"
WS_URL = f"ws://127.0.0.1:{GATEWAY_PORT}"

logger.info(f"Using Python executable: {sys.executable}")
PYTHON_CMD = [sys.executable, "-m", "universal_agent.gateway_server"]

async def wait_for_server(timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{BASE_URL}/api/v1/health") as resp:
                    if resp.status == 200:
                        return True
        except Exception as e:
            logger.warning(f"Waiting for server: {e}")
            await asyncio.sleep(1)
    return False

class GatewayManager:
    def __init__(self, env):
        self.env = {**os.environ, **env}
        self.env["UA_GATEWAY_PORT"] = str(GATEWAY_PORT)
        self.process = None

    async def start(self):
        logger.info("Starting Gateway...")
        self.stdout_f = open(f"/tmp/gw_{GATEWAY_PORT}.out", "w")
        self.stderr_f = open(f"/tmp/gw_{GATEWAY_PORT}.err", "w")
        self.process = subprocess.Popen(
            PYTHON_CMD, 
            env=self.env, 
            stdout=self.stdout_f, 
            stderr=self.stderr_f
        )
        if not await wait_for_server():
            logger.error("Gateway failed to start")
            self.stop()
            # Read logs
            try:
                with open(f"/tmp/gw_{GATEWAY_PORT}.err", "r") as f:
                    logger.error(f"Gateway STDERR:\n{f.read()}")
            except: pass
            raise RuntimeError("Gateway failed to start")
        logger.info("Gateway started.")

    def stop(self):
        if self.process:
            logger.info("Stopping Gateway...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.stdout_f.close()
            self.stderr_f.close()
            logger.info("Gateway stopped.")

async def run_client_check(workspace_dir, setup_instruction, expect_event_type=None, expect_timeout=False):
    """
    Creates a session, connects WS, waits for event.
    setup_instruction: Text to put in HEARTBEAT.md
    """
    # Setup workspace
    os.makedirs(workspace_dir, exist_ok=True)
    with open(os.path.join(workspace_dir, "HEARTBEAT.md"), "w") as f:
        f.write(setup_instruction)

    async with aiohttp.ClientSession() as session:
        # Create session
        async with session.post(f"{BASE_URL}/api/v1/sessions", json={
            "user_id": "test_delivery",
            "workspace_dir": workspace_dir
        }) as resp:
            if resp.status != 200:
                logger.error(f"Failed to create session: {await resp.text()}")
                return False
            data = await resp.json()
            session_id = data["session_id"]
        
        # Connect WS
        ws_url = f"{WS_URL}/api/v1/sessions/{session_id}/stream"
        logger.info(f"Connecting client to {ws_url}...")
        
        try:
            async with session.ws_connect(ws_url) as ws:
                msg = await ws.receive_json() # connected
                
                logger.info(f"Waiting for heartbeat (expect_timeout={expect_timeout})...")
                try:
                    # Wait for execution (can take ~5-10s)
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=20.0)
                    
                    if expect_timeout:
                        logger.error(f"❌ Received unexpected event: {msg['type']}")
                        return False
                    
                    if expect_event_type and msg['type'] != expect_event_type:
                        logger.error(f"❌ Wrong event type. Expected {expect_event_type}, got {msg['type']}")
                        return False
                        
                    logger.info(f"✅ Received expected event: {msg['type']}")
                    if msg['type'] == 'heartbeat_summary':
                        logger.info(f"   Data: {msg['data']}")
                    return True

                except asyncio.TimeoutError:
                    if expect_timeout:
                        logger.info("✅ Verified: No event received (timeout expected).")
                        return True
                    else:
                        logger.error("❌ Timed out waiting for event")
                        return False
        except Exception as e:
            logger.error(f"Client error: {e}")
            return False
        except BaseException as e:
            logger.error(f"CRITICAL CLIENT ERROR: {type(e).__name__ }: {e}")
            raise

async def main():
    logger.info("=== TEST 1: Visibility Policy (show_ok=True) ===")
    gw1 = GatewayManager({
        "UA_ENABLE_HEARTBEAT": "1",
        "UA_HB_SHOW_OK": "true",
        "UA_HEARTBEAT_INTERVAL": "2", # Very fast interval
        "UA_HEARTBEAT_MOCK_RESPONSE": "1",
    })
    try:
        await gw1.start()
        # Prompt for OK
        instr = "If nothing new, reply 'UA_HEARTBEAT_OK'."
        wd = f"/tmp/test_vis_ok_{uuid.uuid4().hex}"
        if not await run_client_check(wd, instr, expect_event_type="heartbeat_summary"):
            logger.error("Test 1 FAILED")
            sys.exit(1)
    finally:
        gw1.stop()

    logger.info("=== TEST 2: Visibility Policy (show_ok=False) ===")
    gw2 = GatewayManager({
        "UA_ENABLE_HEARTBEAT": "1",
        "UA_HB_SHOW_OK": "false",
        "UA_HEARTBEAT_INTERVAL": "2",
        "UA_HEARTBEAT_MOCK_RESPONSE": "1",
    })
    try:
        await gw2.start()
        # Prompt for OK (should be suppressed)
        instr = "If nothing new, reply 'UA_HEARTBEAT_OK'."
        wd = f"/tmp/test_vis_suppress_{uuid.uuid4().hex}"
        if not await run_client_check(wd, instr, expect_timeout=True):
            logger.error("Test 2 FAILED")
            sys.exit(1)
    finally:
        gw2.stop()

    logger.info("=== TEST 3: Deduplication ===")
    # We reuse the workspace to keep state
    wd_dedupe = f"/tmp/test_dedupe_{uuid.uuid4().hex}"
    
    gw3 = GatewayManager({
        "UA_ENABLE_HEARTBEAT": "1",
        "UA_HB_DEDUPE_WINDOW": "3600", # 1 hour
        "UA_HEARTBEAT_INTERVAL": "1",  # 1s interval
        "UA_HEARTBEAT_MOCK_RESPONSE": "1",
    })
    try:
        await gw3.start()
        
        # 3a. First Alert -> Should Send
        logger.info("--- 3a: First Alert ---")
        instr = "Reply exactly: 'ALERT_TEST_A'"
        if not await run_client_check(wd_dedupe, instr, expect_event_type="heartbeat_summary"):
             logger.error("Test 3a FAILED")
             sys.exit(1)

        # 3b. Same Alert -> Should Suppress (Dedupe)
        # Note: We need to trigger a *new* run. The interval is 1s.
        # run_client_check creates a NEW session ID but points to SAME workspace dir.
        # Gateway logic: uses session.workspace_dir to load state. So state persists.
        # BUT `active_sessions` map uses session_id.
        # If we use a new session_id, `HeartbeatService` *registers* it.
        # Does state file lockout other sessions? No.
        # Does `active_sessions` allow multiple sessions for same workspace? Yes.
        # But `busy_sessions` is by session_id.
        # The logic loads state from workspace.
        # So Run 1 updates `last_message_hash`.
        # Run 2 (new session, same dir) reads `last_message_hash`.
        # If content matches, it should suppress.
        
        logger.info("--- 3b: Repeated Alert (Dedupe) ---")
        await asyncio.sleep(2) # Ensure interval passed
        instr = "Reply exactly: 'ALERT_TEST_A'"
        if not await run_client_check(wd_dedupe, instr, expect_timeout=True):
             logger.error("Test 3b FAILED")
             sys.exit(1)

        # 3c. Different Alert -> Should Send
        logger.info("--- 3c: Different Alert ---")
        await asyncio.sleep(2)
        instr = "Reply exactly: 'ALERT_TEST_B'"
        if not await run_client_check(wd_dedupe, instr, expect_event_type="heartbeat_summary"):
             logger.error("Test 3c FAILED")
             sys.exit(1)

    finally:
        gw3.stop()

    logger.info("✅ ALL TESTS PASSED")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except BaseException as e:
        logger.critical(f"UNHANDLED EXCEPTION IN MAIN: {e}", exc_info=True)
        sys.exit(1)
