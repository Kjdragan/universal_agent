import asyncio
import os
import json
import logging
import sys
import argparse
import aiohttp
import time
import uuid
import shutil

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("verify_heartbeat")

BASE_URL = os.getenv("GATEWAY_URL", "http://localhost:8002")
WS_URL = BASE_URL.replace("http", "ws")

async def test_heartbeat():
    async with aiohttp.ClientSession() as session:
        # 1. Create a session
        logger.info(f"Creating session at {BASE_URL}/api/v1/sessions...")
        workspace_dir = f"/tmp/test_hb_{uuid.uuid4().hex}"
        
        # Ensure workspace exists and has HEARTBEAT.md
        os.makedirs(workspace_dir, exist_ok=True)
        with open(os.path.join(workspace_dir, "HEARTBEAT.md"), "w") as f:
            f.write("Perform a quick status check.")
            
        async with session.post(f"{BASE_URL}/api/v1/sessions", json={
            "user_id": "test_user",
            "workspace_dir": workspace_dir
        }) as resp:
            if resp.status != 200:
                logger.error(f"Failed to create session: {await resp.text()}")
                return False
            data = await resp.json()
            session_id = data["session_id"]
            logger.info(f"Session created: {session_id}")

        # 2. Connect client
        ws_url = f"{WS_URL}/api/v1/sessions/{session_id}/stream"
        logger.info(f"Connecting to {ws_url}...")
        
        try:
            async with session.ws_connect(ws_url) as ws:
                logger.info("Connected.")
                msg = await ws.receive_json() # connected
                logger.info(f"Received: {msg['type']}")
                
                # 3. Wait for heartbeat summary
                # Since we can't easily force the scheduler time in an integration test without mocks,
                # we rely on the fact that the first run happens because last_run=0.
                # However, the scheduler loop has a sleep. We might need to wait up to 10s.
                
                logger.info("Waiting for heartbeat_summary...")
                start_wait = time.time()
                while time.time() - start_wait < 30:
                    try:
                        # The heartbeat execution involves spinning up the full agent (CLI wrapper),
                        # which can take 10-20 seconds or more on first run.
                        msg = await asyncio.wait_for(ws.receive_json(), timeout=60.0)
                        logger.info(f"Received event: {msg['type']}")
                        if msg['type'] == 'heartbeat_summary':
                            logger.info(f"✅ SUCCESS: Heartbeat received: {msg['data']}")
                            return True
                    except asyncio.TimeoutError:
                        logger.info("Waiting...")
                        
                logger.error("❌ FAILURE: Timed out waiting for heartbeat_summary")
                return False

        except Exception as e:
            logger.error(f"Test failed with exception: {e}")
            return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    
    success = asyncio.run(test_heartbeat())
    sys.exit(0 if success else 1)
