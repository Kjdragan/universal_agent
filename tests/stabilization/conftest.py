import pytest
import subprocess
import time
import requests
import os
import signal
import sys
from typing import Generator

GATEWAY_PORT = 8002
GATEWAY_URL = f"http://localhost:{GATEWAY_PORT}"
HEALTH_ENDPOINT = f"{GATEWAY_URL}/api/v1/health"

@pytest.fixture(scope="session")
def gateway_server() -> Generator[str, None, None]:
    """
    Starts the Gateway Server for the duration of the test session.
    Yields the base URL of the gateway.
    """
    print(f"\n[Fixture] Starting Gateway Server on port {GATEWAY_PORT}...")
    
    # Ensure port is free
    subprocess.run(["fuser", "-k", f"{GATEWAY_PORT}/tcp"], stderr=subprocess.DEVNULL)
    time.sleep(1)

    # Start Gateway Server
    # We use the module directly to avoid script overhead
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    
    process = subprocess.Popen(
        [sys.executable, "-m", "universal_agent.gateway_server"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    # Wait for health check
    max_retries = 30
    ready = False
    for _ in range(max_retries):
        try:
            resp = requests.get(HEALTH_ENDPOINT)
            if resp.status_code == 200:
                ready = True
                break
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(1)

    if not ready:
        print("[Fixture] Gateway failed to start!")
        process.terminate()
        stdout, stderr = process.communicate()
        print(f"STDOUT: {stdout}")
        print(f"STDERR: {stderr}")
        pytest.fail("Gateway Server failed to start within 30 seconds")

    print("[Fixture] Gateway Server ready.")
    yield GATEWAY_URL

    # Teardown
    print("\n[Fixture] Stopping Gateway Server...")
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    
    # Double check port is free
    subprocess.run(["fuser", "-k", f"{GATEWAY_PORT}/tcp"], stderr=subprocess.DEVNULL)
