import pytest
import subprocess
import sys
import os

def test_direct_mode_plumbing():
    """
    Smoke test: Verifies that main.py can run directly (no gateway)
    and process a simple instruction.
    """
    prompt = "Reply with exactly the string 'SYSTEM_OK' and nothing else."
    
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    
    # Run the CLI in single-query mode
    # We pipe the prompt to stdin if main.py supports reading from piping, 
    # but based on start_cli_dev.sh, it accepts args or interactive.
    # Let's use the pattern from start_cli_dev.sh: pipe 'prompt\nquit\n'
    
    input_text = f"{prompt}\nquit\n"
    
    print(f"\n[Direct] Running main.py with prompt: {prompt}")
    
    start_time = time.time()
    result = subprocess.run(
        [sys.executable, "-m", "universal_agent.main"],
        input=input_text,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=90  # Hard limit for smoke test (startup can be slow)
    )
    duration = time.time() - start_time
    
    # Assertions
    assert result.returncode == 0, f"Process failed with stderr: {result.stderr}"
    assert "SYSTEM_OK" in result.stdout, "Agent failed to return the expected control string"
    print(f"[Direct] Success! Duration: {duration:.2f}s")

import time
