"""Integration test for GET /api/v1/ops/proactive_health.

Narrow monkey-patch approach: stub the gateway hooks the endpoint reaches
(``_task_hub_open_conn``, ``_cron_service``, ``_csi_default_db_path``) and
hit the route via TestClient.  This avoids spinning the full gateway
fixture for a read-only aggregator.
"""

from __future__ import annotations

import importlib
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from universal_agent import gateway_server
from universal_agent.services import pipeline_invariants as pi
from universal_agent.services.pipeline_invariants import clear_registry_for_tests

TASK_HUB_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_hub_items (
    task_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test starts with only the YouTube invariant registered."""
    clear_registry_for_tests()
    from universal_agent.services.invariants import youtube_invariants

    importlib.reload(youtube_invariants)
    yield
    clear_registry_for_tests()


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    # Disable ops auth for the test.
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "")

    activity_db = tmp_path / "activity.db"

    def _open_conn():
        conn = sqlite3.connect(str(activity_db))
        conn.row_factory = sqlite3.Row
        conn.executescript(TASK_HUB_SCHEMA)
        return conn

    # Seed an empty activity DB with just the columns we query.
    _open_conn().close()

    monkeypatch.setattr(gateway_server, "_task_hub_open_conn", _open_conn)
    monkeypatch.setattr(gateway_server, "_cron_service", None)
    monkeypatch.setattr(
        gateway_server, "_csi_default_db_path", lambda: tmp_path / "csi.db"
    )
    return TestClient(gateway_server.app), activity_db, tmp_path


def test_endpoint_returns_ok_payload_for_empty_state(client):
    test_client, _activity_db, _tmp = client
    resp = test_client.get("/api/v1/ops/proactive_health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall_status"] == "ok"
    assert data["crons"] == []
    assert data["stale_tasks"]["count"] == 0
    assert data["parked_tasks"]["count"] == 0
    assert data["invariants"] == []
    assert "generated_at_utc" in data


def test_endpoint_surfaces_parked_task_as_warn(client):
    test_client, activity_db, _tmp = client
    conn = sqlite3.connect(str(activity_db))
    try:
        conn.execute(
            "INSERT INTO task_hub_items (task_id, source_kind, title, status, "
            "created_at, updated_at) VALUES "
            "('park1', 'cron', 'parked cron task', 'needs_review', "
            "datetime('now'), datetime('now'))"
        )
        conn.commit()
    finally:
        conn.close()

    resp = test_client.get("/api/v1/ops/proactive_health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall_status"] == "warn"
    assert data["parked_tasks"]["count"] == 1
    assert data["parked_tasks"]["samples"][0]["task_id"] == "park1"


def test_endpoint_surfaces_invariant_failure_as_critical(client, tmp_path: Path):
    test_client, _activity_db, _tmp = client
    csi_db = tmp_path / "csi.db"
    conn = sqlite3.connect(str(csi_db))
    try:
        conn.executescript(
            """
            CREATE TABLE rss_event_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                source TEXT NOT NULL DEFAULT 'youtube_channel_rss',
                transcript_status TEXT NOT NULL DEFAULT 'missing',
                analyzed_at TEXT DEFAULT (datetime('now'))
            );
            """
        )
        for i in range(10):
            conn.execute(
                "INSERT INTO rss_event_analysis (event_id, source, transcript_status) "
                "VALUES (?, 'youtube_channel_rss', 'missing')",
                (f"e{i}",),
            )
        conn.commit()
    finally:
        conn.close()

    resp = test_client.get("/api/v1/ops/proactive_health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall_status"] == "critical"
    assert len(data["invariants"]) == 1
    f = data["invariants"][0]
    assert f["metric_key"] == "youtube_transcript_coverage"
    assert f["severity"] == "critical"
    assert f["category"] == "proactive_health"


def test_endpoint_requires_ops_auth_when_token_configured(client, monkeypatch):
    test_client, _activity_db, _tmp = client
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "secret")
    resp = test_client.get("/api/v1/ops/proactive_health")
    # _require_ops_auth raises HTTPException(401 or 403); accept either
    assert resp.status_code in (401, 403)
