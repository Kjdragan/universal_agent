"""Tests covering the preference-gate scoping fix and the detox migration.

These tests pin the 2026-05-24 behaviour change: `should_block_proactive_task`
counts only `signal_type='explicit_feedback'` rows, so a burst of implicit
park/block outcomes can never silently suppress a whole pipeline. The
detox script removes pre-existing implicit poison and rebuilds the
snapshot.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from universal_agent.scripts.preference_signal_detox import detox
from universal_agent.services.proactive_preferences import (
    ensure_schema,
    rebuild_preference_snapshot,
    should_block_proactive_task,
)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _insert_signal(
    conn: sqlite3.Connection,
    *,
    signal_key: str,
    weight: float,
    signal_type: str = "implicit_outcome",
    created_at: datetime | None = None,
    artifact_id: str = "art-x",
    text: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO proactive_preference_signals
          (artifact_id, signal_key, signal_type, weight, score, text, created_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, '{}')
        """,
        (
            artifact_id,
            signal_key,
            signal_type,
            weight,
            None,
            text,
            (created_at or datetime.now(timezone.utc)).isoformat(),
        ),
    )
    conn.commit()


# ── Gate scoping ──────────────────────────────────────────────────────────


def test_gate_ignores_implicit_park_burst(tmp_path):
    """A 224-event implicit park burst must not block new briefs."""
    db = tmp_path / "activity_state.db"
    with _connect(db) as conn:
        # Replay the April 2026 poison: 224 implicit park signals against
        # the convergence pipeline's matching keys.
        for i in range(224):
            for key in (
                "type:convergence_detection",
                "topic:convergence",
                "topic:atlas",
                "topic:research",
            ):
                _insert_signal(
                    conn,
                    signal_key=key,
                    weight=-0.1,
                    signal_type="implicit_outcome",
                    artifact_id=f"conv-{i}",
                    text="Outcome: park",
                )
        rebuild_preference_snapshot(conn)

        blocked, reason = should_block_proactive_task(
            conn,
            task_type="convergence_detection",
            topic_tags=["convergence", "atlas", "research"],
        )
    assert blocked is False, f"implicit-only signals must not block; got reason={reason!r}"


def test_gate_blocks_on_strong_explicit_negative(tmp_path):
    """Explicit feedback at -0.8 across every matching key still blocks."""
    db = tmp_path / "activity_state.db"
    with _connect(db) as conn:
        for key in (
            "type:tutorial_build",
            "topic:tutorial",
            "topic:codie",
        ):
            for i in range(3):
                _insert_signal(
                    conn,
                    signal_key=key,
                    weight=-0.8,
                    signal_type="explicit_feedback",
                    artifact_id=f"tut-{key}-{i}",
                    text="thumbs down",
                )

        blocked, reason = should_block_proactive_task(
            conn,
            task_type="tutorial_build",
            topic_tags=["tutorial", "codie"],
        )
    assert blocked is True
    assert "explicit-feedback" in reason


def test_gate_allows_when_any_explicit_key_is_neutral(tmp_path):
    """One neutral/positive explicit key short-circuits the block."""
    db = tmp_path / "activity_state.db"
    with _connect(db) as conn:
        _insert_signal(conn, signal_key="type:insight_detection", weight=-0.9, signal_type="explicit_feedback")
        _insert_signal(conn, signal_key="topic:atlas", weight=-0.9, signal_type="explicit_feedback")
        # topic:insight has one positive explicit signal — that's enough.
        _insert_signal(conn, signal_key="topic:insight", weight=0.5, signal_type="explicit_feedback")

        blocked, _ = should_block_proactive_task(
            conn,
            task_type="insight_detection",
            topic_tags=["insight", "atlas"],
        )
    assert blocked is False


def test_gate_allows_when_only_implicit_signals_exist(tmp_path):
    """Mixing implicit poison with zero explicit signals → no block."""
    db = tmp_path / "activity_state.db"
    with _connect(db) as conn:
        for _ in range(50):
            _insert_signal(conn, signal_key="topic:atlas", weight=-0.1, signal_type="implicit_outcome")
        blocked, _ = should_block_proactive_task(
            conn, task_type="insight_detection", topic_tags=["atlas"]
        )
    assert blocked is False


def test_gate_short_circuits_on_no_keys(tmp_path):
    db = tmp_path / "activity_state.db"
    with _connect(db) as conn:
        blocked, reason = should_block_proactive_task(conn, task_type="", topic_tags=[])
    assert blocked is False
    assert reason == ""


def test_gate_decays_old_explicit_signals(tmp_path):
    """A very old explicit -0.8 signal decays below the block threshold."""
    db = tmp_path / "activity_state.db"
    very_old = datetime.now(timezone.utc) - timedelta(days=120)
    with _connect(db) as conn:
        _insert_signal(
            conn,
            signal_key="type:insight_detection",
            weight=-0.8,
            signal_type="explicit_feedback",
            created_at=very_old,
        )
        blocked, _ = should_block_proactive_task(
            conn, task_type="insight_detection", topic_tags=[]
        )
    # 120 days at 14-day half-life is ~1/365 of original → well above -0.5.
    assert blocked is False


# ── Detox migration ──────────────────────────────────────────────────────


def test_detox_dry_run_changes_nothing(tmp_path):
    db = tmp_path / "activity_state.db"
    with _connect(db) as conn:
        for i in range(5):
            _insert_signal(conn, signal_key="topic:atlas", weight=-0.1, signal_type="implicit_outcome", artifact_id=f"a-{i}")
        _insert_signal(conn, signal_key="topic:atlas", weight=0.9, signal_type="explicit_feedback", artifact_id="exp-1")

        report = detox(conn, commit=False)
        rows_after = conn.execute("SELECT COUNT(*) FROM proactive_preference_signals").fetchone()[0]

    assert report["target_rows"] == 5
    assert report["deleted"] == 0
    assert report["dry_run"] == 1
    assert rows_after == 6  # unchanged


def test_detox_commit_removes_only_target_type(tmp_path):
    db = tmp_path / "activity_state.db"
    with _connect(db) as conn:
        for i in range(10):
            _insert_signal(conn, signal_key="topic:atlas", weight=-0.1, signal_type="implicit_outcome", artifact_id=f"a-{i}")
        _insert_signal(conn, signal_key="topic:atlas", weight=0.9, signal_type="explicit_feedback", artifact_id="exp-1")
        _insert_signal(conn, signal_key="topic:atlas", weight=-0.8, signal_type="explicit_feedback", artifact_id="exp-2")

        report = detox(conn, commit=True)
        remaining_types = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT signal_type FROM proactive_preference_signals"
            ).fetchall()
        ]

    assert report["deleted"] == 10
    assert report["total_after"] == 2
    assert remaining_types == ["explicit_feedback"]


def test_detox_is_idempotent(tmp_path):
    db = tmp_path / "activity_state.db"
    with _connect(db) as conn:
        for i in range(3):
            _insert_signal(conn, signal_key="topic:atlas", weight=-0.1, artifact_id=f"a-{i}")

        first = detox(conn, commit=True)
        second = detox(conn, commit=True)

    assert first["deleted"] == 3
    assert second["deleted"] == 0
    assert second["target_rows"] == 0
