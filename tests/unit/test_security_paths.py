"""Unit tests for security_paths.py — path containment and session ID validation."""
from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent import security_paths


# ---------------------------------------------------------------------------
# is_valid_session_id
# ---------------------------------------------------------------------------
class TestIsValidSessionId:
    def test_simple_alphanumeric(self):
        assert security_paths.is_valid_session_id("abc123") is True

    def test_single_character(self):
        assert security_paths.is_valid_session_id("a") is True

    def test_single_digit(self):
        assert security_paths.is_valid_session_id("0") is True

    def test_allows_dot(self):
        assert security_paths.is_valid_session_id("session.v2") is True

    def test_allows_hyphen(self):
        assert security_paths.is_valid_session_id("session-abc") is True

    def test_allows_underscore(self):
        assert security_paths.is_valid_session_id("session_abc") is True

    def test_max_length_128_chars(self):
        value = "a" + "b" * 127  # 128 chars total
        assert security_paths.is_valid_session_id(value) is True

    def test_too_long_129_chars(self):
        value = "a" + "b" * 128  # 129 chars total
        assert security_paths.is_valid_session_id(value) is False

    def test_empty_string(self):
        assert security_paths.is_valid_session_id("") is False

    def test_whitespace_only(self):
        assert security_paths.is_valid_session_id("   ") is False

    def test_leading_underscore_invalid(self):
        assert security_paths.is_valid_session_id("_bad") is False

    def test_leading_hyphen_invalid(self):
        assert security_paths.is_valid_session_id("-bad") is False

    def test_leading_dot_invalid(self):
        assert security_paths.is_valid_session_id(".bad") is False

    def test_slash_disallowed(self):
        assert security_paths.is_valid_session_id("a/b") is False

    def test_space_disallowed(self):
        assert security_paths.is_valid_session_id("a b") is False

    def test_newline_disallowed(self):
        assert security_paths.is_valid_session_id("a\nb") is False

    def test_null_byte_disallowed(self):
        assert security_paths.is_valid_session_id("a\x00b") is False

    @pytest.mark.parametrize("value", [
        "abc",
        "ABC123",
        "a.b.c",
        "my-session-42",
        "session_v1.2",
    ])
    def test_valid_parametrized(self, value):
        assert security_paths.is_valid_session_id(value) is True

    @pytest.mark.parametrize("value", [
        "",
        "   ",
        "../bad",
        "/absolute",
        "a b",
        "a@b",
        "!invalid",
    ])
    def test_invalid_parametrized(self, value):
        assert security_paths.is_valid_session_id(value) is False


