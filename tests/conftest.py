import os
import pytest


os.environ["UA_DISABLE_LOGFIRE"] = "1"
os.environ["LOGFIRE_TOKEN"] = ""
os.environ["LOGFIRE_WRITE_TOKEN"] = ""
os.environ["LOGFIRE_API_KEY"] = ""
os.environ["LOGFIRE_IGNORE_NO_CONFIG"] = "1"


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "slow: marks tests as slow running")
    config.addinivalue_line("markers", "integration: marks tests requiring external services")
    config.addinivalue_line("markers", "e2e: marks end-to-end tests")


@pytest.fixture(autouse=True)
def _reset_workspace_context():
    """Reset the workspace ContextVar between tests to prevent state leakage.

    Tests that call bind_workspace_env() or workspace_context() set a
    process-wide ContextVar that persists across synchronous test functions.
    Without this reset, workspace paths from earlier tests contaminate
    subsequent assertions (e.g. test_toggle_session → test_workspace_env_aliases).
    """
    from universal_agent.execution_context import _WORKSPACE_CONTEXT_VAR

    token = _WORKSPACE_CONTEXT_VAR.set(None)
    yield
    _WORKSPACE_CONTEXT_VAR.reset(token)


@pytest.fixture
def temp_workspace(tmp_path):
    """Create temporary workspace directory for tests."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace
