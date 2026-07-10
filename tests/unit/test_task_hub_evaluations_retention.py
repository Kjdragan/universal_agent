from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

from universal_agent import task_hub


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _insert_eval(conn: sqlite3.Connection, *, eval_id: str, task_id: str, at: str, decision: str = "reject") -> None:
    conn.execute(
        """
        INSERT INTO task_hub_evaluations (
            id, task_id, evaluated_at, agent_id, decision, reason, score, score_confidence, judge_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (eval_id, task_id, at, "simone", decision, "r", 0.5, 0.9, "{}"),
    )


def _iso(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def test_cap_deletes_over_limit_and_keeps_newest_n() -> None:
    conn = _conn()
    try:
        # 600 evals with a deterministic base time: e0 oldest, e599 newest.
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for i in range(600):
            conn.execute(
                """
                INSERT INTO task_hub_evaluations (
                    id, task_id, evaluated_at, agent_id, decision, reason, score, score_confidence, judge_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (f"e{i}", "task:A", (base + timedelta(minutes=i)).isoformat(), "simone", "reject", "r", 0.5, 0.9, "{}"),
            )
        deleted = task_hub.prune_task_hub_evaluations_to_cap(conn, max_per_task=500)
        assert deleted == 100
        kept = {r["id"] for r in conn.execute("SELECT id FROM task_hub_evaluations WHERE task_id='task:A'")}
        assert len(kept) == 500
        # Newest 500 (e100..e599) kept; oldest 100 (e0..e99) deleted.
        assert "e599" in kept and "e100" in kept
        assert "e99" not in kept and "e0" not in kept
    finally:
        conn.close()


def test_cap_under_limit_is_noop() -> None:
    conn = _conn()
    try:
        for i in range(50):
            _insert_eval(conn, eval_id=f"e{i}", task_id="task:A", at=_iso(50 - i))
        deleted = task_hub.prune_task_hub_evaluations_to_cap(conn, max_per_task=500)
        assert deleted == 0
        assert conn.execute("SELECT COUNT(*) AS c FROM task_hub_evaluations").fetchone()["c"] == 50
    finally:
        conn.close()


def test_cap_isolated_per_task() -> None:
    conn = _conn()
    try:
        # task:A over cap, task:B under cap, task:C empty.
        for i in range(10):
            _insert_eval(conn, eval_id=f"a{i}", task_id="task:A", at=_iso(10 - i))
        for i in range(3):
            _insert_eval(conn, eval_id=f"b{i}", task_id="task:B", at=_iso(3 - i))
        deleted = task_hub.prune_task_hub_evaluations_to_cap(conn, max_per_task=5)
        assert deleted == 5  # only task:A over by 5
        assert conn.execute("SELECT COUNT(*) AS c FROM task_hub_evaluations WHERE task_id='task:A'").fetchone()["c"] == 5
        assert conn.execute("SELECT COUNT(*) AS c FROM task_hub_evaluations WHERE task_id='task:B'").fetchone()["c"] == 3
    finally:
        conn.close()


def test_cap_preserves_stale_policy_nonzero() -> None:
    """_apply_stale_policy treats COUNT(*)==0 as awaiting_evaluation; after capping
    to N>=1 the task must still show >=1 evaluation so it is not falsely flagged."""
    conn = _conn()
    try:
        for i in range(20):
            _insert_eval(conn, eval_id=f"e{i}", task_id="task:A", at=_iso(20 - i))
        task_hub.prune_task_hub_evaluations_to_cap(conn, max_per_task=5)
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM task_hub_evaluations WHERE task_id = ?", ("task:A",)
        ).fetchone()["c"]
        assert count >= 1
    finally:
        conn.close()


def test_cap_is_decision_agnostic() -> None:
    """Cap keeps newest N regardless of decision -> decision-ratio analytics stay
    representative. Verify a mixed task retains both decisions in the kept window."""
    conn = _conn()
    try:
        # Alternating seize/reject, 20 rows; cap at 10 keeps newest 10 (5 each).
        for i in range(20):
            decision = "seize" if i % 2 == 0 else "reject"
            _insert_eval(conn, eval_id=f"e{i}", task_id="task:A", at=_iso(20 - i), decision=decision)
        task_hub.prune_task_hub_evaluations_to_cap(conn, max_per_task=10)
        rows = conn.execute(
            "SELECT decision FROM task_hub_evaluations WHERE task_id='task:A'"
        ).fetchall()
        decisions = [r["decision"] for r in rows]
        assert len(decisions) == 10
        assert "seize" in decisions and "reject" in decisions
    finally:
        conn.close()


def test_activity_prune_old_enforces_cap_end_to_end() -> None:
    """Integration: _activity_prune_old wires the cap and prunes a live schema."""
    import universal_agent.gateway_server as gs

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        gs._ensure_activity_schema(conn)
        task_hub.ensure_schema(conn)

        saved_cap = gs._activity_evaluations_max_per_task
        saved_ts = gs._last_eval_cap_prune_ts
        gs._activity_evaluations_max_per_task = 3
        gs._last_eval_cap_prune_ts = 0.0  # force the time-gated cap to run now
        try:
            for i in range(10):
                _insert_eval(conn, eval_id=f"a{i}", task_id="task:A", at=_iso(10 - i))
            for i in range(2):
                _insert_eval(conn, eval_id=f"b{i}", task_id="task:B", at=_iso(2 - i))
            gs._activity_prune_old(conn)
            a = conn.execute("SELECT COUNT(*) AS c FROM task_hub_evaluations WHERE task_id='task:A'").fetchone()["c"]
            b = conn.execute("SELECT COUNT(*) AS c FROM task_hub_evaluations WHERE task_id='task:B'").fetchone()["c"]
            assert a == 3, a
            assert b == 2, b
        finally:
            gs._activity_evaluations_max_per_task = saved_cap
            gs._last_eval_cap_prune_ts = saved_ts
    finally:
        conn.close()
