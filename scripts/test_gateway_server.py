
import asyncio
import os
import sys
import subprocess
import time
import requests
import json
import websockets
from contextlib import closing

# Configure
HOST = "127.0.0.1"
PORT = 8003  # Use different port to avoid conflict
API_URL = f"http://{HOST}:{PORT}"
WS_URL = f"ws://{HOST}:{PORT}"

async def test_gateway_server():
    print(f"Locked & Loaded: Verifying Gateway Server on port {PORT}")
    
    # 1. Start Server
    env = os.environ.copy()
    env["UA_GATEWAY_PORT"] = str(PORT)
    env["UA_GATEWAY_HOST"] = HOST
    env["PYTHONPATH"] = os.path.join(os.getcwd(), "src") + os.pathsep + env.get("PYTHONPATH", "")
    
    server_process = subprocess.Popen(
        ["uv", "run", "python", "-m", "universal_agent.gateway_server"],
        cwd=os.getcwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    try:
        # 2. Wait for Health
        print("Waiting for server health...")
        for i in range(20):
            try:
                response = requests.get(f"{API_URL}/api/v1/health")
                if response.status_code == 200:
                    print("Server is Healthy!")
                    break
            except requests.ConnectionError:
                pass
            time.sleep(1)
        else:
            print("FAILURE: Server refused to start.")
            # Print logs
            stdout, stderr = server_process.communicate(timeout=5)
            print("STDOUT:", stdout)
            print("STDERR:", stderr)
            sys.exit(1)
            
        # 3. Create Session (REST)
        print("Creating Session...")
        payload = {"user_id": "test_user_gateway", "workspace_dir": "/tmp/gateway_test_workspace"}
        response = requests.post(f"{API_URL}/api/v1/sessions", json=payload)
        response.raise_for_status()
        session_data = response.json()
        session_id = session_data["session_id"]
        print(f"Session Created: {session_id}")
        
        # 4. Connect WebSocket
        print("Connecting WebSocket...")
        ws_endpoint = f"{WS_URL}/api/v1/sessions/{session_id}/stream"
        async with websockets.connect(ws_endpoint) as ws:
            # Expect "connected" message
            msg1 = await ws.recv()
            print(f"Received: {msg1}")
            data = json.loads(msg1)
            assert data["type"] == "connected", "Expected 'connected' message"
            
            # 5. Ping
            print("Sending Ping...")
            await ws.send(json.dumps({"type": "ping", "data": {}}))
            
            # Expect Pong
            msg2 = await ws.recv()
            print(f"Received: {msg2}")
            data = json.loads(msg2)
            assert data["type"] == "pong", "Expected 'pong' message"
            
            print("SUCCESS: WebSockets functional.")
            
    finally:
        print("Killing server...")
        server_process.terminate()
        try:
            server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_process.kill()

if __name__ == "__main__":
    asyncio.run(test_gateway_server())
