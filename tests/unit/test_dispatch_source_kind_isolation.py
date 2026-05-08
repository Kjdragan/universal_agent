"""Tests for the source_kind isolation gate on dispatch claims.

These tests cover the two-layer fix described in
``docs/operations/2026-05-07_open_followups.md`` Followup #3:

  Layer 1 (producer):  vp_orchestration.py mirrors VP missions to Task Hub
                        with agent_ready=False, so they're not eligible for
                        the dispatch queue regardless of who calls
                        claim_next_dispatch_tasks.

  Layer 2 (dispatcher): claim_next_dispatch_tasks accepts an optional
                        forbidden_source_kinds parameter and filters at
                        SQL time. Defense-in-depth backstop for any other
                        path that might surface a vp_mission row as
                        agent_ready=True.
"""

from __future__ import annotations

import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.services.dispatch_service import dispatch_sweep


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _insert_task(conn: sqlite3.Connection, task_id: str, **overrides) -> dict:
    item = {
        "task_id": task_id,
        "title": f"Test {task_id}",
        "status": task_hub.TASK_STATUS_OPEN,
        "source_kind": "internal",
        "agent_ready": True,
        "labels": ["agent-ready"],
    }
    item.update(overrides)
    # task_hub.upsert_item:980-982 re-promotes agent_ready=True when the
    # "agent-ready" label is present. If the caller explicitly disabled
    # agent_ready, drop the auto-added label so the explicit setting wins.
    if not item.get("agent_ready"):
        item["labels"] = [
            label for label in (item.get("labels") or []) if label != "agent-ready"
        ]
    return task_hub.upsert_item(conn, item)


def _claimed_task_ids(claimed: list[dict]) -> set[str]:
    return {str(item.get("task_id") or "") for item in claimed}


# ---------------------------------------------------------------------------
# Layer 1 — producer-side: agent_ready=False keeps vp_mission out of the queue
# ---------------------------------------------------------------------------


class TestProducerSideAgentReadyFalse:
    """When the vp_orchestration mirror writes agent_ready=False, the row is
    not eligible for the dispatch queue and won't be returned by
    claim_next_dispatch_tasks regardless of the caller's filter."""

    def test_vp_mission_with_agent_ready_false_is_not_claimed(self):
        conn = _make_conn()
        # Mirror a VP mission as the new producer-side fix writes it
        _insert_task(
            conn,
            "vp-mission-test-1",
            source_kind="vp_mission",
            agent_ready=False,
            status=task_hub.TASK_STATUS_OPEN,
        )
        # Sanity: insert a normal claimable task so claim has SOMETHING to find
        _insert_task(conn, "internal-test-1", source_kind="internal")

        claimed = task_hub.claim_next_dispatch_tasks(conn, limit=10)
        ids = _claimed_task_ids(claimed)
        assert "vp-mission-test-1" not in ids
        assert "internal-test-1" in ids

    def test_vp_mission_open_after_reopen_stays_unclaimable_when_agent_ready_false(self):
        """reopen_stale_delegations only sets status/seizure_state — it does
        NOT touch agent_ready. So a mirror that started as agent_ready=False
        stays uneligible even after reopen flips it back to OPEN."""
        conn = _make_conn()
        # Insert as DELEGATED (where reopen_stale_delegations operates)
        _insert_task(
            conn,
            "vp-mission-test-2",
            source_kind="vp_mission",
            agent_ready=False,
            status=task_hub.TASK_STATUS_DELEGATED,
        )
        # Force update_at into the past so reopen sees it as stale
        conn.execute(
            "UPDATE task_hub_items SET updated_at = '2020-01-01T00:00:00+00:00' WHERE task_id = ?",
            ("vp-mission-test-2",),
        )
        reopened = task_hub.reopen_stale_delegations(conn, stale_hours=1.0)
        assert any(t["task_id"] == "vp-mission-test-2" for t in reopened)

        # Status is now OPEN, but agent_ready survived as False
        row = task_hub.get_item(conn, "vp-mission-test-2")
        assert row is not None
        assert row["status"] == task_hub.TASK_STATUS_OPEN
        assert bool(row["agent_ready"]) is False

        # Therefore not claimable
        claimed = task_hub.claim_next_dispatch_tasks(conn, limit=10)
        assert "vp-mission-test-2" not in _claimed_task_ids(claimed)


# ---------------------------------------------------------------------------
# Layer 2 — dispatcher-side: forbidden_source_kinds filters at SQL time
# ---------------------------------------------------------------------------


