"""Hermes Phase C — Atlas-direct-dispatch unit tests.

Covers the service module that bypasses Simone's heartbeat throttle for
tasks pre-tagged with ``metadata.preferred_vp = "vp.general.primary"``.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any

import pytest

from universal_agent import task_hub
from universal_agent.services import atlas_direct_dispatch as ada


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _seed_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    status: str = task_hub.TASK_STATUS_OPEN,
    agent_ready: bool = True,
    source_kind: str = "internal",
    metadata: dict[str, Any] | None = None,
    description: str = "do the thing",
) -> dict[str, Any]:
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": source_kind,
            "title": f"task {task_id}",
            "description": description,
            "status": status,
            "agent_ready": agent_ready,
            "metadata": metadata or {},
        },
    )
    return task_hub.get_item(conn, task_id)


# ── find_atlas_direct_candidates ──────────────────────────────────────────


def test_finds_preferred_vp_tagged_open_task() -> None:
    conn = _conn()
    try:
        _seed_task(
            conn,
            task_id="t1",
            metadata={"preferred_vp": "vp.general.primary"},
        )
        candidates = ada.find_atlas_direct_candidates(conn, limit=5)
        assert len(candidates) == 1
        assert candidates[0]["task_id"] == "t1"
    finally:
        conn.close()


def test_skips_untagged_task() -> None:
    conn = _conn()
    try:
        _seed_task(conn, task_id="t2", metadata={})
        assert ada.find_atlas_direct_candidates(conn, limit=5) == []
    finally:
        conn.close()


def test_skips_vp_mission_mirror_rows() -> None:
    """source_kind='vp_mission' is the VP mirror lane; never claim those."""
    conn = _conn()
    try:
        _seed_task(
            conn,
            task_id="t3",
            source_kind="vp_mission",
            metadata={"preferred_vp": "vp.general.primary"},
        )
        assert ada.find_atlas_direct_candidates(conn, limit=5) == []
    finally:
        conn.close()


def test_skips_already_dispatched_task() -> None:
    """Idempotency: once atlas_direct_dispatched_at is set, never re-pick."""
    conn = _conn()
    try:
        _seed_task(
            conn,
            task_id="t4",
            metadata={
                "preferred_vp": "vp.general.primary",
                "dispatch": {"atlas_direct_dispatched_at": "2026-05-11T15:00:00+00:00"},
            },
        )
        assert ada.find_atlas_direct_candidates(conn, limit=5) == []
    finally:
        conn.close()


def test_skips_non_open_status() -> None:
    conn = _conn()
    try:
        _seed_task(
            conn,
            task_id="t5",
            status=task_hub.TASK_STATUS_REVIEW,
            metadata={"preferred_vp": "vp.general.primary"},
        )
        assert ada.find_atlas_direct_candidates(conn, limit=5) == []
    finally:
        conn.close()


# ── try_claim_atlas_direct ────────────────────────────────────────────────


def test_claim_sets_tracking_fields_first_time() -> None:
    conn = _conn()
    try:
        _seed_task(
            conn,
            task_id="claim1",
            metadata={"preferred_vp": "vp.general.primary"},
        )
        ok = ada.try_claim_atlas_direct(
            conn,
            task_id="claim1",
            now_iso="2026-05-11T15:00:00+00:00",
            objective_preview="do this thing now",
        )
        assert ok is True
        item = task_hub.get_item(conn, "claim1")
        dispatch = (item.get("metadata") or {}).get("dispatch", {})
        assert dispatch["atlas_direct_dispatched_at"] == "2026-05-11T15:00:00+00:00"
        assert dispatch["atlas_direct_lane"] == "atlas_direct"
        assert dispatch["atlas_direct_assignee"] == "vp.general.primary"
        assert dispatch["atlas_direct_objective_preview"] == "do this thing now"
    finally:
        conn.close()


def test_claim_rejects_second_call() -> None:
    """Atomic claim: the second caller must get False, no fields overwritten."""
    conn = _conn()
    try:
        _seed_task(
            conn,
            task_id="claim2",
            metadata={"preferred_vp": "vp.general.primary"},
        )
        first = ada.try_claim_atlas_direct(
            conn, task_id="claim2", now_iso="2026-05-11T15:00:00+00:00"
        )
        second = ada.try_claim_atlas_direct(
            conn, task_id="claim2", now_iso="2026-05-11T15:05:00+00:00"
        )
        assert first is True
        assert second is False
        item = task_hub.get_item(conn, "claim2")
        # First claim's timestamp is preserved.
        assert (item["metadata"]["dispatch"]["atlas_direct_dispatched_at"]
                == "2026-05-11T15:00:00+00:00")
    finally:
        conn.close()


# ── dispatch_atlas_candidates_once ───────────────────────────────────────


@pytest.mark.asyncio
async def test_orchestrator_dispatches_and_records_idempotency() -> None:
    """End-to-end: candidate found → claim sets fields → dispatch_fn called once."""
    conn = _conn()
    try:
        _seed_task(
            conn,
            task_id="e2e1",
            description="generate the daily briefing",
            metadata={"preferred_vp": "vp.general.primary"},
        )

        dispatched_calls: list[dict[str, Any]] = []

        async def _stub_dispatch(**kwargs) -> dict[str, Any]:
            dispatched_calls.append(kwargs)
            return {"ok": True, "mission_id": "vp-mission-stub"}

        result = await ada.dispatch_atlas_candidates_once(
            conn,
            max_general_slots=2,
            dispatch_fn=_stub_dispatch,
        )
        assert result["dispatched"] == 1
        assert result["skipped"] == 0
        assert len(dispatched_calls) == 1
        call = dispatched_calls[0]
        assert call["vp_id"] == "vp.general.primary"
        assert call["mission_type"] == "proactive_general"
        assert call["idempotency_key"] == "atlas-direct-e2e1"
        assert call["source_session_id"] == "atlas_direct_dispatch"
        assert call["objective"] == "generate the daily briefing"

        item = task_hub.get_item(conn, "e2e1")
        dispatch = (item.get("metadata") or {}).get("dispatch", {})
        assert dispatch.get("atlas_direct_dispatched_at")
        assert dispatch.get("atlas_direct_assignee") == "vp.general.primary"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_orchestrator_respects_max_slots() -> None:
    conn = _conn()
    try:
        for i in range(3):
            _seed_task(
                conn,
                task_id=f"slot{i}",
                metadata={"preferred_vp": "vp.general.primary"},
            )

        called: list[Any] = []

        async def _stub_dispatch(**kwargs):
            called.append(kwargs)
            return {"ok": True, "mission_id": f"mid-{len(called)}"}

        result = await ada.dispatch_atlas_candidates_once(
            conn,
            max_general_slots=2,  # cap = 2 slots, 0 active → 2 remaining
            dispatch_fn=_stub_dispatch,
        )
        assert result["dispatched"] == 2
        assert len(called) == 2
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_orchestrator_records_error_and_locks_task_when_dispatch_fails() -> None:
    """If dispatch_vp_mission raises, the task is claim-locked + error recorded.

    The next sweep MUST NOT re-dispatch (operator must rehydrate via B.1).
    """
    conn = _conn()
    try:
        _seed_task(
            conn,
            task_id="fail1",
            metadata={"preferred_vp": "vp.general.primary"},
        )

        async def _failing_dispatch(**kwargs) -> dict[str, Any]:
            raise RuntimeError("upstream VP unreachable")

        result = await ada.dispatch_atlas_candidates_once(
            conn,
            max_general_slots=2,
            dispatch_fn=_failing_dispatch,
        )
        assert result["dispatched"] == 0
        item = task_hub.get_item(conn, "fail1")
        dispatch = (item.get("metadata") or {}).get("dispatch", {})
        # Claim went through (task is locked) AND error was recorded.
        assert dispatch.get("atlas_direct_dispatched_at")
        assert "upstream VP unreachable" in dispatch.get(
            "atlas_direct_dispatch_error", ""
        )

        # Second sweep: claim already taken, no candidates remain.
        assert ada.find_atlas_direct_candidates(conn, limit=5) == []
    finally:
        conn.close()


# ── list_recent_atlas_direct_dispatches (C.2 briefing) ──────────────────


def test_list_recent_returns_dispatched_tasks_only() -> None:
    conn = _conn()
    try:
        # Dispatched recently.
        ada.try_claim_atlas_direct(
            conn,
            task_id="recent1",
            now_iso="2026-05-11T15:00:00+00:00",
            objective_preview="recent task",
        )
        _seed_task(
            conn,
            task_id="recent1",
            metadata={
                "preferred_vp": "vp.general.primary",
                "dispatch": {
                    "atlas_direct_dispatched_at": "2026-05-11T15:00:00+00:00",
                    "atlas_direct_objective_preview": "recent task",
                },
            },
        )
        # Not dispatched.
        _seed_task(
            conn,
            task_id="never",
            metadata={"preferred_vp": "vp.general.primary"},
        )
        # The within_minutes filter uses datetime('now', '-15 minutes') in
        # SQLite, so seed a row with a fresh-enough timestamp via a direct
        # update relative to the SQLite session's now().
        conn.execute(
            "UPDATE task_hub_items SET metadata_json = json_set(metadata_json, "
            "'$.dispatch.atlas_direct_dispatched_at', "
            "strftime('%Y-%m-%dT%H:%M:%fZ', datetime('now', '-2 minutes'))) "
            "WHERE task_id = ?",
            ("recent1",),
        )
        rows = ada.list_recent_atlas_direct_dispatches(
            conn, within_minutes=15, limit=10
        )
        ids = [r["task_id"] for r in rows]
        assert "recent1" in ids
        assert "never" not in ids
    finally:
        conn.close()
