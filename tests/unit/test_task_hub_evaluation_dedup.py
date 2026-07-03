"""Unit tests for ``task_hub._record_evaluation`` redundant-defer dedup.

Background: ``rebuild_dispatch_queue`` recorded a ``defer``/``dispatch_rebuild``
row for every dispatchable task on EVERY rebuild, so a task that stayed open
across ~10k rebuilds accumulated ~10k identical rows. That runaway writer
filled ``task_hub_evaluations`` with 4.2M+ identical rows (~2.8 GB) in 8 days.

The fix in ``_record_evaluation`` collapses redundant defers: a ``defer`` that
repeats the most recent evaluation for the same task (same decision AND same
reason) is skipped; every state change and every non-defer decision still
writes. These tests pin that contract.
"""

from __future__ import annotations

import sqlite3

from universal_agent import task_hub

# ── helpers ───────────────────────────────────────────────────────────────


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def _eval_count(conn: sqlite3.Connection, task_id: str) -> int:
    return _count(
        conn, "SELECT COUNT(*) FROM task_hub_evaluations WHERE task_id=?", (task_id,)
    )


def _seed_open_task(conn: sqlite3.Connection, task_id: str) -> None:
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "internal",
            "title": f"task {task_id}",
            "description": "do the thing",
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": True,
        },
    )


# ── (a) redundant consecutive defer is collapsed ──────────────────────────


def test_redundant_defer_same_reason_does_not_persist() -> None:
    conn = _conn()
    task_id = "task:defer:1"

    task_hub._record_evaluation(
        conn,
        task_id=task_id,
        agent_id="scorer",
        decision="defer",
        reason="dispatch_rebuild",
    )
    task_hub._record_evaluation(
        conn,
        task_id=task_id,
        agent_id="scorer",
        decision="defer",
        reason="dispatch_rebuild",
    )
    task_hub._record_evaluation(
        conn,
        task_id=task_id,
        agent_id="scorer",
        decision="defer",
        reason="dispatch_rebuild",
    )

    assert _eval_count(conn, task_id) == 1


def test_first_defer_always_persists() -> None:
    conn = _conn()
    task_id = "task:defer:first"

    task_hub._record_evaluation(
        conn,
        task_id=task_id,
        agent_id="scorer",
        decision="defer",
        reason="dispatch_rebuild",
    )

    assert _eval_count(conn, task_id) == 1


# ── (b) state changes persist ─────────────────────────────────────────────


def test_defer_with_different_reason_persists() -> None:
    conn = _conn()
    task_id = "task:defer:reason-change"

    task_hub._record_evaluation(
        conn,
        task_id=task_id,
        agent_id="scorer",
        decision="defer",
        reason="dispatch_rebuild",
    )
    task_hub._record_evaluation(
        conn, task_id=task_id, agent_id="scorer", decision="defer", reason="low_score"
    )
    task_hub._record_evaluation(
        conn, task_id=task_id, agent_id="scorer", decision="defer", reason="low_score"
    )  # redundant now

    assert _eval_count(conn, task_id) == 2


def test_defer_after_non_defer_is_a_state_change() -> None:
    """A defer following a non-defer (e.g. seizure ending) must persist."""
    conn = _conn()
    task_id = "task:defer:after-seize"

    task_hub._record_evaluation(
        conn, task_id=task_id, agent_id="atlas", decision="seize", reason="claimed"
    )
    task_hub._record_evaluation(
        conn,
        task_id=task_id,
        agent_id="scorer",
        decision="defer",
        reason="dispatch_rebuild",
    )

    assert _eval_count(conn, task_id) == 2


def test_non_defer_after_defer_persists() -> None:
    conn = _conn()
    task_id = "task:defer:then-seize"

    task_hub._record_evaluation(
        conn,
        task_id=task_id,
        agent_id="scorer",
        decision="defer",
        reason="dispatch_rebuild",
    )
    task_hub._record_evaluation(
        conn, task_id=task_id, agent_id="atlas", decision="seize", reason="claimed"
    )

    assert _eval_count(conn, task_id) == 2


