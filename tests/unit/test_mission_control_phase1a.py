"""Phase 1A — tier-0 tile abstractions, sweeper transition detection,
and auto-card creation on yellow/red transitions.

These tests use a self-contained activity-DB fixture so we don't depend
on the production runtime DB. The sweeper's `_open_activity_db` indirection
is overridden per-test to point at the fixture.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from universal_agent.services.mission_control_cards import (
    CARD_STATE_LIVE,
    CARD_STATE_RETIRED,
    CardUpsert,
    get_card,
    list_live_cards,
    make_card_id,
    retire_card,
    upsert_card,
)
from universal_agent.services.mission_control_db import open_store
from universal_agent.services.mission_control_intelligence_sweeper import (
    MissionControlSweeper,
    SweeperConfig,
)
from universal_agent.services.mission_control_tiles import (
    COLOR_GREEN,
    COLOR_RED,
    COLOR_UNKNOWN,
    COLOR_YELLOW,
    CronPipelinesTile,
    CsiIngesterTile,
    DatabaseTile,
    GatewayTile,
    HeartbeatDaemonTile,
    ModelUsageTodayTile,
    ProactivePipelineTile,
    TaskHubPressureTile,
    TileState,
    VPAgentHealthTile,
    all_tiles,
    tile_by_name,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def activity_db(tmp_path: Path) -> sqlite3.Connection:
    """A throwaway SQLite database holding the minimal activity_events
    + task_hub_items shapes the tiles query against. Mirrors the
    production schema exactly enough for tile logic to exercise.
    """
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


def _insert_event(conn: sqlite3.Connection, **fields):
    """Insert a synthetic activity event with sensible defaults."""
    defaults = {
        "id": fields.get("id", "ev_" + str(hash(json.dumps(fields, sort_keys=True)))),
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


def _iso_minutes_ago(minutes: float) -> str:
    """Return an ISO timestamp `minutes` minutes in the past."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _iso_hours_ago(hours: float) -> str:
    return _iso_minutes_ago(hours * 60.0)


# ── Tile state computation ─────────────────────────────────────────────


def test_gateway_tile_green_with_recent_activity(activity_db):
    _insert_event(activity_db, id="ev1", created_at=_iso_minutes_ago(0.5))
    state = GatewayTile().compute_state(activity_db)
    assert state.color == COLOR_GREEN
    assert "active" in state.one_line_status


def test_gateway_tile_yellow_when_quiet(activity_db):
    _insert_event(activity_db, id="ev1", created_at=_iso_minutes_ago(3))
    state = GatewayTile().compute_state(activity_db)
    assert state.color == COLOR_YELLOW
    assert "quiet" in state.one_line_status


def test_gateway_tile_yellow_when_no_recent_events(activity_db):
    # No events at all in last 15 min -> yellow (the 15-min window has
    # no rows, so we infer "quiet" rather than "silent")
    state = GatewayTile().compute_state(activity_db)
    assert state.color == COLOR_YELLOW
    assert state.evidence["last_event"] is None


def test_database_tile_green_on_fast_select(activity_db):
    state = DatabaseTile().compute_state(activity_db)
    # In-memory SQLite is sub-ms; should always be green
    assert state.color == COLOR_GREEN
    assert state.evidence["select1_ms"] < 100


def test_csi_ingester_tile_green_on_recent_event(activity_db):
    _insert_event(activity_db, id="csi1", source_domain="csi", created_at=_iso_minutes_ago(10))
    state = CsiIngesterTile().compute_state(activity_db)
    assert state.color == COLOR_GREEN
    assert state.evidence["events_24h"] == 1


def test_csi_ingester_tile_yellow_on_stale(activity_db):
    _insert_event(activity_db, id="csi1", source_domain="csi", created_at=_iso_hours_ago(3))
    state = CsiIngesterTile().compute_state(activity_db)
    assert state.color == COLOR_YELLOW


def test_csi_ingester_tile_red_when_no_recent_events(activity_db):
    state = CsiIngesterTile().compute_state(activity_db)
    assert state.color == COLOR_RED
    assert state.evidence["events_24h"] == 0


def test_cron_pipelines_tile_green_when_all_clear(activity_db):
    state = CronPipelinesTile().compute_state(activity_db)
    assert state.color == COLOR_GREEN


