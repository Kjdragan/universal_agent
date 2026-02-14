import os
import subprocess

import pytest


@pytest.mark.skipif(
    (os.getenv("RUN_CODEINTERPRETER_SMOKE", "") or "").strip().lower() not in {"1", "true", "yes"},
    reason="Set RUN_CODEINTERPRETER_SMOKE=1 to run live CodeInterpreter smoke test.",
)
def test_codeinterpreter_smoke_script_runs():
    # This is a live integration test. It requires COMPOSIO_API_KEY and a usable user id.
    p = subprocess.run(
        ["uv", "run", "python", "scripts/experiments/codeinterpreter_smoke_test.py"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert p.returncode == 0, f"stdout:\n{p.stdout}\n\nstderr:\n{p.stderr}"

