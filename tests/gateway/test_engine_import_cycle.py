import os
import subprocess
import sys
from pathlib import Path


def test_gateway_import_prefers_execution_engine():
    """
    Regression: importing `universal_agent.gateway` used to set
    `EXECUTION_ENGINE_AVAILABLE=False` due to a circular import chain via
    `universal_agent.api.__init__`.
    """

    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src")

    program = """
import universal_agent.gateway as g
print("EXECUTION_ENGINE_AVAILABLE", g.EXECUTION_ENGINE_AVAILABLE)
print("USE_LEGACY_DEFAULT", g.InProcessGateway()._use_legacy)
"""

    proc = subprocess.run(
        [sys.executable, "-c", program],
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "EXECUTION_ENGINE_AVAILABLE True" in proc.stdout
    assert "USE_LEGACY_DEFAULT False" in proc.stdout


def test_api_submodule_import_does_not_import_gateway():
    """Ensure api package init stays lightweight (no implicit gateway import)."""

    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src")

    program = """
import sys
import universal_agent.api.input_bridge  # noqa: F401
print("GATEWAY_IMPORTED", "universal_agent.gateway" in sys.modules)
"""
    proc = subprocess.run(
        [sys.executable, "-c", program],
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "GATEWAY_IMPORTED False" in proc.stdout