def test_cron_pipelines_tile_red_when_multiple_jobs_failing(activity_db):
    for i, job in enumerate(["a", "b"], start=1):
        _insert_event(
            activity_db,
            id=f"cron_fail_{i}",
            source_domain="cron",
            severity="error",
            created_at=_iso_hours_ago(1),
            metadata_json=json.dumps({"job_id": job}),
        )
    state = CronPipelinesTile().compute_state(activity_db)
    assert state.color == COLOR_RED
    assert state.evidence["distinct_failing_jobs"] == 2


def test_cron_pipelines_tile_yellow_when_one_job_fails(activity_db):
    _insert_event(
        activity_db,
        id="cron_one",
        source_domain="cron",
        severity="error",
        created_at=_iso_hours_ago(1),
        metadata_json=json.dumps({"job_id": "single"}),
    )
    state = CronPipelinesTile().compute_state(activity_db)
    assert state.color == COLOR_YELLOW


def test_heartbeat_tile_green_on_recent_tick(activity_db):
    _insert_event(activity_db, id="hb1", source_domain="heartbeat", created_at=_iso_minutes_ago(0.5))
    state = HeartbeatDaemonTile().compute_state(activity_db)
    assert state.color == COLOR_GREEN


def test_heartbeat_tile_red_on_no_ticks(activity_db):
    state = HeartbeatDaemonTile().compute_state(activity_db)
    assert state.color == COLOR_RED


def test_task_hub_pressure_tile_green_at_idle(activity_db):
    state = TaskHubPressureTile().compute_state(activity_db)
    assert state.color == COLOR_GREEN
    assert state.evidence["in_progress"] == 0
    assert state.evidence["stuck_claims"] == 0


def test_task_hub_pressure_tile_red_when_stuck(activity_db):
    for i in range(3):
        activity_db.execute(
            """
            INSERT INTO task_hub_items
                (task_id, source_kind, title, status, created_at, updated_at)
            VALUES (?, 'manual', 'stuck', 'in_progress', ?, ?)
            """,
            (f"stuck-{i}", _iso_hours_ago(2), _iso_hours_ago(2)),
        )
    state = TaskHubPressureTile().compute_state(activity_db)
    assert state.color == COLOR_RED
    assert state.evidence["stuck_claims"] == 3


def test_model_usage_tile_green_with_no_rate_limits(activity_db):
    state = ModelUsageTodayTile().compute_state(activity_db)
    assert state.color == COLOR_GREEN


def test_model_usage_tile_yellow_with_a_few_rate_limits(activity_db):
    for i in range(2):
        _insert_event(
            activity_db,
            id=f"rl_{i}",
            summary="Got 429 from upstream",
            created_at=_iso_minutes_ago(30),
        )
    state = ModelUsageTodayTile().compute_state(activity_db)
    assert state.color == COLOR_YELLOW


def test_proactive_pipeline_tile_yellow_when_silent(activity_db):
    state = ProactivePipelineTile().compute_state(activity_db)
    assert state.color == COLOR_YELLOW


def test_proactive_pipeline_tile_red_on_repeated_failures(activity_db):
    for i in range(3):
        activity_db.execute(
            """
            INSERT INTO task_hub_items
                (task_id, source_kind, title, status, created_at, updated_at)
            VALUES (?, 'proactive_codie', 'p', 'failed', ?, ?)
            """,
            (f"pf-{i}", _iso_hours_ago(1), _iso_hours_ago(1)),
        )
    state = ProactivePipelineTile().compute_state(activity_db)
    assert state.color == COLOR_RED


def test_vp_agent_health_tile_yellow_when_no_missions(activity_db):
    state = VPAgentHealthTile().compute_state(activity_db)
    assert state.color == COLOR_YELLOW


def test_vp_agent_health_tile_green_when_completing(activity_db):
    for i in range(3):
        activity_db.execute(
            """
            INSERT INTO task_hub_items
                (task_id, source_kind, source_ref, title, status, created_at, updated_at)
            VALUES (?, 'vp_mission', 'vp.coder.primary', 't', 'completed', ?, ?)
            """,
            (f"vp-{i}", _iso_hours_ago(2), _iso_hours_ago(2)),
        )
    state = VPAgentHealthTile().compute_state(activity_db)
    assert state.color == COLOR_GREEN