# ── (c) non-defer decisions always persist, even when repeated ────────────


def test_repeated_non_defer_always_persists() -> None:
    conn = _conn()
    task_id = "task:seize:repeat"

    for _ in range(4):
        task_hub._record_evaluation(
            conn, task_id=task_id, agent_id="atlas", decision="seize", reason="claimed"
        )

    assert _eval_count(conn, task_id) == 4


def test_repeated_reject_always_persists() -> None:
    conn = _conn()
    task_id = "task:reject:repeat"

    for _ in range(3):
        task_hub._record_evaluation(
            conn,
            task_id=task_id,
            agent_id="scorer",
            decision="reject",
            reason="duplicate",
        )

    assert _eval_count(conn, task_id) == 3


def test_decision_match_is_case_insensitive() -> None:
    """A 'DEFER' decision still dedups against a prior 'defer'."""
    conn = _conn()
    task_id = "task:defer:case"

    task_hub._record_evaluation(
        conn,
        task_id=task_id,
        agent_id="scorer",
        decision="defer",
        reason="dispatch_rebuild",
    )
    task_hub._record_evaluation(
        conn,
        task_id=task_id,
        agent_id="scorer",
        decision="DEFER",
        reason="dispatch_rebuild",
    )

    assert _eval_count(conn, task_id) == 1


# ── integration: the actual runaway scenario stays bounded ────────────────


def test_rebuild_dispatch_queue_does_not_pile_up_defer_rows() -> None:
    """The original bug: 50 rebuilds wrote 50 identical defer rows per task.

    After the fix, an open task that stays put across many rebuilds keeps a
    single most-recent defer row (the rebuild re-defers it identically each
    time, which is exactly what the dedup collapses).
    """
    conn = _conn()
    task_id = "task:rebuild:open"
    _seed_open_task(conn, task_id)

    for _ in range(50):
        task_hub.rebuild_dispatch_queue(conn)

    # The dispatch-rebuild defer is recorded identically on every rebuild, so
    # dedup keeps it to exactly one row.
    assert _eval_count(conn, task_id) == 1


def test_rebuild_then_state_change_then_rebuild_writes_two_rows() -> None:
    """A genuine evaluation state change across rebuilds still persists."""
    conn = _conn()
    task_id = "task:rebuild:change"
    _seed_open_task(conn, task_id)

    task_hub.rebuild_dispatch_queue(conn)  # writes defer/dispatch_rebuild
    # A non-defer evaluation lands between rebuilds (e.g. a seize attempt).
    task_hub._record_evaluation(
        conn, task_id=task_id, agent_id="atlas", decision="seize", reason="claimed"
    )
    task_hub.rebuild_dispatch_queue(
        conn
    )  # defer after seize = state change -> persists

    rows = conn.execute(
        "SELECT decision, reason FROM task_hub_evaluations WHERE task_id=? ORDER BY evaluated_at ASC",
        (task_id,),
    ).fetchall()
    decisions = [(r["decision"], r["reason"]) for r in rows]
    assert ("defer", "dispatch_rebuild") in decisions
    assert ("seize", "claimed") in decisions
    assert len(decisions) == 3  # defer, seize, defer


# ── readers that depend on the table are not broken ───────────────────────


def test_apply_stale_policy_still_sees_at_least_one_eval() -> None:
    """``_apply_stale_policy`` keys off eval_count<=0; dedup keeps >=1 row."""
    conn = _conn()
    task_id = "task:stale:1"
    _seed_open_task(conn, task_id)

    task_hub.rebuild_dispatch_queue(conn)  # first defer persists
    task_hub.rebuild_dispatch_queue(conn)  # redundant, collapsed

    # The stale check must NOT see zero evaluations.
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM task_hub_evaluations WHERE task_id=?", (task_id,)
    ).fetchone()
    assert int(row["c"]) >= 1
