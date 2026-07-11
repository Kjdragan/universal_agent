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

    Also pins UA_SCRATCH_ARCHIVE_ROOT to a throwaway dir: publish_scratch.sh now drops a
    durable archive copy after each publish, and the desktop/dev default for that root is
    ``<repo>/scratch_archive`` — so without this a test reaching the real publish path
    would write archive files straight into the working tree. Pointing it at a temp dir
    keeps the repo clean.
    """
    root = tmp_path_factory.mktemp("ua_scratch_isolated")
    archive_root = tmp_path_factory.mktemp("ua_scratch_archive_isolated")
    prev = os.environ.get("UA_SCRATCH_ROOT")
    prev_archive = os.environ.get("UA_SCRATCH_ARCHIVE_ROOT")
    os.environ["UA_SCRATCH_ROOT"] = str(root)
    os.environ["UA_SCRATCH_ARCHIVE_ROOT"] = str(archive_root)
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("UA_SCRATCH_ROOT", None)
        else:
            os.environ["UA_SCRATCH_ROOT"] = prev
        if prev_archive is None:
            os.environ.pop("UA_SCRATCH_ARCHIVE_ROOT", None)
        else:
            os.environ["UA_SCRATCH_ARCHIVE_ROOT"] = prev_archive


@pytest.fixture(autouse=True, scope="session")
def _isolate_outbound_channels():
    """Never let a test send REAL email/messages to the operator.

    Same systemic-backstop pattern as ``_isolate_scratch_root``, for outbound
    channels. On 2026-07-11 a desktop test run (developer shells here load
    real Infisical secrets into the environment) drove the real notification
    pipeline: five "[ERROR] ... unit_test_raiser / heartbeat_service failed"
    fixture emails landed in the operator's Gmail via Simone's live AgentMail
    inbox. CI is already green with none of these vars present, so scrubbing
    them cannot break any gated test; tests that exercise send paths set fake
    keys themselves via monkeypatch (which runs after this session fixture).

    - AGENTMAIL_API_KEY removed -> AgentMailService refuses to start a real
      client (services/agentmail_service.py reads it at client init).
    - UA_AGENTMAIL_GMAIL_FALLBACK=0 -> the gws Gmail-CLI fallback stays off
      even where ~/.config/gws holds live desktop credentials.
    - TELEGRAM_BOT_TOKEN removed -> the bot adapters cannot push to the
      operator's phone.
    - UA_INFISICAL_ENABLED=0 -> initialize_runtime_secrets() cannot re-fetch
      the scrubbed keys from Infisical mid-test.
    """
    removed = {}
    for key in ("AGENTMAIL_API_KEY", "TELEGRAM_BOT_TOKEN"):
        removed[key] = os.environ.pop(key, None)
    forced = {"UA_AGENTMAIL_GMAIL_FALLBACK": "0", "UA_INFISICAL_ENABLED": "0"}
    prev_forced = {key: os.environ.get(key) for key in forced}
    os.environ.update(forced)
    try:
        yield
    finally:
        for key, value in {**removed, **prev_forced}.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture
def temp_workspace(tmp_path):
    """Create temporary workspace directory for tests."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace
