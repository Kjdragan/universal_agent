"""
Integration smoke tests for the gws MCP bridge.

These tests require:
- The `gws` binary to be installed and on $PATH
- Valid gws auth credentials (~/.config/gws/credentials.enc or plain)

Run with:
    uv run pytest tests/integration/test_gws_mcp_smoke.py -v

Skip automatically if gws binary is not found.
"""

from __future__ import annotations

import json
import subprocess
import shutil

import pytest


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _gws_available() -> bool:
    return shutil.which("gws") is not None


def _gws_auth_valid() -> bool:
    """Check that gws auth reports token_valid=true."""
    try:
        result = subprocess.run(
            ["gws", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False
        data = json.loads(result.stdout)
        return bool(data.get("token_valid"))
    except Exception:
        return False


skip_no_gws = pytest.mark.skipif(
    not _gws_available(),
    reason="gws binary not found on $PATH",
)
skip_no_auth = pytest.mark.skipif(
    not _gws_auth_valid(),
    reason="gws auth credentials not valid or not configured",
)


# ---------------------------------------------------------------------------
# Binary / auth tests
# ---------------------------------------------------------------------------


@skip_no_gws
def test_gws_binary_is_found():
    assert shutil.which("gws") is not None


@skip_no_gws
def test_gws_auth_status_reports_token_valid():
    result = subprocess.run(
        ["gws", "auth", "status"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data.get("token_valid") is True, f"token_valid is not True: {data}"
    assert data.get("auth_method") == "oauth2"


# ---------------------------------------------------------------------------
# API smoke tests
# ---------------------------------------------------------------------------


@skip_no_gws
@skip_no_auth
def test_gmail_list_messages_returns_results():
    result = subprocess.run(
        ["gws", "gmail", "users", "messages", "list",
         "--params", '{"userId": "me", "maxResults": 1}'],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    data = json.loads(result.stdout)
    assert "messages" in data or "resultSizeEstimate" in data


@skip_no_gws
@skip_no_auth
def test_gmail_triage_helper_runs():
    result = subprocess.run(
        ["gws", "gmail", "+triage"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"


@skip_no_gws
@skip_no_auth
def test_calendar_agenda_helper_runs():
    result = subprocess.run(
        ["gws", "calendar", "+agenda"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"


# ---------------------------------------------------------------------------
# MCP server tests
# ---------------------------------------------------------------------------


@skip_no_gws
@skip_no_auth
def test_gws_mcp_server_lists_tools():
    """Start the gws MCP server and verify it exposes tools."""
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    result = subprocess.run(
        ["gws", "mcp", "-s", "gmail,calendar,drive,sheets", "-e", "-w"],
        input=request,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, f"MCP server failed: {result.stderr}"
    data = json.loads(result.stdout)
    tools = data.get("result", {}).get("tools", [])
    assert len(tools) > 50, f"Expected >50 tools, got {len(tools)}"
    tool_names = {t["name"] for t in tools}
    assert "gmail_users_messages_send" in tool_names
    assert "calendar_events_list" in tool_names
    assert "drive_files_list" in tool_names
    assert "sheets_spreadsheets_get" in tool_names


@skip_no_gws
@skip_no_auth
def test_gws_mcp_server_exposes_workflow_tools():
    """Verify workflow tools are included when -w flag is passed."""
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    result = subprocess.run(
        ["gws", "mcp", "-s", "gmail,calendar,drive,sheets", "-w"],
        input=request,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    tool_names = {t["name"] for t in data.get("result", {}).get("tools", [])}
    workflow_tools = [n for n in tool_names if n.startswith("workflow_")]
    assert len(workflow_tools) > 0, "No workflow tools found"
