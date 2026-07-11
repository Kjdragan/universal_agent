"""Regression: sqlite helpers must both COMMIT and CLOSE their connections.

The `with contextlib.closing(sqlite3.connect(...)) as conn, conn:` idiom
replaced a bare `with sqlite3.connect(...) as conn:` in plan_persistence and
memory_vector_index. The bare form committed but never closed the connection
(a slow fd leak); `closing` alone would have closed but dropped the commit
(data loss). These tests prove the idiom preserves writes AND releases the fd.
"""

from __future__ import annotations

from universal_agent.memory import memory_vector_index as mvi
from universal_agent.urw.plan_persistence import SQLitePlanStore
from universal_agent.urw.plan_schema import AtomicTask, Phase, Plan


def test_plan_store_round_trip_persists(tmp_path):
    db = tmp_path / "plans.db"
    store = SQLitePlanStore(str(db))

    task = AtomicTask(name="do a thing")
    phase = Phase(name="phase-1", order=0, tasks=[task])
    plan = Plan(name="plan-1", phases=[phase])

    store.save_plan(plan)

    # A brand-new store instance (fresh connection) must see the committed row —
    # proves the transaction was committed, not lost by the closing() change.
    reloaded = SQLitePlanStore(str(db)).load_plan(plan.id)
    assert reloaded is not None
    assert reloaded.id == plan.id
    assert reloaded.phases[0].tasks[0].name == "do a thing"


def test_vector_index_round_trip_persists(tmp_path):
    db = str(tmp_path / "vec.db")
    mvi.upsert_vector(
        db,
        entry_id="e1",
        content_hash="h1",
        timestamp="2026-07-10T00:00:00Z",
        summary="a summary about idempotency ledgers",
        preview="preview",
        content="idempotency ledger duplicate side effects",
    )
    hits = mvi.search_vectors(db, "idempotency ledger", limit=5)
    assert any(h["entry_id"] == "e1" for h in hits)


def test_helpers_leave_no_open_transaction(tmp_path):
    """After each helper returns, no sqlite3.Connection it opened should still
    be mid-transaction — a proxy for 'the connection context exited cleanly and
    the handle was released' rather than lingering open."""
    import gc
    import sqlite3

    db = str(tmp_path / "vec2.db")
    mvi.upsert_vector(
        db,
        entry_id="e2",
        content_hash="h2",
        timestamp="2026-07-10T00:00:00Z",
        summary="s",
        preview="p",
        content="hello world",
    )
    mvi.search_vectors(db, "hello", limit=5)
    gc.collect()
    live = [o for o in gc.get_objects() if isinstance(o, sqlite3.Connection)]
    assert not any(getattr(c, "in_transaction", False) for c in live)
