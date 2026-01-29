import pytest
import subprocess
import sys
import os
import time

def test_gateway_mode_plumbing(gateway_server):
    """
    Smoke test: Verifies that the CLI client can connect to the Gateway
    and execute a simple instruction.
    """
    gateway_url = gateway_server  # From fixture
    prompt = "Reply with exactly the string 'SYSTEM_OK' and nothing else."
    
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["UA_GATEWAY_URL"] = gateway_url
    
    input_text = f"{prompt}\nquit\n"
    
    print(f"\n[Gateway] Connecting to {gateway_url} with prompt: {prompt}")
    
    start_time = time.time()
    result = subprocess.run(
        [sys.executable, "-m", "universal_agent.main"],
        input=input_text,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30  # Hard limit for smoke test
    )
    duration = time.time() - start_time
    
    # Assertions
    assert result.returncode == 0, f"Process failed with stderr: {result.stderr}"
    assert "SYSTEM_OK" in result.stdout, "Agent failed to return control string via Gateway"
    print(f"[Gateway] Success! Duration: {duration:.2f}s")