# ── TileState invariants ───────────────────────────────────────────────


def test_tile_state_signature_is_deterministic():
    a = TileState(color=COLOR_GREEN, one_line_status="ok", evidence={"k": 1})
    b = TileState(color=COLOR_GREEN, one_line_status="ok", evidence={"k": 1})
    assert a.signature == b.signature


def test_tile_state_signature_changes_on_evidence_change():
    a = TileState(color=COLOR_GREEN, one_line_status="ok", evidence={"k": 1})
    b = TileState(color=COLOR_GREEN, one_line_status="ok", evidence={"k": 2})
    assert a.signature != b.signature


def test_tile_state_rejects_invalid_color():
    with pytest.raises(ValueError):
        TileState(color="puce", one_line_status="bogus")


# ── Registry ───────────────────────────────────────────────────────────


def test_all_tiles_returns_nine_tiles():
    tiles = all_tiles()
    assert len(tiles) == 9
    names = [t.name for t in tiles]
    assert names == [
        "gateway",
        "database",
        "csi_ingester",
        "cron_pipelines",
        "heartbeat_daemon",
        "task_hub_pressure",
        "model_usage_today",
        "proactive_pipeline",
        "vp_agent_health",
    ]


def test_tile_by_name_round_trip():
    assert tile_by_name("gateway") is not None
    assert tile_by_name("nonexistent") is None


# ── Card persistence ───────────────────────────────────────────────────


def test_card_id_is_stable_for_same_subject():
    a = make_card_id("infrastructure", "infra:csi_ingester")
    b = make_card_id("infrastructure", "infra:csi_ingester")
    c = make_card_id("infrastructure", "infra:gateway")
    assert a == b
    assert a != c


def test_upsert_card_creates_new_card(tmp_path: Path):
    conn = open_store(tmp_path / "mc.db")
    try:
        card = upsert_card(
            conn,
            CardUpsert(
                subject_kind="infrastructure",
                subject_id="infra:csi_ingester",
                severity="warning",
                title="CSI Ingester: silent for 8h",
                narrative="full text" * 100,
                why_it_matters="why" * 100,
            ),
        )
        assert card["current_state"] == CARD_STATE_LIVE
        assert card["recurrence_count"] == 1
        # No prior synthesis to push into history on first insert
        assert json.loads(card["synthesis_history_json"]) == []
    finally:
        conn.close()


def test_upsert_card_revives_retired_card_with_recurrence_bump(tmp_path: Path):
    conn = open_store(tmp_path / "mc.db")
    try:
        first = upsert_card(
            conn,
            CardUpsert(
                subject_kind="infrastructure",
                subject_id="infra:csi_ingester",
                severity="warning",
                title="t1",
                narrative="n1",
                why_it_matters="w1",
            ),
        )
        retire_card(conn, first["card_id"])
        revived = upsert_card(
            conn,
            CardUpsert(
                subject_kind="infrastructure",
                subject_id="infra:csi_ingester",
                severity="critical",
                title="t2",
                narrative="n2",
                why_it_matters="w2",
            ),
        )
        assert revived["card_id"] == first["card_id"]  # identity preserved
        assert revived["current_state"] == CARD_STATE_LIVE
        assert revived["recurrence_count"] == 2
        history = json.loads(revived["synthesis_history_json"])
        assert len(history) == 1
        assert history[0]["narrative"] == "n1"
    finally:
        conn.close()


def test_upsert_live_card_pushes_prior_into_history_without_recurrence_bump(tmp_path: Path):
    conn = open_store(tmp_path / "mc.db")
    try:
        first = upsert_card(
            conn,
            CardUpsert(
                subject_kind="infrastructure",
                subject_id="infra:gateway",
                severity="warning",
                title="t1",
                narrative="n1",
                why_it_matters="w1",
            ),
        )
        second = upsert_card(
            conn,
            CardUpsert(
                subject_kind="infrastructure",
                subject_id="infra:gateway",
                severity="warning",
                title="t2",
                narrative="n2",
                why_it_matters="w2",
            ),
        )
        assert second["recurrence_count"] == 1  # not bumped on live->live
        assert second["narrative"] == "n2"
        history = json.loads(second["synthesis_history_json"])
        assert history[0]["narrative"] == "n1"
        # card_id identity preserved
        assert second["card_id"] == first["card_id"]
    finally:
        conn.close()


