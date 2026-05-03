"""Phase 1B — sweeper async loop, API endpoint shapes,
LLM-annotation preservation across mechanical re-polls.

Frontend smoke is covered by manual verification + visual review;
unit-test scope here is the backend API contract and the loop
behavior the gateway lifespan depends on.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from universal_agent.services.mission_control_db import open_store
from universal_agent.services.mission_control_intelligence_sweeper import (
    MissionControlSweeper,
    SweeperConfig,
    reset_sweeper_for_tests,
    run_sweeper_loop,
)
from universal_agent.services.mission_control_tiles import (
    COLOR_GREEN,
    COLOR_YELLOW,
    Tile,
    TileState,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def activity_db(tmp_path: Path) -> sqlite3.Connection:
    path = tmp_path / "activity.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE activity_events (
            id TEXT PRIMARY KEY,
            event_class TEXT NOT NULL DEFAULT 'notification',
            source_domain TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            full_message TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            status TEXT NOT NULL DEFAULT 'new',
            requires_action INTEGER NOT NULL DEFAULT 0,
            session_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            entity_ref_json TEXT NOT NULL DEFAULT '{}',
            actions_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE task_hub_items (
            task_id TEXT PRIMARY KEY,
            source_kind TEXT NOT NULL,
            source_ref TEXT,
            title TEXT NOT NULL,
            description TEXT,
            project_key TEXT,
            priority INTEGER DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            agent_ready INTEGER DEFAULT 0
        );
        """
    )
    return conn


