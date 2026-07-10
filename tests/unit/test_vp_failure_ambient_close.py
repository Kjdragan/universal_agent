"""Ambient-close for stale ``vp_mission_failure`` Task Hub items.

Covers the shared core ``close_ambient_vp_failures`` (used by BOTH the TTL sweep
cron and the ``bulk_close_ambient`` Task Hub verb):

* closes qualifying items (old + failure_count<=1) and SKIPS guarded items
  (recent <48h OR failure_count>=2 OR below the TTL window), returning an
  accurate summary;
* the ambient close is distinguishable from a rescue-complete in the audit
  trail (``metadata.ambient_close`` marker + ``last_disposition=ambient_closed``
  + an evaluation row tagged ``source=vp_mission_failure_ambient_close``);
* the ``bulk_close_ambient`` verb routes through the sanctioned per-item path
  (one evaluation row + one completion_token per closed item) — NOT a
  set-level bulk UPDATE;
* malformed-timestamp items are skipped, never closed;
* other source_kinds are untouched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

from universal_agent import task_hub


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _set_age(conn: sqlite3.Connection, task_id: str, age_hours: float) -> None:
    """Pin created_at so the item looks `age_hours` old."""
    created = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    conn.execute(
        "UPDATE task_hub_items SET created_at=? WHERE task_id=?",
        (created.strftime("%Y-%m-%dT%H:%M:%S+00:00"), task_id),
    )


def _seed_failure(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    failure_count: int = 1,
    age_hours: float = 24 * 30,
    status: str = task_hub.TASK_STATUS_OPEN,
    source_kind: str = task_hub.VP_FAILURE_AMBIENT_SOURCE_KIND,
) -> dict:
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": source_kind,
            "title": f"VP failure ({task_id})",
            "status": status,
            "agent_ready": True,
            "trigger_type": "immediate",
            "metadata": {"failure_count": failure_count, "mission_id": task_id},
        },
    )
    _set_age(conn, task_id, age_hours)
    return task_hub.get_item(conn, task_id)


def _eval_rows(conn: sqlite3.Connection, task_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM task_hub_evaluations WHERE task_id=? ORDER BY evaluated_at",
        (task_id,),
    ).fetchall()


# ─────────────────────────────────────────────────────────────────────────────
# Guard: qualifying vs. guarded
# ─────────────────────────────────────────────────────────────────────────────


def test_closes_qualifying_skips_guarded_returns_accurate_summary():
    conn = _conn()
    # qualifying: old (30d) + single failure
    _seed_failure(conn, task_id="vp_failure:ok", failure_count=1, age_hours=24 * 30)
    # guarded — recurring: old but failure_count=2
    _seed_failure(conn, task_id="vp_failure:recurring", failure_count=2, age_hours=24 * 30)
    # guarded — recent: 1 day old (within 48h guard)
    _seed_failure(conn, task_id="vp_failure:recent", failure_count=1, age_hours=24)
    # guarded — below TTL window: 3 days old (< 7d TTL)
    _seed_failure(conn, task_id="vp_failure:below_ttl", failure_count=1, age_hours=24 * 3)

    summary = task_hub.close_ambient_vp_failures(
        conn,
        older_than_days=7,
        max_failure_count=1,
        within_hours_guard=48,
        via="ttl",
    )

    assert summary["closed"] == 1
    assert summary["closed_ids"] == ["vp_failure:ok"]
    assert summary["skipped_reasons"]["recurring"] == 1
    assert summary["skipped_reasons"]["recent"] == 1
    assert summary["skipped_reasons"]["below_ttl_window"] == 1
    assert summary["guard"] == {"within_hours": 48, "older_than_days": 7, "max_failure_count": 1}

    assert task_hub.get_item(conn, "vp_failure:ok")["status"] == task_hub.TASK_STATUS_COMPLETED
    assert task_hub.get_item(conn, "vp_failure:recurring")["status"] == task_hub.TASK_STATUS_OPEN
    assert task_hub.get_item(conn, "vp_failure:recent")["status"] == task_hub.TASK_STATUS_OPEN
    assert task_hub.get_item(conn, "vp_failure:below_ttl")["status"] == task_hub.TASK_STATUS_OPEN


def test_recent_failure_preserved_even_with_zero_ttl():
    """The 48h guard is independent of the TTL window — a recent failure is
    never auto-closed even if older_than_days=0."""
    conn = _conn()
    _seed_failure(conn, task_id="vp_failure:fresh", failure_count=1, age_hours=6)
    summary = task_hub.close_ambient_vp_failures(
        conn, older_than_days=0, max_failure_count=1, within_hours_guard=48
    )
    assert summary["closed"] == 0
    assert summary["skipped_reasons"]["recent"] == 1
    assert task_hub.get_item(conn, "vp_failure:fresh")["status"] == task_hub.TASK_STATUS_OPEN


def test_recurring_failure_preserved_even_when_old():
    """failure_count>=2 is never auto-closed, regardless of age."""
    conn = _conn()
    _seed_failure(conn, task_id="vp_failure:bad", failure_count=3, age_hours=24 * 60)
    summary = task_hub.close_ambient_vp_failures(
        conn, older_than_days=7, max_failure_count=1, within_hours_guard=48
    )
    assert summary["closed"] == 0
    assert summary["skipped_reasons"]["recurring"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Audit-trail distinguishability
# ─────────────────────────────────────────────────────────────────────────────


def test_ambient_close_distinguishable_from_rescue_complete():
    conn = _conn()
    _seed_failure(conn, task_id="vp_failure:stale", failure_count=1, age_hours=24 * 30)
    # a normal, non-failure task completed the canonical way
    task_hub.upsert_item(
        conn,
        {
            "task_id": "task:normal",
            "source_kind": "manual",
            "title": "Normal task",
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": True,
        },
    )
    _set_age(conn, "task:normal", 1)

    task_hub.close_ambient_vp_failures(
        conn, older_than_days=7, max_failure_count=1, within_hours_guard=48, via="ttl"
    )
    task_hub.perform_task_action(conn, task_id="task:normal", action="complete", reason="done")

    stale = task_hub.get_item(conn, "vp_failure:stale")
    normal = task_hub.get_item(conn, "task:normal")

    # ambient-closed carries the honest marker + disposition
    assert stale["metadata"].get("ambient_close") == {
        "reason": task_hub.AMBIENT_CLOSE_REASON,
        "via": "ttl",
        "closed_at": stale["metadata"]["ambient_close"]["closed_at"],
        "age_days": stale["metadata"]["ambient_close"]["age_days"],
        "failure_count": 1,
    }
    assert stale["metadata"]["dispatch"]["last_disposition"] == task_hub.AMBIENT_CLOSE_LAST_DISPOSITION

    # rescue-complete does NOT carry the ambient marker
    assert "ambient_close" not in (normal["metadata"] or {})
    assert normal["metadata"]["dispatch"]["last_disposition"] == "completed"

    # the evaluation audit row distinguishes them by source
    stale_evals = _eval_rows(conn, "vp_failure:stale")
    normal_evals = _eval_rows(conn, "task:normal")
    assert any(
        r["decision"] == "complete" and r["reason"] == task_hub.AMBIENT_CLOSE_REASON
        for r in stale_evals
    ), "ambient-closed item must have an evaluation row tagged ambient_stale_no_action"
    assert not any(
        r["reason"] == task_hub.AMBIENT_CLOSE_REASON for r in normal_evals
    ), "rescue-complete must not carry the ambient marker"


# ─────────────────────────────────────────────────────────────────────────────
# Sanctioned per-item path (no set-level bulk UPDATE)
# ─────────────────────────────────────────────────────────────────────────────


def test_bulk_verb_writes_per_item_audit_trail():
    """The bulk verb closes each item through the sanctioned path: one
    evaluation row + one distinct completion_token per closed item. Asserts
    there is NO set-level bulk UPDATE (each closed item is processed
    individually, and guarded items get no audit row for the close)."""
    conn = _conn()
    closed_ids = []
    for i in range(3):
        tid = f"vp_failure:close{i}"
        _seed_failure(conn, task_id=tid, failure_count=1, age_hours=24 * 20)
        closed_ids.append(tid)
    # a guarded item that must NOT be closed and must NOT get an ambient eval row
    _seed_failure(conn, task_id="vp_failure:guarded", failure_count=2, age_hours=24 * 20)

    summary = task_hub.perform_task_action(
        conn,
        task_id="",
        action="bulk_close_ambient",
        filt={"older_than_days": 7, "max_failure_count": 1, "within_hours_guard": 48},
        agent_id="simone",
    )
    assert summary["via"] == "bulk_verb"
    assert summary["closed"] == 3
    assert set(summary["closed_ids"]) == set(closed_ids)

    tokens = set()
    for tid in closed_ids:
        item = task_hub.get_item(conn, tid)
        assert item["status"] == task_hub.TASK_STATUS_COMPLETED
        token = conn.execute(
            "SELECT completion_token FROM task_hub_items WHERE task_id=?", (tid,)
        ).fetchone()["completion_token"]
        assert token and token.startswith("ambient_"), f"{tid} missing ambient completion_token"
        tokens.add(token)
        # one ambient evaluation row per closed item (sanctioned per-item audit)
        amb_evals = [
            r for r in _eval_rows(conn, tid)
            if r["reason"] == task_hub.AMBIENT_CLOSE_REASON
        ]
        assert len(amb_evals) == 1, f"{tid} should have exactly one ambient eval row"
    # distinct tokens prove per-item writes, not one bulk UPDATE
    assert len(tokens) == 3

    # guarded item untouched + no ambient eval row
    guarded = task_hub.get_item(conn, "vp_failure:guarded")
    assert guarded["status"] == task_hub.TASK_STATUS_OPEN
    assert not any(
        r["reason"] == task_hub.AMBIENT_CLOSE_REASON
        for r in _eval_rows(conn, "vp_failure:guarded")
    )


def test_bulk_verb_summary_reports_skipped_reasons():
    conn = _conn()
    _seed_failure(conn, task_id="vp_failure:ok", failure_count=1, age_hours=24 * 30)
    _seed_failure(conn, task_id="vp_failure:rec", failure_count=2, age_hours=24 * 30)
    summary = task_hub.perform_task_action(
        conn, task_id="", action="bulk_close_ambient", filt={}, agent_id="simone"
    )
    # bare filter -> conservative defaults (7d/48h/fc<=1)
    assert summary["closed"] == 1
    assert summary["skipped_reasons"]["recurring"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────────────────


def test_malformed_timestamp_skipped_never_closed():
    conn = _conn()
    task_hub.upsert_item(
        conn,
        {
            "task_id": "vp_failure:badts",
            "source_kind": task_hub.VP_FAILURE_AMBIENT_SOURCE_KIND,
            "title": "bad ts",
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": True,
            "metadata": {"failure_count": 1},
        },
    )
    # unparseable created_at — mirrors the one malformed row in the live pile
    conn.execute(
        "UPDATE task_hub_items SET created_at='' WHERE task_id='vp_failure:badts'"
    )
    summary = task_hub.close_ambient_vp_failures(
        conn, older_than_days=7, max_failure_count=1, within_hours_guard=48
    )
    assert summary["closed"] == 0
    assert summary["skipped_reasons"]["malformed_timestamp"] == 1
    assert task_hub.get_item(conn, "vp_failure:badts")["status"] == task_hub.TASK_STATUS_OPEN


def test_other_source_kinds_untouched():
    conn = _conn()
    _seed_failure(conn, task_id="vp_failure:stale", failure_count=1, age_hours=24 * 30)
    # a stale reflection item — must NOT be closed by the vp_failure sweep
    task_hub.upsert_item(
        conn,
        {
            "task_id": "task:reflection",
            "source_kind": "reflection",
            "title": "old reflection",
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": True,
        },
    )
    _set_age(conn, "task:reflection", 24 * 30)

    task_hub.close_ambient_vp_failures(
        conn, older_than_days=7, max_failure_count=1, within_hours_guard=48
    )
    assert task_hub.get_item(conn, "vp_failure:stale")["status"] == task_hub.TASK_STATUS_COMPLETED
    assert task_hub.get_item(conn, "task:reflection")["status"] == task_hub.TASK_STATUS_OPEN


def test_dry_run_does_not_write():
    conn = _conn()
    _seed_failure(conn, task_id="vp_failure:stale", failure_count=1, age_hours=24 * 30)
    summary = task_hub.close_ambient_vp_failures(
        conn, older_than_days=7, max_failure_count=1, within_hours_guard=48, dry_run=True
    )
    assert summary["closed"] == 1
    assert summary["dry_run"] is True
    assert task_hub.get_item(conn, "vp_failure:stale")["status"] == task_hub.TASK_STATUS_OPEN


def test_already_ambient_closed_not_reclosed():
    conn = _conn()
    _seed_failure(conn, task_id="vp_failure:stale", failure_count=1, age_hours=24 * 30)
    task_hub.close_ambient_vp_failures(
        conn, older_than_days=7, max_failure_count=1, within_hours_guard=48
    )
    # second sweep is a no-op for the already-closed row (it's terminal now,
    # so excluded by the status filter; idempotent).
    summary = task_hub.close_ambient_vp_failures(
        conn, older_than_days=7, max_failure_count=1, within_hours_guard=48
    )
    assert summary["closed"] == 0
