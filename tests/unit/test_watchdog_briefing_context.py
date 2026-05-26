"""Tests for the watchdog briefing context block.

Background: 2026-05-20 Simone's morning digest missed every active
watchdog finding (csi_source_liveness critical, youtube_enrichment_coverage
critical, etc.) because briefings_agent.py had zero watchdog awareness.
P6 adds the missing wire-in so morning briefings surface what the
watchdog has been detecting.

Same lightweight pattern as the HN block + triage block: a kill-switch-
gated helper that returns either a markdown block or "" on failure.
Never raises — the briefing must not break because the watchdog query did.
"""

from __future__ import annotations

import json
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
                "recommendation": "3 CSI adapters past expected silence threshold: reddit_discovery, threads_owned, threads_trends_broad.",
                "runbook_command": "sqlite3 /var/lib/.../csi.db ...",
                "observed_value": {"stale_count": 3},
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
    """In-memory task_hub with proactive_health:* rows so the block sees them."""
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
    conn.execute("""
        INSERT INTO task_hub_items (task_id, source_kind, title, status, created_at, updated_at)
        VALUES (?, 'proactive_health', '[CRITICAL] YouTube enrichment lag', 'needs_review',
                '2026-05-20T18:30:00+00:00', '2026-05-20T18:30:00+00:00')
    """, ("proactive_health:invariant:youtube_enrichment_coverage",))
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


def test_kill_switch_returns_empty(monkeypatch, memory_taskhub):
    monkeypatch.setenv("UA_BRIEFING_WATCHDOG_BLOCK_ENABLED", "0")
    block = build_briefing_block()
    assert block == ""


def test_healthy_state_returns_empty_or_brief(monkeypatch, memory_taskhub):
    """When overall_status is ok and no parked rows, the block stays empty —
    don't pollute the briefing with healthy noise."""
    monkeypatch.setenv("UA_BRIEFING_WATCHDOG_BLOCK_ENABLED", "1")
    monkeypatch.setenv("UA_OPS_TOKEN", "test-token")
    monkeypatch.setenv("UA_GATEWAY_PORT", "8002")

    # Clear the seeded rows so the task_hub is empty
    conn = sqlite3.connect(str(memory_taskhub))
    conn.execute("DELETE FROM task_hub_items")
    conn.commit()
    conn.close()

    with _mock_proactive_health_response(_ok_payload()):
        block = build_briefing_block()
    assert block == ""


def test_critical_findings_render_in_block(monkeypatch, memory_taskhub):
    """A critical finding must appear in the block with its title +
    recommendation so Simone surfaces it in the briefing."""
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


def test_parked_taskhub_rows_appear_in_block(monkeypatch, memory_taskhub):
    """proactive_health:* Task Hub rows are persistent state — include them
    in the block as a backlog section."""
    monkeypatch.setenv("UA_BRIEFING_WATCHDOG_BLOCK_ENABLED", "1")
    monkeypatch.setenv("UA_OPS_TOKEN", "test-token")
    monkeypatch.setenv("UA_GATEWAY_PORT", "8002")

    with _mock_proactive_health_response(_ok_payload()):
        block = build_briefing_block()

    # task_hub rows are present even when current findings are quiet
    assert block != ""
    assert "proactive_health:invariant:csi_source_liveness" in block
    assert "proactive_health:invariant:youtube_enrichment_coverage" in block


def test_endpoint_unreachable_block_still_uses_taskhub(monkeypatch, memory_taskhub):
    """If the endpoint times out / fails, we still have task_hub persistent
    state — don't return empty just because one source failed."""
    monkeypatch.setenv("UA_BRIEFING_WATCHDOG_BLOCK_ENABLED", "1")
    monkeypatch.setenv("UA_OPS_TOKEN", "test-token")
    monkeypatch.setenv("UA_GATEWAY_PORT", "8002")

    with patch(
        "universal_agent.services.watchdog_briefing_context.httpx.get",
        side_effect=Exception("connection refused"),
    ):
        block = build_briefing_block()

    # Endpoint failed but task_hub rows ARE there from the fixture
    assert block != ""
    assert "proactive_health" in block


def test_no_ops_token_block_still_uses_taskhub(monkeypatch, memory_taskhub):
    """If UA_OPS_TOKEN isn't set, skip the endpoint call but still surface
    the task_hub backlog. Avoid total silence."""
    monkeypatch.setenv("UA_BRIEFING_WATCHDOG_BLOCK_ENABLED", "1")
    monkeypatch.delenv("UA_OPS_TOKEN", raising=False)

    block = build_briefing_block()

    assert "proactive_health:invariant:csi_source_liveness" in block


def test_block_never_raises_on_bad_db(monkeypatch, tmp_path):
    """If the activity DB doesn't exist (dev box), helper returns "" not raise."""
    monkeypatch.setenv("UA_BRIEFING_WATCHDOG_BLOCK_ENABLED", "1")
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(tmp_path / "does_not_exist.db"))
    monkeypatch.delenv("UA_OPS_TOKEN", raising=False)

    # Should NOT raise
    block = build_briefing_block()
    assert isinstance(block, str)
