"""Tests for ideation backpressure (top-9 handoff, task 6).

Faucet gate: emission pauses while pending > threshold with no recent
review activity. Drain nudge: the morning report switches to a ranked
top-5 batch + bottleneck banner. Near-duplicate pre-create dedup: a
reworded proposal collapses onto the existing open row.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.services import ideation_report, reflection_engine
from universal_agent.tools import task_hub_bridge


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _seed_reflections(conn, n, *, status="open", agent_ready=0, updated_days_ago=10.0, score=None):
    stamp = (datetime.now(timezone.utc) - timedelta(days=updated_days_ago)).isoformat()
    for i in range(n):
        task_hub.upsert_item(
            conn,
            {
                "task_id": f"refl:{status}:{agent_ready}:{updated_days_ago}:{i}",
                "source_kind": "reflection",
                "title": f"proposal {status} {i} {updated_days_ago}",
                "status": status,
                "agent_ready": agent_ready,
                "score": score,
            },
        )
        conn.execute(
            "UPDATE task_hub_items SET updated_at=?, created_at=? WHERE task_id=?",
            (stamp, stamp, f"refl:{status}:{agent_ready}:{updated_days_ago}:{i}"),
        )
    conn.commit()


# ── faucet gate ─────────────────────────────────────────────────────────────


def test_backpressure_fires_when_backlog_large_and_stalled(monkeypatch):
    monkeypatch.setenv("UA_IDEATION_BACKPRESSURE_PENDING", "10")
    import importlib

    importlib.reload(reflection_engine)
    conn = _conn()
    _seed_reflections(conn, 12, updated_days_ago=10.0)
    reason = reflection_engine.ideation_backpressure_reason(conn)
    assert reason is not None
    assert "12" in reason


def test_backpressure_quiet_below_threshold(monkeypatch):
    monkeypatch.setenv("UA_IDEATION_BACKPRESSURE_PENDING", "10")
    import importlib

    importlib.reload(reflection_engine)
    conn = _conn()
    _seed_reflections(conn, 5, updated_days_ago=10.0)
    assert reflection_engine.ideation_backpressure_reason(conn) is None


def test_backpressure_reopens_on_recent_review_activity(monkeypatch):
    """A recent promote (agent_ready=1, fresh updated_at) reopens the faucet."""
    monkeypatch.setenv("UA_IDEATION_BACKPRESSURE_PENDING", "10")
    import importlib

    importlib.reload(reflection_engine)
    conn = _conn()
    _seed_reflections(conn, 12, updated_days_ago=10.0)
    _seed_reflections(conn, 1, agent_ready=1, updated_days_ago=1.0)
    assert reflection_engine.ideation_backpressure_reason(conn) is None


def test_backpressure_reopens_on_recent_dismissal(monkeypatch):
    monkeypatch.setenv("UA_IDEATION_BACKPRESSURE_PENDING", "10")
    import importlib

    importlib.reload(reflection_engine)
    conn = _conn()
    _seed_reflections(conn, 12, updated_days_ago=10.0)
    _seed_reflections(conn, 1, status="cancelled", updated_days_ago=0.5)
    assert reflection_engine.ideation_backpressure_reason(conn) is None


# ── drain nudge (report) ────────────────────────────────────────────────────


def test_ranked_top5_prefers_score_then_age():
    conn = _conn()
    _seed_reflections(conn, 8, updated_days_ago=3.0)
    _seed_reflections(conn, 1, updated_days_ago=1.0, score=9.5)
    ranked = ideation_report.get_held_proposals_ranked(conn, limit=5)
    assert len(ranked) == 5
    assert ranked[0]["score"] == 9.5


@pytest.mark.asyncio
async def test_report_switches_to_drain_view_under_backpressure(monkeypatch):
    monkeypatch.setenv("UA_IDEATION_BACKPRESSURE_PENDING", "10")
    import importlib

    importlib.reload(reflection_engine)
    conn = _conn()
    _seed_reflections(conn, 12, updated_days_ago=10.0)

    sent = {}

    class _Mail:
        async def send_email(self, **kwargs):
            sent.update(kwargs)
            return {"status": "sent", "message_id": "m1"}

    monkeypatch.setattr(
        ideation_report, "publish_html_to_scratch", lambda *a, **k: "https://scratch/x"
    )
    result = await ideation_report.deliver_ideation_report(conn, _Mail(), "op@example.com")
    assert result["status"] == "delivered"
    assert "decision-throughput" in sent["html"]
    assert "top 5" in sent["subject"].lower() or "top 5" in sent["text"].lower()
    # Drain view carries at most 5 proposal cards, not the raw 12.
    assert sent["html"].count("✓ Promote") <= 6  # 5 cards (+possible stale overlap)


@pytest.mark.asyncio
async def test_report_normal_view_without_backpressure(monkeypatch):
    monkeypatch.setenv("UA_IDEATION_BACKPRESSURE_PENDING", "1000")
    import importlib

    importlib.reload(reflection_engine)
    conn = _conn()
    _seed_reflections(conn, 3, updated_days_ago=1.0)

    sent = {}

    class _Mail:
        async def send_email(self, **kwargs):
            sent.update(kwargs)
            return {"status": "sent", "message_id": "m1"}

    monkeypatch.setattr(
        ideation_report, "publish_html_to_scratch", lambda *a, **k: "https://scratch/x"
    )
    result = await ideation_report.deliver_ideation_report(conn, _Mail(), "op@example.com")
    assert result["status"] == "delivered"
    assert "decision-throughput" not in sent["html"]


# ── near-duplicate pre-create dedup ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_reworded_reflection_collapses_onto_existing(monkeypatch, tmp_path):
    """A REWORDED near-duplicate (exact-title key misses) still dedups via
    the Jaccard token-overlap backstop."""
    db_path = str(tmp_path / "activity_state.db")
    with sqlite3.connect(db_path) as c:
        c.row_factory = sqlite3.Row
        task_hub.ensure_schema(c)
    monkeypatch.setattr(task_hub_bridge, "get_activity_db_path", lambda: db_path)

    import json

    def _payload(result):
        return json.loads(result["content"][0]["text"])

    # The ideator's real near-dup pattern: the structured description body is
    # re-emitted nearly verbatim while the TITLE is reworded (so the
    # exact-title incident_key misses).
    description = (
        "**Rationale:** the hackernews ingestion pipeline fails on transient "
        "errors because fetches have no retry backoff, dropping signal. "
        "**First concrete step:** wrap the fetch call in exponential backoff. "
        "**Effort:** S **Suggested executor:** Cody"
    )
    first = _payload(
        await task_hub_bridge._task_hub_create_impl(
            {
                "title": "Add retry backoff to the hackernews ingestion pipeline",
                "description": description,
                "source_kind": "reflection",
            }
        )
    )
    second = _payload(
        await task_hub_bridge._task_hub_create_impl(
            {
                "title": "Hackernews fetches should retry with exponential backoff",
                "description": description,
                "source_kind": "reflection",
            }
        )
    )
    assert second.get("deduplicated") is True
    assert second.get("dedup_kind") == "near_duplicate"
    assert second["task_id"] == first["task_id"]


@pytest.mark.asyncio
async def test_distinct_reflection_still_creates(monkeypatch, tmp_path):
    db_path = str(tmp_path / "activity_state.db")
    with sqlite3.connect(db_path) as c:
        c.row_factory = sqlite3.Row
        task_hub.ensure_schema(c)
    monkeypatch.setattr(task_hub_bridge, "get_activity_db_path", lambda: db_path)

    import json

    def _payload(result):
        return json.loads(result["content"][0]["text"])

    first = _payload(
        await task_hub_bridge._task_hub_create_impl(
            {
                "title": "Add retry backoff to the hackernews ingestion pipeline",
                "description": "Pipeline retries.",
                "source_kind": "reflection",
            }
        )
    )
    second = _payload(
        await task_hub_bridge._task_hub_create_impl(
            {
                "title": "Build a weekly digest of vault entity coverage gaps",
                "description": "Completely different idea about vault coverage reporting.",
                "source_kind": "reflection",
            }
        )
    )
    assert second.get("deduplicated") is not True
    assert second["task_id"] != first["task_id"]