def _iso_minutes_ago(minutes: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _insert_event(conn, **fields):
    defaults = {
        "id": fields.get("id", "ev_default"),
        "source_domain": "csi",
        "kind": "noop",
        "title": "synthetic",
        "summary": "synthetic event",
        "full_message": "synthetic event",
        "severity": "info",
        "status": "new",
        "requires_action": 0,
        "created_at": _iso_minutes_ago(0),
        "updated_at": _iso_minutes_ago(0),
        "metadata_json": "{}",
    }
    defaults.update(fields)
    conn.execute(
        """
        INSERT OR REPLACE INTO activity_events
            (id, source_domain, kind, title, summary, full_message,
             severity, status, requires_action, created_at, updated_at,
             metadata_json)
        VALUES
            (:id, :source_domain, :kind, :title, :summary, :full_message,
             :severity, :status, :requires_action, :created_at, :updated_at,
             :metadata_json)
        """,
        defaults,
    )


class _FixtureSweeper(MissionControlSweeper):
    def __init__(self, activity_conn, mc_db_path: Path) -> None:
        super().__init__(SweeperConfig())
        self._activity_conn = activity_conn
        import os

        os.environ["UA_MISSION_CONTROL_INTEL_DB_PATH"] = str(mc_db_path)

    def _open_activity_db(self):
        return _NonClosingProxy(self._activity_conn)


class _NonClosingProxy:
    def __init__(self, conn) -> None:
        self._conn = conn

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def close(self) -> None:
        pass


# ── run_sweeper_loop integration with the lifespan stop_event ──────────


@pytest.mark.asyncio
async def test_run_sweeper_loop_exits_promptly_when_stop_event_set(monkeypatch):
    """The lifespan shutdown sets `stop_event`; the loop must exit
    within one interval cycle. We verify by configuring a short
    interval and asserting the coroutine returns within a generous
    safety margin.
    """
    monkeypatch.delenv("UA_MC_PHASE_1_ENABLED", raising=False)
    # Reset the singleton so our SweeperConfig override (via env) is
    # picked up freshly.
    reset_sweeper_for_tests()
    monkeypatch.setenv("UA_MISSION_CONTROL_SWEEPER_INTERVAL_S", "0.05")

    stop_event = asyncio.Event()
    task = asyncio.create_task(run_sweeper_loop(stop_event))
    await asyncio.sleep(0.05)
    stop_event.set()
    # Should exit within a single interval + a bit of grace.
    await asyncio.wait_for(task, timeout=1.5)
    assert task.done()
    reset_sweeper_for_tests()


@pytest.mark.asyncio
async def test_run_sweeper_loop_continues_on_tick_exception(monkeypatch):
    """If `tick()` raises, the loop catches and continues. This
    protects the gateway from a single bad tile crashing the entire
    intelligence pipeline."""
    monkeypatch.setenv("UA_MISSION_CONTROL_SWEEPER_INTERVAL_S", "0.05")
    reset_sweeper_for_tests()

    from universal_agent.services import mission_control_intelligence_sweeper as mcis

    call_counter = {"n": 0}

    class _BoomSweeper(MissionControlSweeper):
        def tick(self):  # type: ignore[override]
            call_counter["n"] += 1
            raise RuntimeError("synthetic tick failure")

    boom = _BoomSweeper()

    def _factory():
        return boom

    monkeypatch.setattr(mcis, "get_sweeper", _factory)

    stop_event = asyncio.Event()
    task = asyncio.create_task(run_sweeper_loop(stop_event))
    await asyncio.sleep(0.2)  # several intervals worth
    stop_event.set()
    await asyncio.wait_for(task, timeout=1.5)
    # We expect multiple ticks attempted despite each raising
    assert call_counter["n"] >= 2
    reset_sweeper_for_tests()


# ── Annotation preservation across mechanical re-polls ─────────────────


def test_existing_llm_annotation_is_preserved_when_color_unchanged(
    monkeypatch, activity_db, tmp_path
):
    """Phase 5 will write LLM-enriched `current_annotation` text. Phase 1
    sweeps should NOT overwrite that enrichment with the mechanical
    one-line-status when the color hasn't changed. Verifies via direct
    DB manipulation simulating a Phase 5 annotation write."""
    monkeypatch.setenv("UA_MC_PHASE_1_ENABLED", "1")
    sweeper = _FixtureSweeper(activity_db, tmp_path / "mc.db")
    sweeper.tick()

    # Simulate a Phase 5+ LLM annotation write on the gateway tile.
    mc_conn = open_store(tmp_path / "mc.db")
    try:
        mc_conn.execute(
            """
            UPDATE mission_control_tile_states
            SET current_annotation = 'LLM-enriched explanation: gateway is fine',
                last_annotation_at = datetime('now')
            WHERE tile_id = 'gateway'
            """
        )
        before = mc_conn.execute(
            "SELECT current_annotation FROM mission_control_tile_states WHERE tile_id='gateway'"
        ).fetchone()
        assert "LLM-enriched" in (before["current_annotation"] or "")
    finally:
        mc_conn.close()

    # Run another tick — color should be the same (no events were
    # added/removed). Phase 1 should preserve the LLM annotation.
    sweeper.tick()

    mc_conn = open_store(tmp_path / "mc.db")
    try:
        after = mc_conn.execute(
            "SELECT current_annotation FROM mission_control_tile_states WHERE tile_id='gateway'"
        ).fetchone()
        assert "LLM-enriched" in (after["current_annotation"] or ""), (
            "mechanical re-poll overwrote LLM annotation"
        )
    finally:
        mc_conn.close()


def test_annotation_replaced_when_color_transitions(
    monkeypatch, activity_db, tmp_path
):
    """When the color CHANGES, we DO want the mechanical status to
    overwrite the prior annotation — the prior annotation explained
    the prior state and is no longer accurate."""
    monkeypatch.setenv("UA_MC_PHASE_1_ENABLED", "1")
    _insert_event(activity_db, id="ev1", source_domain="csi", created_at=_iso_minutes_ago(0.5))
    sweeper = _FixtureSweeper(activity_db, tmp_path / "mc.db")
    sweeper.tick()  # CSI green

    mc_conn = open_store(tmp_path / "mc.db")
    try:
        mc_conn.execute(
            """
            UPDATE mission_control_tile_states
            SET current_annotation = 'LLM said all good',
                last_annotation_at = datetime('now')
            WHERE tile_id = 'csi_ingester'
            """
        )
    finally:
        mc_conn.close()

    # Force CSI red: delete recent events.
    activity_db.execute("DELETE FROM activity_events WHERE source_domain='csi'")
    sweeper.tick()

    mc_conn = open_store(tmp_path / "mc.db")
    try:
        after = mc_conn.execute(
            "SELECT current_state, current_annotation FROM mission_control_tile_states "
            "WHERE tile_id='csi_ingester'"
        ).fetchone()
        assert after["current_state"] == "red"
        # Mechanical text overwrites stale "all good" annotation
        assert "LLM said all good" not in (after["current_annotation"] or "")
        assert after["current_annotation"]
    finally:
        mc_conn.close()


# ── API endpoint shape ──────────────────────────────────────────────────
# We don't bring up the full FastAPI app for unit tests — that would
# require the entire gateway stack to import cleanly which is too heavy
# for fast unit feedback. Instead we exercise the underlying functions
# the endpoints depend on so the contract stays sound.


def test_tile_states_serialize_with_one_line_status(
    monkeypatch, activity_db, tmp_path
):
    """The endpoint surfaces `current_annotation` as the one_line_status
    for the frontend. Verify the column is populated by a tick."""
    monkeypatch.setenv("UA_MC_PHASE_1_ENABLED", "1")
    _insert_event(activity_db, id="ev1", source_domain="csi", created_at=_iso_minutes_ago(0.5))
    sweeper = _FixtureSweeper(activity_db, tmp_path / "mc.db")
    sweeper.tick()

    conn = open_store(tmp_path / "mc.db")
    try:
        rows = conn.execute(
            "SELECT tile_id, current_state, current_annotation, evidence_payload_json "
            "FROM mission_control_tile_states ORDER BY tile_id"
        ).fetchall()
        assert len(rows) == 9
        for row in rows:
            assert row["current_annotation"], (
                f"tile {row['tile_id']} has empty annotation; frontend will show blank line"
            )
            evidence = json.loads(row["evidence_payload_json"]) if row["evidence_payload_json"] else {}
            assert isinstance(evidence, dict)
    finally:
        conn.close()


def test_card_endpoint_hydrates_json_columns(monkeypatch, tmp_path):
    """The /cards endpoint pre-parses JSON columns so the frontend
    doesn't have to. Verify the hydration logic by simulating it
    directly: insert a card, list it, parse the JSON columns."""
    from universal_agent.services.mission_control_cards import (
        CardUpsert,
        list_live_cards,
        upsert_card,
    )

    conn = open_store(tmp_path / "mc.db")
    try:
        upsert_card(
            conn,
            CardUpsert(
                subject_kind="infrastructure",
                subject_id="infra:gateway",
                severity="warning",
                title="Gateway: silent for 8m",
                narrative="Long narrative " * 100,
                why_it_matters="Why " * 50,
                tags=["infra", "gateway"],
                evidence_refs=[
                    {"kind": "tile", "id": "gateway", "uri": "/x", "label": "Gateway tile"}
                ],
                evidence_payload={"age_seconds": 480, "last_event_iso": "2026-05-03T12:00:00Z"},
            ),
        )
        cards = list_live_cards(conn)
        assert len(cards) == 1
        # The endpoint expects these columns to be valid JSON. Parse them
        # the way the endpoint does and verify shapes.
        card = cards[0]
        for json_field in (
            "tags_json",
            "evidence_refs_json",
            "evidence_payload_json",
            "synthesis_history_json",
            "dispatch_history_json",
            "operator_feedback_json",
        ):
            raw = card.get(json_field)
            assert raw is not None, f"{json_field} should never be NULL"
            json.loads(raw)  # must parse
    finally:
        conn.close()


# ── Sweeper config from env via lifespan ───────────────────────────────


def test_sweeper_config_loop_interval_respected(monkeypatch):
    monkeypatch.setenv("UA_MISSION_CONTROL_SWEEPER_INTERVAL_S", "5")
    reset_sweeper_for_tests()
    cfg = SweeperConfig.from_env()
    assert cfg.interval_seconds == 5.0
    reset_sweeper_for_tests()
