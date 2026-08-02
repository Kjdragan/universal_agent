"""Guards for the T15 signal-surfacing fix in proactive_artifacts.py.

T15: the daily digest's ranked pool (``intelligence_reporter._rank_digest_artifacts``)
drew from ``list_artifacts(limit=250)`` ordered ``priority DESC, updated_at DESC``
with no delivery filter. 293 lifetime artifacts at priority>=4 were never
archived, so they permanently saturated the 250-row window — every
``proactive_signal`` artifact (priority<=3, by far the highest-volume lane)
was structurally unreachable: 3,436 lifetime rows, zero ever surfaced.

These tests lock the two schema/query-layer fixes that make the pool
self-correcting:

  (a) ``list_artifacts(order_by="updated_at")`` ranks by recency first, so a
      fresh low-priority row beats a stale high-priority one within a
      bounded LIMIT window — instead of a priority-first order letting old
      high-priority rows permanently win the window.
  (b) ``archive_stale_artifacts`` makes a staleness verdict durable (via the
      existing ``update_artifact_state`` archive verb) so stale rows leave
      the pool for good instead of being filtered out transiently on every
      single query.

The default ``list_artifacts(order_by="priority", ...)`` (and the previously
uncovered ``exclude_status``/``delivery_state`` no-op-by-default behavior)
is left exactly as prior callers depend on — see
test_proactive_artifacts_dict_row.py for that pre-existing coverage.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

from universal_agent.services import proactive_artifacts


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    proactive_artifacts.ensure_schema(conn)
    return conn


def _set_updated_at(conn: sqlite3.Connection, artifact_id: str, when: datetime) -> None:
    """Force a deterministic updated_at, independent of wall-clock timing
    between two upsert_artifact() calls in the same test."""
    conn.execute(
        "UPDATE proactive_artifacts SET updated_at = ? WHERE artifact_id = ?",
        (when.isoformat(), artifact_id),
    )
    conn.commit()


def _make(
    conn: sqlite3.Connection,
    *,
    title: str,
    priority: int,
    delivery_state: str = proactive_artifacts.DELIVERY_NOT_SURFACED,
    status: str = proactive_artifacts.ARTIFACT_STATUS_CANDIDATE,
    artifact_type: str = "signal_card",
    source_kind: str = "proactive_signal",
) -> dict:
    return proactive_artifacts.upsert_artifact(
        conn,
        artifact_type=artifact_type,
        source_kind=source_kind,
        source_ref=title,
        title=title,
        status=status,
        delivery_state=delivery_state,
        priority=priority,
    )


class TestPoolDrawsOnlyNotSurfaced:
    def test_delivery_state_filter_excludes_emailed_and_reviewed(self):
        conn = _conn()
        _make(conn, title="never surfaced", priority=2)
        emailed = _make(conn, title="already emailed", priority=5)
        proactive_artifacts.update_artifact_state(
            conn,
            artifact_id=emailed["artifact_id"],
            status=proactive_artifacts.ARTIFACT_STATUS_SURFACED,
            delivery_state=proactive_artifacts.DELIVERY_EMAILED,
        )
        reviewed = _make(conn, title="already reviewed", priority=5)
        proactive_artifacts.update_artifact_state(
            conn,
            artifact_id=reviewed["artifact_id"],
            status=proactive_artifacts.ARTIFACT_STATUS_ACCEPTED,
            delivery_state=proactive_artifacts.DELIVERY_REVIEWED,
        )

        pool = proactive_artifacts.list_artifacts(
            conn,
            delivery_state=proactive_artifacts.DELIVERY_NOT_SURFACED,
            limit=250,
        )
        titles = {row["title"] for row in pool}
        assert titles == {"never surfaced"}


class TestFreshLowPriorityBeatsStaleHighPriority:
    def test_order_by_updated_at_prefers_recency_over_priority(self):
        conn = _conn()
        now = datetime(2026, 8, 1, tzinfo=timezone.utc)
        stale_high = _make(conn, title="stale high priority", priority=5)
        _set_updated_at(conn, stale_high["artifact_id"], now - timedelta(days=10))
        fresh_low = _make(conn, title="fresh low priority", priority=1)
        _set_updated_at(conn, fresh_low["artifact_id"], now)

        # With the OLD ordering (priority DESC, updated_at DESC — the
        # default), the stale high-priority row wins a limit=1 window.
        old_order = proactive_artifacts.list_artifacts(
            conn, delivery_state=proactive_artifacts.DELIVERY_NOT_SURFACED, limit=1
        )
        assert old_order[0]["artifact_id"] == stale_high["artifact_id"]

        # With order_by="updated_at", the fresh low-priority row wins instead
        # — this is the fix: recency beats priority within the bounded pool.
        new_order = proactive_artifacts.list_artifacts(
            conn,
            delivery_state=proactive_artifacts.DELIVERY_NOT_SURFACED,
            limit=1,
            order_by="updated_at",
        )
        assert new_order[0]["artifact_id"] == fresh_low["artifact_id"]


class TestArchiveStaleArtifacts:
    def test_archives_matching_rows_via_existing_archive_verb(self):
        conn = _conn()
        stale = _make(conn, title="stale", priority=3)
        fresh = _make(conn, title="fresh", priority=3)

        archived_count = proactive_artifacts.archive_stale_artifacts(
            conn,
            is_stale=lambda artifact: artifact["artifact_id"] == stale["artifact_id"],
        )
        assert archived_count == 1

        refreshed_stale = proactive_artifacts.get_artifact(conn, stale["artifact_id"])
        refreshed_fresh = proactive_artifacts.get_artifact(conn, fresh["artifact_id"])
        assert refreshed_stale["status"] == proactive_artifacts.ARTIFACT_STATUS_ARCHIVED
        assert refreshed_stale["archived_at"]
        assert refreshed_fresh["status"] != proactive_artifacts.ARTIFACT_STATUS_ARCHIVED

    def test_excludes_artifact_types_from_the_sweep(self):
        conn = _conn()
        digest = _make(conn, title="digest", priority=5, artifact_type="daily_digest")

        archived_count = proactive_artifacts.archive_stale_artifacts(
            conn,
            is_stale=lambda artifact: True,  # everything looks stale
            exclude_artifact_types={"daily_digest"},
        )
        assert archived_count == 0
        refreshed = proactive_artifacts.get_artifact(conn, digest["artifact_id"])
        assert refreshed["status"] != proactive_artifacts.ARTIFACT_STATUS_ARCHIVED

    def test_already_archived_rows_are_not_rechecked(self):
        conn = _conn()
        already = _make(
            conn,
            title="already archived",
            priority=3,
            status=proactive_artifacts.ARTIFACT_STATUS_ARCHIVED,
        )
        calls: list[str] = []

        def _is_stale(artifact: dict) -> bool:
            calls.append(artifact["artifact_id"])
            return True

        archived_count = proactive_artifacts.archive_stale_artifacts(conn, is_stale=_is_stale)
        assert archived_count == 0
        assert already["artifact_id"] not in calls

    def test_pool_no_longer_saturated_after_archival(self):
        """Reproduces T15's saturation shape at small scale: many
        never-archived high-priority rows plus one low-priority row must
        not starve the low-priority row out of a small bounded window once
        the high-priority rows are swept as stale."""
        conn = _conn()
        for i in range(5):
            _make(conn, title=f"high priority filler {i}", priority=5)
        signal = _make(conn, title="signal insight", priority=1)

        # Before archival: priority-ordered window of size 1 is saturated by
        # a filler row, not the signal.
        before = proactive_artifacts.list_artifacts(
            conn, delivery_state=proactive_artifacts.DELIVERY_NOT_SURFACED, limit=1
        )
        assert before[0]["artifact_id"] != signal["artifact_id"]

        proactive_artifacts.archive_stale_artifacts(
            conn,
            is_stale=lambda artifact: artifact["artifact_id"] != signal["artifact_id"],
        )

        after = proactive_artifacts.list_artifacts(
            conn,
            delivery_state=proactive_artifacts.DELIVERY_NOT_SURFACED,
            exclude_status=proactive_artifacts.ARTIFACT_STATUS_ARCHIVED,
            limit=1,
        )
        assert len(after) == 1
        assert after[0]["artifact_id"] == signal["artifact_id"]


class TestListArtifactsExcludeStatus:
    def test_exclude_status_filters_out_matching_rows(self):
        conn = _conn()
        kept = _make(conn, title="kept", priority=2)
        archived = _make(
            conn, title="archived", priority=2, status=proactive_artifacts.ARTIFACT_STATUS_ARCHIVED
        )
        results = proactive_artifacts.list_artifacts(
            conn, exclude_status=proactive_artifacts.ARTIFACT_STATUS_ARCHIVED, limit=50
        )
        ids = {row["artifact_id"] for row in results}
        assert kept["artifact_id"] in ids
        assert archived["artifact_id"] not in ids

    def test_default_behavior_unchanged_for_existing_callers(self):
        """Backward-compat guard: no exclude_status/order_by args behaves
        exactly like the pre-T15 list_artifacts (all statuses, priority
        order)."""
        conn = _conn()
        _make(conn, title="a", priority=1)
        _make(conn, title="b", priority=5)
        results = proactive_artifacts.list_artifacts(conn)
        assert [row["title"] for row in results] == ["b", "a"]