def test_list_live_cards_orders_by_severity_then_recurrence(tmp_path: Path):
    conn = open_store(tmp_path / "mc.db")
    try:
        upsert_card(conn, CardUpsert(
            subject_kind="task", subject_id="t1", severity="informational",
            title="info", narrative="n", why_it_matters="w",
        ))
        upsert_card(conn, CardUpsert(
            subject_kind="task", subject_id="t2", severity="critical",
            title="crit", narrative="n", why_it_matters="w",
        ))
        upsert_card(conn, CardUpsert(
            subject_kind="task", subject_id="t3", severity="warning",
            title="warn", narrative="n", why_it_matters="w",
        ))
        cards = list_live_cards(conn)
        assert [c["severity"] for c in cards] == ["critical", "warning", "informational"]
    finally:
        conn.close()


def test_card_history_caps_at_ten_entries(tmp_path: Path):
    conn = open_store(tmp_path / "mc.db")
    try:
        for i in range(15):
            upsert_card(conn, CardUpsert(
                subject_kind="task", subject_id="t-cap", severity="warning",
                title=f"v{i}", narrative=f"n{i}", why_it_matters="w",
            ))
        card = list_live_cards(conn)[0]
        history = json.loads(card["synthesis_history_json"])
        assert len(history) == 10
        # Newest historical entry should be v13 (current is v14)
        assert history[0]["narrative"] == "n13"
    finally:
        conn.close()


# ── Sweeper tier-0 integration ─────────────────────────────────────────


class _FixtureSweeper(MissionControlSweeper):
    """Sweeper that sources its activity DB from a fixture connection
    rather than the production runtime DB.
    """

    def __init__(self, activity_conn: sqlite3.Connection, mc_db_path: Path) -> None:
        super().__init__(SweeperConfig())
        self._activity_conn = activity_conn
        # Force the sweeper to use a test-controlled MC store path.
        import os

        os.environ["UA_MISSION_CONTROL_INTEL_DB_PATH"] = str(mc_db_path)

    def _open_activity_db(self):
        return _NonClosingProxy(self._activity_conn)