# ---------------------------------------------------------------------------
# validate_session_id
# ---------------------------------------------------------------------------
class TestValidateSessionId:
    def test_valid_returns_stripped(self):
        assert security_paths.validate_session_id("  abc123  ") == "abc123"

    def test_valid_no_strip_needed(self):
        assert security_paths.validate_session_id("abc") == "abc"

    def test_invalid_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid session id format"):
            security_paths.validate_session_id("../traversal")

    def test_empty_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid session id format"):
            security_paths.validate_session_id("")

    def test_none_equivalent_raises(self):
        with pytest.raises((ValueError, AttributeError)):
            security_paths.validate_session_id(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _is_within
# ---------------------------------------------------------------------------
class TestIsWithin:
    def test_child_is_within_parent(self, tmp_path: Path):
        child = tmp_path / "subdir" / "file.txt"
        assert security_paths._is_within(tmp_path, child) is True

    def test_parent_itself_is_within(self, tmp_path: Path):
        assert security_paths._is_within(tmp_path, tmp_path) is True

    def test_sibling_not_within(self, tmp_path: Path):
        sibling = tmp_path.parent / "other"
        assert security_paths._is_within(tmp_path, sibling) is False

    def test_parent_dir_not_within_child(self, tmp_path: Path):
        child = tmp_path / "subdir"
        assert security_paths._is_within(child, tmp_path) is False

    def test_traversal_path_not_within(self, tmp_path: Path):
        traversal = tmp_path / ".." / "escape"
        assert security_paths._is_within(tmp_path, traversal) is False

    def test_deep_nested_is_within(self, tmp_path: Path):
        deep = tmp_path / "a" / "b" / "c" / "d"
        assert security_paths._is_within(tmp_path, deep) is True


# ---------------------------------------------------------------------------
# resolve_workspace_dir
# ---------------------------------------------------------------------------
class TestResolveWorkspaceDir:
    def test_none_requested_returns_none(self, tmp_path: Path):
        result = security_paths.resolve_workspace_dir(tmp_path, None)
        assert result is None

    def test_empty_string_returns_none(self, tmp_path: Path):
        result = security_paths.resolve_workspace_dir(tmp_path, "")
        assert result is None

    def test_relative_path_resolved_under_workspaces(self, tmp_path: Path):
        result = security_paths.resolve_workspace_dir(tmp_path, "myworkspace")
        assert result == str((tmp_path / "myworkspace").resolve())

    def test_absolute_path_within_workspaces_ok(self, tmp_path: Path):
        child = tmp_path / "session123"
        result = security_paths.resolve_workspace_dir(tmp_path, str(child))
        assert result == str(child.resolve())

    def test_path_traversal_raises(self, tmp_path: Path):
        escape = str(tmp_path / ".." / "escape")
        with pytest.raises(ValueError, match="must remain under UA_WORKSPACES_DIR"):
            security_paths.resolve_workspace_dir(tmp_path, escape)

    def test_absolute_outside_raises_without_flag(self, tmp_path: Path):
        outside = str(tmp_path.parent / "external_dir")
        with pytest.raises(ValueError, match="must remain under UA_WORKSPACES_DIR"):
            security_paths.resolve_workspace_dir(tmp_path, outside)

    def test_allow_external_bypasses_containment_check(self, tmp_path: Path):
        outside = str(tmp_path.parent / "external_dir")
        result = security_paths.resolve_workspace_dir(tmp_path, outside, allow_external=True)
        assert result == str(Path(outside).resolve())

    def test_allow_external_still_resolves_relative(self, tmp_path: Path):
        result = security_paths.resolve_workspace_dir(tmp_path, "rel", allow_external=True)
        assert result == str((tmp_path / "rel").resolve())


# ---------------------------------------------------------------------------
# resolve_ops_log_path
# ---------------------------------------------------------------------------
class TestResolveOpsLogPath:
    def test_relative_path_resolved_under_workspaces(self, tmp_path: Path):
        result = security_paths.resolve_ops_log_path(tmp_path, "ops.log")
        assert result == (tmp_path / "ops.log").resolve()

    def test_absolute_within_workspaces_ok(self, tmp_path: Path):
        log = tmp_path / "subdir" / "ops.log"
        result = security_paths.resolve_ops_log_path(tmp_path, str(log))
        assert result == log.resolve()

    def test_traversal_raises(self, tmp_path: Path):
        escape = str(tmp_path / ".." / "etc" / "passwd")
        with pytest.raises(ValueError, match="must remain under UA_WORKSPACES_DIR"):
            security_paths.resolve_ops_log_path(tmp_path, escape)

    def test_absolute_outside_raises(self, tmp_path: Path):
        outside = "/tmp/injected.log"
        with pytest.raises(ValueError, match="must remain under UA_WORKSPACES_DIR"):
            security_paths.resolve_ops_log_path(tmp_path, outside)

    def test_returns_path_object(self, tmp_path: Path):
        result = security_paths.resolve_ops_log_path(tmp_path, "ops.log")
        assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# allow_external_workspaces_from_env
# ---------------------------------------------------------------------------
class TestAllowExternalWorkspacesFromEnv:
    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "True", "YES", "ON"])
    def test_truthy_values(self, value, monkeypatch):
        monkeypatch.setenv("UA_ALLOW_EXTERNAL_WORKSPACES", value)
        assert security_paths.allow_external_workspaces_from_env() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "nope", "2"])
    def test_falsy_values(self, value, monkeypatch):
        monkeypatch.setenv("UA_ALLOW_EXTERNAL_WORKSPACES", value)
        assert security_paths.allow_external_workspaces_from_env() is False

    def test_unset_returns_false(self, monkeypatch):
        monkeypatch.delenv("UA_ALLOW_EXTERNAL_WORKSPACES", raising=False)
        assert security_paths.allow_external_workspaces_from_env() is False

    def test_whitespace_around_value(self, monkeypatch):
        monkeypatch.setenv("UA_ALLOW_EXTERNAL_WORKSPACES", "  true  ")
        assert security_paths.allow_external_workspaces_from_env() is True
