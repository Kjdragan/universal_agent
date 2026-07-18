"""Tests for heartbeat environment context injection."""
from __future__ import annotations

import pytest


def test_build_heartbeat_environment_context_contains_factory_identity(monkeypatch):
    """Verify the context includes factory slug, role, hostname, and workspace."""
    monkeypatch.setenv("UA_MACHINE_SLUG", "vps-hq-production")
    monkeypatch.setenv("FACTORY_ROLE", "HEADQUARTERS")

    from universal_agent.heartbeat_service import _build_heartbeat_environment_context

    ctx = _build_heartbeat_environment_context("/opt/ua/AGENT_RUN_WORKSPACES/session_test_123")

    assert "vps-hq-production" in ctx
    assert "HEADQUARTERS" in ctx
    assert "/opt/ua/AGENT_RUN_WORKSPACES/session_test_123" in ctx
    # Must tell agent it's local
    assert "LOCALLY" in ctx
    assert "Do NOT SSH" in ctx


def test_build_heartbeat_environment_context_local_worker(monkeypatch):
    """Verify the context adapts for a LOCAL_WORKER factory (e.g., desktop)."""
    monkeypatch.setenv("UA_MACHINE_SLUG", "kevins-desktop")
    monkeypatch.setenv("FACTORY_ROLE", "LOCAL_WORKER")

    from universal_agent.heartbeat_service import _build_heartbeat_environment_context

    ctx = _build_heartbeat_environment_context("/home/user/workspace/session_abc")

    assert "kevins-desktop" in ctx
    assert "LOCAL_WORKER" in ctx
    assert "/home/user/workspace/session_abc" in ctx


def test_build_heartbeat_environment_context_write_rules(monkeypatch):
    """Verify file write guidance is present."""
    monkeypatch.setenv("UA_MACHINE_SLUG", "test-node")
    monkeypatch.setenv("FACTORY_ROLE", "HEADQUARTERS")

    from universal_agent.heartbeat_service import _build_heartbeat_environment_context

    ctx = _build_heartbeat_environment_context("/tmp/ws")

    assert "write_text_file" in ctx
    assert "SEQUENTIALLY" in ctx
    assert "sibling failures cascade" in ctx
    assert "Do NOT use the native `Write` tool" in ctx


def test_build_heartbeat_environment_context_health_check_efficiency(monkeypatch):
    """Verify health check consolidation guidance is present."""
    monkeypatch.setenv("UA_MACHINE_SLUG", "test-node")
    monkeypatch.setenv("FACTORY_ROLE", "HEADQUARTERS")

    from universal_agent.heartbeat_service import _build_heartbeat_environment_context

    ctx = _build_heartbeat_environment_context("/tmp/ws")

    assert "Combine multiple shell health checks" in ctx
    assert "&&" in ctx


def test_compose_heartbeat_prompt_with_workspace_dir_injects_context(monkeypatch):
    """When workspace_dir is provided, environment context is injected into the prompt."""
    monkeypatch.setenv("UA_MACHINE_SLUG", "vps-test")
    monkeypatch.setenv("FACTORY_ROLE", "HEADQUARTERS")

    from universal_agent.heartbeat_service import _compose_heartbeat_prompt

    prompt = _compose_heartbeat_prompt(
        "Check HEARTBEAT.md",
        investigation_only=False,
        task_hub_claims=[],
        workspace_dir="/opt/ua/ws/session_test",
    )

    assert "Check HEARTBEAT.md" in prompt
    assert "## Heartbeat Environment Context" in prompt
    assert "vps-test" in prompt
    assert "/opt/ua/ws/session_test" in prompt


def test_compose_heartbeat_prompt_without_workspace_dir_no_context(monkeypatch):
    """When workspace_dir is empty, no environment context is injected (backward compat)."""
    from universal_agent.heartbeat_service import _compose_heartbeat_prompt

    prompt = _compose_heartbeat_prompt(
        "Check HEARTBEAT.md",
        investigation_only=False,
        task_hub_claims=[],
        workspace_dir="",
    )

    assert "Check HEARTBEAT.md" in prompt
    assert "Heartbeat Environment Context" not in prompt


def test_compose_heartbeat_prompt_default_workspace_dir_param():
    """workspace_dir defaults to empty string (backward compat with existing callers)."""
    from universal_agent.heartbeat_service import _compose_heartbeat_prompt

    # Should work without workspace_dir kwarg at all
    prompt = _compose_heartbeat_prompt(
        "Check HEARTBEAT.md",
        investigation_only=False,
        task_hub_claims=[],
    )

    assert "Check HEARTBEAT.md" in prompt
    assert "Heartbeat Environment Context" not in prompt


def test_compose_heartbeat_prompt_task_focused_uses_lean_context_and_skips_blocks(monkeypatch):
    """R4: task_focused=True uses the lean env context and omits brainstorm/morning-
    report/recent-topics/DB-health/proactive-health blocks."""
    monkeypatch.setenv("UA_MACHINE_SLUG", "vps-test")
    monkeypatch.setenv("FACTORY_ROLE", "HEADQUARTERS")

    from universal_agent.heartbeat_service import _compose_heartbeat_prompt

    prompt = _compose_heartbeat_prompt(
        "Check HEARTBEAT.md",
        investigation_only=False,
        task_hub_claims=[],
        workspace_dir="/opt/ua/ws/session_test",
        brainstorm_context_text="BRAINSTORM_MARKER_TEXT",
        morning_report_text="MORNING_REPORT_MARKER_TEXT",
        recent_topics_text="RECENT_TOPICS_MARKER_TEXT",
        task_focused=True,
    )

    # Lean env context in, verbose one out.
    assert "## Task Execution Environment" in prompt
    assert "## Heartbeat Environment Context" not in prompt
    assert "No System Monitoring" in prompt

    # Gated blocks absent.
    assert "BRAINSTORM_MARKER_TEXT" not in prompt
    assert "MORNING_REPORT_MARKER_TEXT" not in prompt
    assert "RECENT_TOPICS_MARKER_TEXT" not in prompt
    assert "== DATABASE HEALTH ALERTS ==" not in prompt
    assert "== PROACTIVE HEALTH" not in prompt


def test_compose_heartbeat_prompt_not_task_focused_keeps_blocks_regression(monkeypatch):
    """Regression: task_focused=False (the historical default) still injects the
    verbose env context and the gated advisor blocks when text is supplied."""
    monkeypatch.setenv("UA_MACHINE_SLUG", "vps-test")
    monkeypatch.setenv("FACTORY_ROLE", "HEADQUARTERS")

    from universal_agent.heartbeat_service import _compose_heartbeat_prompt

    prompt = _compose_heartbeat_prompt(
        "Check HEARTBEAT.md",
        investigation_only=False,
        task_hub_claims=[],
        workspace_dir="/opt/ua/ws/session_test",
        brainstorm_context_text="BRAINSTORM_MARKER_TEXT",
        morning_report_text="MORNING_REPORT_MARKER_TEXT",
        recent_topics_text="RECENT_TOPICS_MARKER_TEXT",
        task_focused=False,
    )

    assert "## Heartbeat Environment Context" in prompt
    assert "## Task Execution Environment" not in prompt
    assert "BRAINSTORM_MARKER_TEXT" in prompt
    assert "MORNING_REPORT_MARKER_TEXT" in prompt
    assert "RECENT_TOPICS_MARKER_TEXT" in prompt
