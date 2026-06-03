"""Tests for the watchdog briefing context block.

Background: 2026-05-20 Simone's morning digest missed every active
watchdog finding (csi_source_liveness critical, youtube_enrichment_coverage
critical, etc.) because briefings_agent.py had zero watchdog awareness.
P6 adds the missing wire-in so morning briefings surface what the
watchdog has been detecting.

2026-06-03: proactive_health findings are no longer parked as Task Hub
``needs_review`` rows (the Task Hub write was retired — see
``test_proactive_health_no_task_hub_write``). The briefing block now
sources ONLY from the live ``GET /api/v1/ops/proactive_health`` endpoint,
which always reflects current invariant state. There is no Task Hub
fallback: if the endpoint is unreachable or no UA_OPS_TOKEN is set, the
block is omitted rather than rendering a stale backlog.

Same lightweight pattern as the HN block + triage block: a kill-switch-
gated helper that returns either a markdown block or "" on failure.
Never raises — the briefing must not break because the watchdog query did.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from universal_agent.services.watchdog_briefing_context import (
    build_briefing_block,
)


def _ok_payload() -> dict:
    return {
        "overall_status": "ok",
        "generated_at_utc": "2026-05-20T20:00:00+00:00",
        "crons": [],
        "stale_tasks": {"count": 0, "samples": []},
        "parked_tasks": {"count": 0, "samples": []},
        "invariants": [],
    }


def _critical_payload() -> dict:
    return {
        "overall_status": "critical",
        "generated_at_utc": "2026-05-20T20:00:00+00:00",
        "crons": [],
        "stale_tasks": {"count": 0, "samples": []},
        "parked_tasks": {"count": 2, "samples": []},
        "invariants": [
            {
                "finding_id": "invariant:csi_source_liveness",
                "severity": "critical",
                "metric_key": "csi_source_liveness",
                "title": "CSI adapters producing events on schedule",
                "recommendation": "2 CSI adapters past expected silence threshold: threads_owned, threads_trends_broad.",
                "runbook_command": "sqlite3 /var/lib/.../csi.db ...",
                "observed_value": {"stale_count": 2},
            },
            {
                "finding_id": "invariant:disk_usage_health",
                "severity": "warn",
                "metric_key": "disk_usage_health",
                "title": "Disk usage across monitored mounts within safe range",
                "recommendation": "Disk pressure on / at 80%. Cleanup target: AGENT_RUN_WORKSPACES older than 14 days.",
                "runbook_command": "df -h; find /opt/...",
                "observed_value": {"worst_used_pct": 80},
            },
        ],
    }


@pytest.fixture
def memory_taskhub(tmp_path, monkeypatch):
    """Seed an activity DB with proactive_health:* rows.

    Post-2026-06-03 these rows must be IGNORED by the briefing block (the
    Task Hub backlog source was removed). The fixture exists to prove the
    rows are no longer read, not that they are surfaced.
    """
    db_path = tmp_path / "activity_state.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE task_hub_items (
            task_id TEXT PRIMARY KEY,
            source_kind TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)
    conn.execute("""
        INSERT INTO task_hub_items (task_id, source_kind, title, status, created_at, updated_at)
        VALUES (?, 'proactive_health', '[CRITICAL] CSI adapters dead', 'needs_review',
                '2026-05-20T19:00:00+00:00', '2026-05-20T19:00:00+00:00')
    """, ("proactive_health:invariant:csi_source_liveness",))
    conn.commit()
    conn.close()
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(db_path))
    yield db_path


def _mock_proactive_health_response(payload: dict):
    """Patch httpx to return a fake /api/v1/ops/proactive_health response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value=payload)
    return patch(
        "universal_agent.services.watchdog_briefing_context.httpx.get",
        return_value=mock_response,
    )


def test_kill_switch_returns_empty(monkeypatch):
    monkeypatch.setenv("UA_BRIEFING_WATCHDOG_BLOCK_ENABLED", "0")
    block = build_briefing_block()
    assert block == ""


def test_healthy_state_returns_empty(monkeypatch):
    """overall_status ok + no active findings -> empty block (no healthy noise)."""
    monkeypatch.setenv("UA_BRIEFING_WATCHDOG_BLOCK_ENABLED", "1")
    monkeypatch.setenv("UA_OPS_TOKEN", "test-token")
    monkeypatch.setenv("UA_GATEWAY_PORT", "8002")

    with _mock_proactive_health_response(_ok_payload()):
        block = build_briefing_block()
    assert block == ""


def test_critical_findings_render_in_block(monkeypatch):
    """A critical finding from the live endpoint must appear in the block with
    its metric + recommendation so Simone surfaces it in the briefing."""
    monkeypatch.setenv("UA_BRIEFING_WATCHDOG_BLOCK_ENABLED", "1")
    monkeypatch.setenv("UA_OPS_TOKEN", "test-token")
    monkeypatch.setenv("UA_GATEWAY_PORT", "8002")

    with _mock_proactive_health_response(_critical_payload()):
        block = build_briefing_block()

    assert block != ""
    assert "Watchdog" in block
    assert "csi_source_liveness" in block
    assert "critical" in block.lower()
    # The recommendation should be visible to Simone
    assert "CSI adapters past expected silence" in block


def test_taskhub_rows_are_no_longer_surfaced(monkeypatch, memory_taskhub):
    """Regression for the 2026-06-03 surface move: even with proactive_health
    rows present in the activity DB, an 'ok' live payload yields an EMPTY block.
    The Task Hub backlog is no longer a briefing source."""
    monkeypatch.setenv("UA_BRIEFING_WATCHDOG_BLOCK_ENABLED", "1")
    monkeypatch.setenv("UA_OPS_TOKEN", "test-token")
    monkeypatch.setenv("UA_GATEWAY_PORT", "8002")

    with _mock_proactive_health_response(_ok_payload()):
        block = build_briefing_block()

    assert block == ""
    assert "proactive_health:invariant:csi_source_liveness" not in block


def test_endpoint_unreachable_returns_empty(monkeypatch, memory_taskhub):
    """If the live endpoint fails, the block is omitted — there is no Task Hub
    fallback anymore. Better an absent section than a stale one."""
    monkeypatch.setenv("UA_BRIEFING_WATCHDOG_BLOCK_ENABLED", "1")
    monkeypatch.setenv("UA_OPS_TOKEN", "test-token")
    monkeypatch.setenv("UA_GATEWAY_PORT", "8002")

    with patch(
        "universal_agent.services.watchdog_briefing_context.httpx.get",
        side_effect=Exception("connection refused"),
    ):
        block = build_briefing_block()

    assert block == ""


def test_no_ops_token_returns_empty(monkeypatch, memory_taskhub):
    """No UA_OPS_TOKEN -> the endpoint call is skipped and the block is omitted
    (no Task Hub fallback)."""
    monkeypatch.setenv("UA_BRIEFING_WATCHDOG_BLOCK_ENABLED", "1")
    monkeypatch.delenv("UA_OPS_TOKEN", raising=False)

    block = build_briefing_block()

    assert block == ""


def test_block_never_raises(monkeypatch):
    """No token, no endpoint -> helper returns "" (a str), never raises."""
    monkeypatch.setenv("UA_BRIEFING_WATCHDOG_BLOCK_ENABLED", "1")
    monkeypatch.delenv("UA_OPS_TOKEN", raising=False)

    block = build_briefing_block()
    assert isinstance(block, str)
    assert block == ""
