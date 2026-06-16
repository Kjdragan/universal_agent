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


@pytest.fixture(autouse=True, scope="session")
def _isolate_scratch_root(tmp_path_factory):
    """Never let a test publish into the real tailnet scratchpad.

    publish_scratch.sh honors UA_SCRATCH_ROOT, so pointing it at a throwaway dir means a
    test that reaches the real publish path (e.g. a digest test calling
    process_daily_digest with email_to set) writes there instead of /home/ua/ua_scratch.
    This is the systemic backstop behind per-test mocking — it closes the leak that put
    "Fake Digest" stubs into the live store when the suite ran on the VPS.
    """
    root = tmp_path_factory.mktemp("ua_scratch_isolated")
    prev = os.environ.get("UA_SCRATCH_ROOT")
    os.environ["UA_SCRATCH_ROOT"] = str(root)
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("UA_SCRATCH_ROOT", None)
        else:
            os.environ["UA_SCRATCH_ROOT"] = prev


@pytest.fixture
def temp_workspace(tmp_path):
    """Create temporary workspace directory for tests."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace
