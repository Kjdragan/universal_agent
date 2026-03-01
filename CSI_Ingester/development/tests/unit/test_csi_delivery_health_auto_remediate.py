from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
import uuid

script_dir = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(script_dir))
import csi_delivery_health_auto_remediate as auto

from csi_ingester.store import source_state as source_state_store
from csi_ingester.store.sqlite import connect, ensure_schema


def _insert_event(conn, *, source: str, delivered: int = 1) -> str:
    eid = f"evt_{uuid.uuid4().hex[:10]}"
    conn.execute(
        """
        INSERT INTO events (
            event_id, dedupe_key, source, event_type, occurred_at, received_at,
            subject_json, routing_json, metadata_json, delivered
        ) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'), ?, ?, ?, ?)
        """,
        (eid, f"dk_{eid}", source, "channel_new_upload", "{}", "{}", "{}", int(delivered)),
    )
    conn.commit()
    return eid


def _insert_attempt(conn, *, event_id: str, delivered: int, status_code: int) -> None:
    conn.execute(
        """
        INSERT INTO delivery_attempts (event_id, target, delivered, status_code, attempted_at)
        VALUES (?, 'ua_signals_ingest', ?, ?, datetime('now'))
        """,
        (event_id, int(delivered), int(status_code)),
    )
    conn.commit()


def test_guardrail_decision_cooldown_and_max_attempts():
    state: dict[str, object] = {}
    allowed, reason = auto._guardrail_decision(
        state=state,
        action_key="delivery_failures_detected::youtube_channel_rss",
        now_epoch=1000,
        cooldown_minutes=30,
        max_attempts_per_window=3,
        attempt_window_minutes=360,
    )
    assert allowed is True
    assert reason == "ok"

    auto._record_action_attempt(
        state=state,
        action_key="delivery_failures_detected::youtube_channel_rss",
        now_epoch=1000,
        success=True,
        detail="ok",
        attempt_window_minutes=360,
    )
    allowed, reason = auto._guardrail_decision(
        state=state,
        action_key="delivery_failures_detected::youtube_channel_rss",
        now_epoch=1100,  # inside cooldown
        cooldown_minutes=30,
        max_attempts_per_window=3,
        attempt_window_minutes=360,
    )
    assert allowed is False
    assert reason == "cooldown_active"

    # Exceed attempts in window.
    auto._record_action_attempt(
        state=state,
        action_key="delivery_failures_detected::youtube_channel_rss",
        now_epoch=4000,
        success=True,
        detail="ok",
        attempt_window_minutes=360,
    )
    auto._record_action_attempt(
        state=state,
        action_key="delivery_failures_detected::youtube_channel_rss",
        now_epoch=7000,
        success=True,
        detail="ok",
        attempt_window_minutes=360,
    )
    allowed, reason = auto._guardrail_decision(
        state=state,
        action_key="delivery_failures_detected::youtube_channel_rss",
        now_epoch=10000,
        cooldown_minutes=30,
        max_attempts_per_window=3,
        attempt_window_minutes=360,
    )
    assert allowed is False
    assert reason == "max_attempts_reached"


def test_cursor_reset_if_stale(tmp_path: Path):
    db = tmp_path / "csi.db"
    conn = connect(db)
    ensure_schema(conn)

    _insert_event(conn, source="youtube_channel_rss", delivered=1)
    _insert_event(conn, source="youtube_channel_rss", delivered=1)

    state_path = tmp_path / "rss_state.json"
    state_path.write_text('{"last_sent_id": 99}', encoding="utf-8")
    result = auto._cursor_reset_if_stale(
        conn=conn,
        state_path=state_path,
        source_name="youtube_channel_rss",
        dry_run=False,
    )
    conn.close()
    assert result["ok"] is True
    assert result["changed"] is True
    payload = auto._load_json(state_path)
    assert int(payload.get("last_sent_id") or 0) == 0


def test_collect_actions_and_state_roundtrip(tmp_path: Path):
    db = tmp_path / "csi.db"
    conn = connect(db)
    ensure_schema(conn)
    source_state_store.set_state(
        conn,
        "runtime_canary:auto_remediation",
        {"status": "failing"},
    )

    health = {
        "status": "failing",
        "remediation_steps": [
            {"code": "delivery_failures_detected", "source": "youtube_channel_rss"},
            {"code": "adapter_consecutive_failures", "source": "reddit_discovery"},
        ],
    }
    actions = auto._collect_actions(health)
    conn.close()
    keys = {f"{a['handler']}::{a['source']}" for a in actions}
    assert "replay_dlq::youtube_channel_rss" in keys
    assert "restart_ingester::reddit_discovery" in keys
    assert "cursor_reset_rss::youtube_channel_rss" in keys
    assert "cursor_reset_reddit::reddit_discovery" in keys


def test_run_once_dry_run_executes_actions_on_regression(tmp_path: Path):
    db = tmp_path / "csi.db"
    conn = connect(db)
    ensure_schema(conn)

    rss_event_id = _insert_event(conn, source="youtube_channel_rss", delivered=0)
    _insert_attempt(conn, event_id=rss_event_id, delivered=0, status_code=500)
    conn.execute(
        """
        INSERT INTO dead_letter (event_id, event_json, error_reason, retry_count)
        VALUES (?, ?, ?, ?)
        """,
        (f"dlq_{uuid.uuid4().hex[:8]}", '{"source":"youtube_channel_rss"}', "ua_status_500", 1),
    )
    conn.commit()
    conn.close()

    rss_state = tmp_path / "rss_state.json"
    rss_state.write_text('{"last_sent_id": 999}', encoding="utf-8")
    reddit_state = tmp_path / "reddit_state.json"
    reddit_state.write_text('{"last_sent_id": 999}', encoding="utf-8")

    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    csi_env_file = tmp_path / "csi.env"
    csi_env_file.write_text("", encoding="utf-8")

    args = argparse.Namespace(
        db_path=str(db),
        state_key="runtime_canary:auto_remediation:test",
        rss_state_path=str(rss_state),
        reddit_state_path=str(reddit_state),
        cooldown_minutes=1,
        max_attempts_per_window=5,
        attempt_window_minutes=360,
        max_actions_per_run=5,
        dlq_replay_limit=50,
        dlq_replay_attempts=3,
        force=False,
        dry_run=True,
        env_file=str(env_file),
        csi_env_file=str(csi_env_file),
    )
    code, summary = asyncio.run(auto._run_once(args))
    assert code == 0
    assert summary["health_status"] in {"degraded", "failing"}
    executed = summary.get("executed_actions")
    assert isinstance(executed, list) and executed
    handlers = {str(item.get("handler") or "") for item in executed if isinstance(item, dict)}
    assert "replay_dlq" in handlers