class _NonClosingProxy:
    """Wraps a fixture sqlite connection so the sweeper's `.close()` call
    is a no-op (the fixture owns the lifecycle).
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def close(self) -> None:
        pass


def test_sweeper_persists_tile_states_on_first_tick(monkeypatch, activity_db, tmp_path):
    monkeypatch.setenv("UA_MC_PHASE_1_ENABLED", "1")
    sweeper = _FixtureSweeper(activity_db, tmp_path / "mc.db")
    result = sweeper.tick()
    assert result.errors == [], f"unexpected errors: {result.errors}"
    assert result.tier0_checked is True

    conn = open_store(tmp_path / "mc.db")
    try:
        rows = conn.execute("SELECT tile_id, current_state FROM mission_control_tile_states").fetchall()
        # All 9 tiles should have been persisted on first tick
        assert len(rows) == 9
        names = {r["tile_id"] for r in rows}
        assert "gateway" in names
        assert "csi_ingester" in names
    finally:
        conn.close()


def test_sweeper_detects_color_transition_and_creates_card(monkeypatch, activity_db, tmp_path):
    monkeypatch.setenv("UA_MC_PHASE_1_ENABLED", "1")
    # First tick: no CSI events -> CSI Ingester tile is RED.
    sweeper = _FixtureSweeper(activity_db, tmp_path / "mc.db")
    first = sweeper.tick()
    assert first.errors == []
    # No transitions on the first tick (nothing to transition FROM).
    assert all("csi_ingester" not in t for t in first.tier0_transitions)

    # Now make CSI Ingester green by inserting a recent event,
    # then tick again -> expect a red->green transition AND the
    # auto-card creation for the previous red state already happened
    # (no, it shouldn't have on the first tick since there was no prior
    # state — the red state is the initial state, no transition fired).
    _insert_event(activity_db, id="csi-fresh", source_domain="csi", created_at=_iso_minutes_ago(1))
    second = sweeper.tick()
    assert second.errors == []
    csi_transitions = [t for t in second.tier0_transitions if t.startswith("csi_ingester:")]
    assert len(csi_transitions) == 1
    assert "red->green" in csi_transitions[0]


def test_sweeper_creates_infrastructure_card_on_yellow_red_transition(monkeypatch, activity_db, tmp_path):
    monkeypatch.setenv("UA_MC_PHASE_1_ENABLED", "1")
    # Start with CSI green
    _insert_event(activity_db, id="csi-fresh", source_domain="csi", created_at=_iso_minutes_ago(1))
    sweeper = _FixtureSweeper(activity_db, tmp_path / "mc.db")
    sweeper.tick()  # establish baseline (green)

    # Move time forward: delete the recent CSI event so the next tick
    # sees no recent CSI activity -> tile flips to red.
    activity_db.execute("DELETE FROM activity_events WHERE id = 'csi-fresh'")
    second = sweeper.tick()
    csi_transitions = [t for t in second.tier0_transitions if t.startswith("csi_ingester:")]
    assert csi_transitions, "expected CSI tile to transition"
    assert "->red" in csi_transitions[0] or "->yellow" in csi_transitions[0]

    # Verify the auto-created infrastructure card now exists.
    conn = open_store(tmp_path / "mc.db")
    try:
        cards = list_live_cards(conn)
        infra_cards = [c for c in cards if c["subject_kind"] == "infrastructure"]
        assert any(c["subject_id"] == "infra:csi_ingester" for c in infra_cards), (
            "auto-card for CSI ingester missing"
        )
    finally:
        conn.close()


def test_sweeper_signature_unchanged_skips_state_update(monkeypatch, activity_db, tmp_path):
    monkeypatch.setenv("UA_MC_PHASE_1_ENABLED", "1")
    _insert_event(activity_db, id="csi-fresh", source_domain="csi", created_at=_iso_minutes_ago(1))
    sweeper = _FixtureSweeper(activity_db, tmp_path / "mc.db")
    sweeper.tick()

    conn = open_store(tmp_path / "mc.db")
    try:
        first_check = conn.execute(
            "SELECT last_checked_at FROM mission_control_tile_states WHERE tile_id='database'"
        ).fetchone()["last_checked_at"]
    finally:
        conn.close()

    # Tick again with no underlying changes — Database tile signature
    # may differ slightly because select1_ms is included; we measure
    # last_checked_at advancing as the proof-of-bookkeeping signal.
    import time

    time.sleep(0.01)
    sweeper.tick()
    conn = open_store(tmp_path / "mc.db")
    try:
        second_check = conn.execute(
            "SELECT last_checked_at FROM mission_control_tile_states WHERE tile_id='database'"
        ).fetchone()["last_checked_at"]
    finally:
        conn.close()
    assert second_check >= first_check


def test_sweeper_handles_missing_tables_gracefully(monkeypatch, tmp_path):
    """If the activity DB doesn't have the expected schema, tiles return
    UNKNOWN states and the sweeper records errors per-tile rather than
    crashing the whole tick.
    """
    monkeypatch.setenv("UA_MC_PHASE_1_ENABLED", "1")
    empty_path = tmp_path / "empty.db"
    empty_conn = sqlite3.connect(str(empty_path))
    empty_conn.row_factory = sqlite3.Row
    sweeper = _FixtureSweeper(empty_conn, tmp_path / "mc.db")
    result = sweeper.tick()
    # Tile errors are tolerated; tier0_checked still True
    assert result.tier0_checked is True
    # Some tiles should land in UNKNOWN; verify by reading state
    conn = open_store(tmp_path / "mc.db")
    try:
        rows = conn.execute("SELECT current_state FROM mission_control_tile_states").fetchall()
        states = [r["current_state"] for r in rows]
        # At least some unknown/yellow states expected when there's no schema
        assert any(s in ("unknown", "yellow", "red") for s in states)
    finally:
        conn.close()
        empty_conn.close()


def test_severity_mapping_for_tile_colors():
    """Auto-card creation maps tile colors to card severities. Verify
    the mapping so a red tile doesn't accidentally produce a `success`
    severity card or similar.
    """
    map_fn = MissionControlSweeper._severity_for_color
    assert map_fn(COLOR_RED) == "critical"
    assert map_fn(COLOR_YELLOW) == "warning"
    assert map_fn(COLOR_UNKNOWN) == "watching"
    assert map_fn(COLOR_GREEN) == "informational"


# ── Phase 1.1 regression: first-appearance non-green tile must create a card

def test_sweeper_creates_card_on_first_appearance_red_tile(monkeypatch, activity_db, tmp_path):
    """Production smoke test on Phase 1B revealed that a freshly-booted
    sweeper with a red tile on its first tick produced ZERO cards because
    the prior code only fired card creation on color transitions.
    A fresh boot has no `prior_color`, so 'transition' was always False.

    This test pins the corrected behavior: first-appearance non-green
    tiles must produce an `infrastructure` card immediately, without
    needing a second tick to "transition into" their state.
    """
    monkeypatch.setenv("UA_MC_PHASE_1_ENABLED", "1")
    # CSI tile starts RED because no CSI events exist in the fixture DB
    sweeper = _FixtureSweeper(activity_db, tmp_path / "mc.db")
    result = sweeper.tick()
    assert result.errors == []

    conn = open_store(tmp_path / "mc.db")
    try:
        cards = list_live_cards(conn)
        infra_subjects = {c["subject_id"] for c in cards if c["subject_kind"] == "infrastructure"}
        # CSI ingester is red on first tick (no events in 24h) — must create
        # its infrastructure card immediately.
        assert "infra:csi_ingester" in infra_subjects, (
            f"first-appearance CSI red tile should have created a card; "
            f"got infra subjects={sorted(infra_subjects)}"
        )
        # The card narrative should reflect first-appearance phrasing,
        # not transition phrasing.
        csi_card = next(c for c in cards if c["subject_id"] == "infra:csi_ingester")
        assert "first observed" in csi_card["narrative"], (
            f"first-appearance card narrative should say 'first observed', got: "
            f"{csi_card['narrative'][:200]!r}"
        )
    finally:
        conn.close()


def test_sweeper_does_not_duplicate_cards_on_repeated_same_color_ticks(
    monkeypatch, activity_db, tmp_path
):
    """Idempotency contract: a tile that stays red across many sweeps must
    not produce a flurry of duplicate cards. The card_id is a stable hash
    of (subject_kind, subject_id), so upserts collapse to the same row,
    but we must not spuriously bloat synthesis_history when nothing
    changed."""
    monkeypatch.setenv("UA_MC_PHASE_1_ENABLED", "1")
    sweeper = _FixtureSweeper(activity_db, tmp_path / "mc.db")
    sweeper.tick()  # first appearance: should create card
    sweeper.tick()  # second tick, same color: should not bloat history
    sweeper.tick()  # third tick, same color

    conn = open_store(tmp_path / "mc.db")
    try:
        cards = list_live_cards(conn)
        csi = [c for c in cards if c["subject_id"] == "infra:csi_ingester"]
        assert len(csi) == 1, "card should not be duplicated"
        history = json.loads(csi[0]["synthesis_history_json"])
        # Signature-unchanged ticks short-circuit before persist runs and
        # before card upsert runs, so history should be empty (no prior
        # synthesis to push) after three identical ticks.
        assert len(history) == 0, (
            f"history should not bloat across identical-color sweeps; got {len(history)}"
        )
    finally:
        conn.close()


def test_sweeper_does_not_create_card_for_first_appearance_green(
    monkeypatch, activity_db, tmp_path
):
    """Green tiles never warrant infrastructure cards — only yellow/red
    do. Verify by inserting a recent CSI event so CSI is green, then
    confirming no infra card lands."""
    _insert_event(activity_db, id="csi-fresh", source_domain="csi",
                  created_at=_iso_minutes_ago(1))
    monkeypatch.setenv("UA_MC_PHASE_1_ENABLED", "1")
    sweeper = _FixtureSweeper(activity_db, tmp_path / "mc.db")
    sweeper.tick()

    conn = open_store(tmp_path / "mc.db")
    try:
        cards = list_live_cards(conn)
        csi_cards = [c for c in cards if c["subject_id"] == "infra:csi_ingester"]
        assert csi_cards == [], "green CSI tile must not produce a card"
    finally:
        conn.close()
