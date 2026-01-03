import pytest

from universal_agent.durable.classification import (
    REPLAY_EXACT,
    REPLAY_IDEMPOTENT,
    RELAUNCH,
    classify_replay_policy,
    classify_tool,
    _reset_tool_policy_cache,
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


def test_policy_config_override(tmp_path, monkeypatch):
    policy = """version: 1
policies:
  - name: test_read_only
    tool_namespace: composio
    side_effect_class: read_only
    replay_policy: REPLAY_IDEMPOTENT
    patterns:
      - "^COMPOSIO_TEST_READ$"
"""
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(policy)
    monkeypatch.setenv("UA_TOOL_POLICIES_PATH", str(policy_path))
    _reset_tool_policy_cache()
    assert classify_tool("COMPOSIO_TEST_READ", "composio") == "read_only"
    assert classify_replay_policy("COMPOSIO_TEST_READ", "composio") == REPLAY_IDEMPOTENT


def test_policy_tool_name_regex_and_overlay(tmp_path, monkeypatch):
    base = """version: 1
policies:
  - name: base_read_only
    tool_namespace: composio
    side_effect_class: read_only
    replay_policy: REPLAY_IDEMPOTENT
    tool_name_regex: "^COMPOSIO_OVERLAY$"
"""
    overlay = """version: 1
policies:
  - name: overlay_external
    namespace: composio
    side_effect_class: external
    replay_policy: REPLAY_EXACT
    tool_name_regex: "^COMPOSIO_OVERLAY$"
"""
    base_path = tmp_path / "base.yaml"
    overlay_path = tmp_path / "overlay.yaml"
    base_path.write_text(base)
    overlay_path.write_text(overlay)
    monkeypatch.setenv("UA_TOOL_POLICIES_PATH", str(base_path))
    monkeypatch.setenv("UA_TOOL_POLICIES_OVERLAY_PATH", str(overlay_path))
    _reset_tool_policy_cache()
    assert classify_tool("COMPOSIO_OVERLAY", "composio") == "external"
    assert classify_replay_policy("COMPOSIO_OVERLAY", "composio") == REPLAY_EXACT


def test_invalid_policy_schema_fails_fast(tmp_path, monkeypatch):
    bad_policy = """version: 1
policies:
  - name: missing_patterns
    tool_namespace: composio
"""
    policy_path = tmp_path / "bad.yaml"
    policy_path.write_text(bad_policy)
    monkeypatch.setenv("UA_TOOL_POLICIES_PATH", str(policy_path))
    _reset_tool_policy_cache()
    with pytest.raises(ValueError):
        classify_tool("COMPOSIO_TEST", "composio")


def test_task_output_forces_relaunch():
    assert classify_replay_policy("TaskOutput", "composio") == RELAUNCH
    assert (
        classify_replay_policy(
            "any", "composio", metadata={"raw_tool_name": "TaskResult"}
        )
        == RELAUNCH
    )
