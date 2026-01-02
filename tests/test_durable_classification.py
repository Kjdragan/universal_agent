from universal_agent.durable.classification import (
    REPLAY_EXACT,
    REPLAY_IDEMPOTENT,
    RELAUNCH,
    classify_replay_policy,
    classify_tool,
)


def test_classify_composio_email():
    assert classify_tool("GMAIL_SEND_EMAIL", "composio") == "external"


def test_classify_mcp_upload():
    assert classify_tool("upload_to_composio", "mcp") == "external"


def test_classify_mcp_memory_append():
    assert classify_tool("core_memory_append", "mcp") == "memory"


def test_classify_mcp_write_local():
    assert classify_tool("write_local_file", "mcp") == "local"


def test_classify_composio_search():
    assert classify_tool("COMPOSIO_SEARCH_WEB", "composio") == "read_only"


def test_replay_policy_task_relaunch():
    assert classify_replay_policy("task", "claude_code") == RELAUNCH


def test_replay_policy_read_only_idempotent():
    assert classify_replay_policy("read_local_file", "mcp") == REPLAY_IDEMPOTENT


def test_replay_policy_side_effect_exact():
    assert classify_replay_policy("GMAIL_SEND_EMAIL", "composio") == REPLAY_EXACT