class TestDispatcherSideForbiddenSourceKinds:
    """Even if some other path puts a vp_mission row at agent_ready=True,
    a caller passing forbidden_source_kinds=['vp_mission'] won't see it."""

    def test_forbidden_source_kinds_excludes_matching_rows(self):
        conn = _make_conn()
        # Hostile / legacy mirror: agent_ready=True (the bug we're guarding against)
        _insert_task(
            conn,
            "vp-mission-test-3",
            source_kind="vp_mission",
            agent_ready=True,
        )
        _insert_task(conn, "internal-test-3", source_kind="internal")

        # Without the filter, the legacy-mirror row WOULD be claimed (this is
        # the exact bug Followup #3 documents).
        claimed_unfiltered = task_hub.claim_next_dispatch_tasks(conn, limit=10)
        assert "vp-mission-test-3" in _claimed_task_ids(claimed_unfiltered)

        # Reset the dispatch state by re-inserting fresh task_ids — claimed
        # tasks from the previous call now have status=in_progress and
        # wouldn't appear again anyway, so create a parallel pair.
        _insert_task(
            conn,
            "vp-mission-test-3b",
            source_kind="vp_mission",
            agent_ready=True,
        )
        _insert_task(conn, "internal-test-3b", source_kind="internal")

        # WITH the filter, vp_mission rows are excluded.
        claimed_filtered = task_hub.claim_next_dispatch_tasks(
            conn,
            limit=10,
            forbidden_source_kinds=["vp_mission"],
        )
        ids = _claimed_task_ids(claimed_filtered)
        assert "vp-mission-test-3b" not in ids
        assert "internal-test-3b" in ids

    def test_forbidden_source_kinds_supports_multiple_kinds(self):
        conn = _make_conn()
        _insert_task(conn, "vp-1", source_kind="vp_mission", agent_ready=True)
        _insert_task(conn, "ext-1", source_kind="external_mirror", agent_ready=True)
        _insert_task(conn, "ok-1", source_kind="internal", agent_ready=True)

        claimed = task_hub.claim_next_dispatch_tasks(
            conn,
            limit=10,
            forbidden_source_kinds=["vp_mission", "external_mirror"],
        )
        ids = _claimed_task_ids(claimed)
        assert "vp-1" not in ids
        assert "ext-1" not in ids
        assert "ok-1" in ids

    def test_forbidden_source_kinds_none_or_empty_is_no_op(self):
        """Backwards compatibility — existing callers that don't pass the
        param see the same behavior as before."""
        conn = _make_conn()
        _insert_task(conn, "any-1", source_kind="anything", agent_ready=True)

        # No param at all
        claimed = task_hub.claim_next_dispatch_tasks(conn, limit=10)
        assert "any-1" in _claimed_task_ids(claimed)

        # Re-create after the previous claim flipped status
        _insert_task(conn, "any-1b", source_kind="anything", agent_ready=True)

        # Empty list also a no-op
        claimed_empty = task_hub.claim_next_dispatch_tasks(
            conn,
            limit=10,
            forbidden_source_kinds=[],
        )
        assert "any-1b" in _claimed_task_ids(claimed_empty)


# ---------------------------------------------------------------------------
# Layer 2 plumbing — dispatch_sweep threads forbidden_source_kinds through
# ---------------------------------------------------------------------------


class TestDispatchSweepThreadsForbiddenSourceKinds:
    def test_dispatch_sweep_passes_forbidden_source_kinds_through(self):
        conn = _make_conn()
        _insert_task(conn, "vp-sweep-1", source_kind="vp_mission", agent_ready=True)
        _insert_task(conn, "ok-sweep-1", source_kind="internal")

        claimed = dispatch_sweep(
            conn,
            agent_id="todo:test",
            limit=10,
            forbidden_source_kinds=["vp_mission"],
        )
        ids = _claimed_task_ids(claimed)
        assert "vp-sweep-1" not in ids
        assert "ok-sweep-1" in ids

    def test_dispatch_sweep_without_filter_sees_all_kinds(self):
        """Sanity: dispatch_sweep without forbidden_source_kinds is unchanged."""
        conn = _make_conn()
        _insert_task(conn, "any-sweep-1", source_kind="vp_mission", agent_ready=True)
        _insert_task(conn, "ok-sweep-2", source_kind="internal")

        claimed = dispatch_sweep(conn, agent_id="todo:test", limit=10)
        ids = _claimed_task_ids(claimed)
        assert "any-sweep-1" in ids
        assert "ok-sweep-2" in ids


# ---------------------------------------------------------------------------
# Combined: producer fix + dispatcher backstop together
# ---------------------------------------------------------------------------


class TestCombinedDefense:
    def test_producer_and_dispatcher_layers_compose(self):
        """The realistic shipped configuration: producer writes
        agent_ready=False AND dispatcher passes forbidden_source_kinds.
        The vp_mission row is unreachable through either layer alone."""
        conn = _make_conn()
        # Producer-side fix in effect: agent_ready=False
        _insert_task(
            conn,
            "vp-defense-1",
            source_kind="vp_mission",
            agent_ready=False,
        )
        _insert_task(conn, "ok-defense-1", source_kind="internal")

        # Even with both layers active, normal claims work
        claimed = dispatch_sweep(
            conn,
            agent_id="todo:test",
            limit=10,
            forbidden_source_kinds=["vp_mission"],
        )
        ids = _claimed_task_ids(claimed)
        assert "vp-defense-1" not in ids
        assert "ok-defense-1" in ids
