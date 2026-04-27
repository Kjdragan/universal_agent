"""
Google Workspace CLI (gws) MCP Bridge.

Manages the `gws mcp` subprocess as a stdio MCP server, providing
Google Workspace API access (Gmail, Calendar, Drive, Sheets, Docs)
through typed MCP tools.

Feature-gated by UA_ENABLE_GWS_CLI. When disabled, all Google Workspace
traffic continues to flow through Composio.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import shutil
from typing import Any, Optional

try:
    import logfire
except ImportError:  # pragma: no cover
    logfire = None  # type: ignore[assignment]

from universal_agent.feature_flags import gws_cli_enabled

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

# gws returns structured JSON errors: {"error": {"code": 403, "message": "...", "status": "..."}}
_RETRY_CODES = {429, 500, 502, 503, 504}


def classify_gws_error(error_payload: dict[str, Any]) -> str:
    """
    Classify a gws JSON error payload into a short category string.

    Returns one of: "auth", "scope", "rate_limit", "transient", "permanent".
    """
    err = error_payload.get("error", error_payload)
    code = int(err.get("code", 0))
    status = str(err.get("status", "")).upper()
    message = str(err.get("message", "")).lower()

    if code == 401 or status == "UNAUTHENTICATED":
        return "auth"
    if code == 403:
        if "insufficient" in message and "scope" in message:
            return "scope"
        if "permission" in message or status == "PERMISSION_DENIED":
            return "auth"
        return "permanent"
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        return "rate_limit"
    if code in _RETRY_CODES:
        return "transient"
    return "permanent"


def log_gws_error(tool_name: str, error_payload: dict[str, Any]) -> None:
    """Log a classified gws error via Logfire and Python logger."""
    err = error_payload.get("error", error_payload)
    code = int(err.get("code", 0))
    message = err.get("message", "unknown error")
    category = classify_gws_error(error_payload)

    logger.warning("gws MCP error [%s]: %s %s", category, code, message)
    if logfire is not None:
        logfire.warning(
            "gws_mcp_error",
            tool_name=tool_name,
            error_code=code,
            error_category=category,
            error_message=message,
        )


def log_gws_lifecycle(event: str, **kwargs: Any) -> None:
    """Log a gws MCP subprocess lifecycle event."""
    logger.info("gws MCP lifecycle [%s]: %s", event, kwargs)
    if logfire is not None:
        logfire.info("gws_mcp_lifecycle", event=event, **kwargs)


def log_gws_fallback(reason: str, **kwargs: Any) -> None:
    """Log when routing falls back from gws to Composio."""
    logger.warning("gws MCP fallback: %s", reason)
    if logfire is not None:
        logfire.warning("gws_mcp_fallback", reason=reason, **kwargs)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_SERVICES = "gmail,calendar,drive,sheets"


@dataclass
class GwsMcpConfig:
    """Configuration for the gws MCP stdio server."""

    binary_path: str = "gws"
    services: str = _DEFAULT_SERVICES
    enable_helpers: bool = True
    enable_workflows: bool = True
    tool_mode: str = "full"
    enable_sanitize: bool = False
    credentials_file: str = ""
    impersonated_user: str = ""
    token: str = ""

    @classmethod
    def from_env(cls) -> GwsMcpConfig:
        """Load configuration from environment variables."""
        return cls(
            binary_path=os.getenv("UA_GWS_BINARY_PATH", "gws").strip() or "gws",
            services=os.getenv("UA_GWS_SERVICES", _DEFAULT_SERVICES).strip() or _DEFAULT_SERVICES,
            enable_helpers=os.getenv("UA_GWS_ENABLE_HELPERS", "1").strip() != "0",
            enable_workflows=os.getenv("UA_GWS_ENABLE_WORKFLOWS", "1").strip() != "0",
            tool_mode=os.getenv("UA_GWS_TOOL_MODE", "full").strip() or "full",
            enable_sanitize=os.getenv("UA_GWS_ENABLE_SANITIZE", "0").strip() == "1",
            credentials_file=os.getenv("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE", "").strip(),
            impersonated_user=os.getenv("GOOGLE_WORKSPACE_CLI_IMPERSONATED_USER", "").strip(),
            token=os.getenv("GOOGLE_WORKSPACE_CLI_TOKEN", "").strip(),
        )

    def build_args(self) -> list[str]:
        """Build CLI arguments for `gws mcp`."""
        args = ["mcp"]
        if self.services:
            args.extend(["-s", self.services])
        if self.enable_helpers:
            args.append("-e")
        if self.enable_workflows:
            args.append("-w")
        if self.tool_mode and self.tool_mode != "full":
            args.extend(["--tool-mode", self.tool_mode])
        return args

    def build_env(self) -> dict[str, str]:
        """Build environment variables to pass to the gws subprocess."""
        env: dict[str, str] = {}
        if self.credentials_file:
            env["GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"] = self.credentials_file
        if self.impersonated_user:
            env["GOOGLE_WORKSPACE_CLI_IMPERSONATED_USER"] = self.impersonated_user
        if self.token:
            env["GOOGLE_WORKSPACE_CLI_TOKEN"] = self.token
        return env


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------


def is_gws_available(config: Optional[GwsMcpConfig] = None) -> bool:
    """Check if the gws binary exists on $PATH (or at the configured path)."""
    binary = (config.binary_path if config else "gws")
    return shutil.which(binary) is not None


# ---------------------------------------------------------------------------
# MCP server dict builder (for agent_setup._build_mcp_servers)
# ---------------------------------------------------------------------------


def build_gws_mcp_server_config(config: Optional[GwsMcpConfig] = None) -> Optional[dict[str, Any]]:
    """
    Build the MCP server configuration dict for the gws stdio server.

    Returns None if:
    - The gws_cli_enabled() feature flag is False
    - The gws binary is not found on $PATH

    The returned dict is suitable for inclusion in the agent_setup
    _build_mcp_servers() return value.
    """
    if not gws_cli_enabled():
        logger.debug("gws MCP bridge: disabled (UA_ENABLE_GWS_CLI not set)")
        return None

    if config is None:
        config = GwsMcpConfig.from_env()

    if not is_gws_available(config):
        logger.warning(
            "gws MCP bridge: enabled but binary not found at '%s' — skipping",
            config.binary_path,
        )
        log_gws_fallback(
            "binary_not_found",
            binary_path=config.binary_path,
        )
        return None

    server_config: dict[str, Any] = {
        "type": "stdio",
        "command": config.binary_path,
        "args": config.build_args(),
    }

    env = config.build_env()
    if env:
        server_config["env"] = env

    logger.info(
        "gws MCP bridge: registering stdio server (services=%s, helpers=%s, workflows=%s)",
        config.services,
        config.enable_helpers,
        config.enable_workflows,
    )
    log_gws_lifecycle(
        "server_registered",
        services=config.services,
        helpers=config.enable_helpers,
        workflows=config.enable_workflows,
        tool_mode=config.tool_mode,
        binary=config.binary_path,
    )
    return server_config
