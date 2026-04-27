"""Unit tests for the gws MCP bridge module."""

import os
import shutil
from unittest import mock

import pytest

from universal_agent.services.gws_mcp_bridge import (
    GwsMcpConfig,
    build_gws_mcp_server_config,
    classify_gws_error,
    is_gws_available,
    log_gws_error,
    log_gws_fallback,
    log_gws_lifecycle,
)

# ---------------------------------------------------------------------------
# GwsMcpConfig tests
# ---------------------------------------------------------------------------


class TestGwsMcpConfig:
    def test_default_config(self):
        cfg = GwsMcpConfig()
        assert cfg.binary_path == "gws"
        assert cfg.services == "gmail,calendar,drive,sheets"
        assert cfg.enable_helpers is True
        assert cfg.enable_workflows is True
        assert cfg.tool_mode == "full"

    def test_build_args_defaults(self):
        cfg = GwsMcpConfig()
        args = cfg.build_args()
        assert args == ["mcp", "-s", "gmail,calendar,drive,sheets", "-e", "-w"]

    def test_build_args_no_helpers_no_workflows(self):
        cfg = GwsMcpConfig(enable_helpers=False, enable_workflows=False)
        args = cfg.build_args()
        assert "-e" not in args
        assert "-w" not in args

    def test_build_args_compact_mode(self):
        cfg = GwsMcpConfig(tool_mode="compact")
        args = cfg.build_args()
        assert "--tool-mode" in args
        assert "compact" in args

    def test_build_args_full_mode_omitted(self):
        cfg = GwsMcpConfig(tool_mode="full")
        args = cfg.build_args()
        assert "--tool-mode" not in args

    def test_build_args_custom_services(self):
        cfg = GwsMcpConfig(services="gmail,drive")
        args = cfg.build_args()
        assert "-s" in args
        idx = args.index("-s")
        assert args[idx + 1] == "gmail,drive"

    def test_build_env_empty_when_no_creds(self):
        cfg = GwsMcpConfig()
        assert cfg.build_env() == {}

    def test_build_env_with_credentials(self):
        cfg = GwsMcpConfig(
            credentials_file="/path/to/creds.json",
            impersonated_user="user@example.com",
            token="tok123",
        )
        env = cfg.build_env()
        assert env["GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"] == "/path/to/creds.json"
        assert env["GOOGLE_WORKSPACE_CLI_IMPERSONATED_USER"] == "user@example.com"
        assert env["GOOGLE_WORKSPACE_CLI_TOKEN"] == "tok123"

    def test_from_env_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = GwsMcpConfig.from_env()
            assert cfg.binary_path == "gws"
            assert cfg.services == "gmail,calendar,drive,sheets"
            assert cfg.enable_helpers is True
            assert cfg.enable_workflows is True

    def test_from_env_custom(self):
        env = {
            "UA_GWS_BINARY_PATH": "/usr/local/bin/gws",
            "UA_GWS_SERVICES": "gmail,drive",
            "UA_GWS_ENABLE_HELPERS": "0",
            "UA_GWS_ENABLE_WORKFLOWS": "0",
            "UA_GWS_TOOL_MODE": "compact",
            "UA_GWS_ENABLE_SANITIZE": "1",
            "GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE": "/creds.json",
            "GOOGLE_WORKSPACE_CLI_IMPERSONATED_USER": "admin@corp.com",
            "GOOGLE_WORKSPACE_CLI_TOKEN": "mytoken",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = GwsMcpConfig.from_env()
            assert cfg.binary_path == "/usr/local/bin/gws"
            assert cfg.services == "gmail,drive"
            assert cfg.enable_helpers is False
            assert cfg.enable_workflows is False
            assert cfg.tool_mode == "compact"
            assert cfg.enable_sanitize is True
            assert cfg.credentials_file == "/creds.json"
            assert cfg.impersonated_user == "admin@corp.com"
            assert cfg.token == "mytoken"


# ---------------------------------------------------------------------------
# is_gws_available tests
# ---------------------------------------------------------------------------


class TestIsGwsAvailable:
    def test_returns_false_when_binary_missing(self):
        cfg = GwsMcpConfig(binary_path="__nonexistent_binary_12345__")
        assert is_gws_available(cfg) is False

    def test_returns_true_when_binary_on_path(self):
        with mock.patch.object(shutil, "which", return_value="/usr/bin/gws"):
            assert is_gws_available() is True


# ---------------------------------------------------------------------------
# build_gws_mcp_server_config tests
# ---------------------------------------------------------------------------


class TestBuildGwsMcpServerConfig:
    def test_returns_none_when_flag_disabled(self):
        with mock.patch.dict(os.environ, {"UA_ENABLE_GWS_CLI": "0"}, clear=False):
            result = build_gws_mcp_server_config()
            assert result is None

    def test_returns_none_when_flag_not_set(self):
        env = {k: v for k, v in os.environ.items() if k not in ("UA_ENABLE_GWS_CLI", "UA_DISABLE_GWS_CLI")}
        with mock.patch.dict(os.environ, env, clear=True):
            result = build_gws_mcp_server_config()
            assert result is None

    def test_returns_none_when_binary_missing(self):
        with mock.patch.dict(os.environ, {"UA_ENABLE_GWS_CLI": "1"}, clear=False):
            with mock.patch.object(shutil, "which", return_value=None):
                result = build_gws_mcp_server_config()
                assert result is None

    def test_returns_config_when_enabled_and_available(self):
        with mock.patch.dict(os.environ, {"UA_ENABLE_GWS_CLI": "1"}, clear=False):
            with mock.patch.object(shutil, "which", return_value="/usr/bin/gws"):
                result = build_gws_mcp_server_config()
                assert result is not None
                assert result["type"] == "stdio"
                assert result["command"] == "gws"
                assert "mcp" in result["args"]
                assert "-s" in result["args"]

    def test_config_includes_env_when_credentials_set(self):
        env = {
            "UA_ENABLE_GWS_CLI": "1",
            "GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE": "/path/creds.json",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(shutil, "which", return_value="/usr/bin/gws"):
                result = build_gws_mcp_server_config()
                assert result is not None
                assert "env" in result
                assert result["env"]["GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"] == "/path/creds.json"

    def test_disable_flag_overrides_enable(self):
        env = {"UA_ENABLE_GWS_CLI": "1", "UA_DISABLE_GWS_CLI": "1"}
        with mock.patch.dict(os.environ, env, clear=False):
            result = build_gws_mcp_server_config()
            assert result is None


# ---------------------------------------------------------------------------
# classify_gws_error tests
# ---------------------------------------------------------------------------


class TestClassifyGwsError:
    def test_401_is_auth(self):
        assert classify_gws_error({"error": {"code": 401, "message": "Unauthorized"}}) == "auth"

    def test_unauthenticated_status_is_auth(self):
        assert classify_gws_error({"error": {"code": 401, "status": "UNAUTHENTICATED"}}) == "auth"

    def test_403_insufficient_scope_is_scope(self):
        payload = {"error": {"code": 403, "message": "Request had insufficient scope for the method."}}
        assert classify_gws_error(payload) == "scope"

    def test_403_permission_denied_is_auth(self):
        payload = {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "Permission denied."}}
        assert classify_gws_error(payload) == "auth"

    def test_403_other_is_permanent(self):
        payload = {"error": {"code": 403, "message": "Forbidden resource."}}
        assert classify_gws_error(payload) == "permanent"

    def test_429_is_rate_limit(self):
        assert classify_gws_error({"error": {"code": 429, "message": "Too many requests"}}) == "rate_limit"

    def test_resource_exhausted_is_rate_limit(self):
        assert classify_gws_error({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}) == "rate_limit"

    def test_500_is_transient(self):
        assert classify_gws_error({"error": {"code": 500, "message": "Internal server error"}}) == "transient"

    def test_503_is_transient(self):
        assert classify_gws_error({"error": {"code": 503, "message": "Service unavailable"}}) == "transient"

    def test_404_is_permanent(self):
        assert classify_gws_error({"error": {"code": 404, "message": "Not found"}}) == "permanent"

    def test_flat_payload_without_error_key(self):
        assert classify_gws_error({"code": 401, "message": "Unauthorized"}) == "auth"


# ---------------------------------------------------------------------------
# log_gws_* smoke tests (no logfire — just verify no exceptions raised)
# ---------------------------------------------------------------------------


class TestLogGwsFunctions:
    def test_log_gws_error_does_not_raise(self):
        log_gws_error("gmail_users_messages_send", {"error": {"code": 401, "message": "Unauthorized"}})

    def test_log_gws_lifecycle_does_not_raise(self):
        log_gws_lifecycle("server_registered", services="gmail", helpers=True)

    def test_log_gws_fallback_does_not_raise(self):
        log_gws_fallback("binary_not_found", binary_path="gws")
