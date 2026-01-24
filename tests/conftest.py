import os
import pytest


os.environ["UA_DISABLE_LOGFIRE"] = "1"
os.environ["LOGFIRE_TOKEN"] = ""
os.environ["LOGFIRE_WRITE_TOKEN"] = ""
os.environ["LOGFIRE_API_KEY"] = ""


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "slow: marks tests as slow running")
    config.addinivalue_line("markers", "integration: marks tests requiring external services")
    config.addinivalue_line("markers", "e2e: marks end-to-end tests")


@pytest.fixture
def temp_workspace(tmp_path):
    """Create temporary workspace directory for tests."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace
