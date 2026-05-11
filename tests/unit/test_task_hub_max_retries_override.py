"""Phase A.1 — per-task ``max_retries`` override unit tests.

Verifies the per-task retry-budget override added in
``docs/reports/hermes-adaptation-phased-plan-2026-05-10.md`` Phase A.1:

* Resolution order: task ``max_retries`` (if set) → caller-supplied
  ``heartbeat_max_retries`` / ``UA_TASK_HUB_TODO_MAX_RETRIES`` → default (3).
* Limit source is recorded on ``metadata.dispatch.{heartbeat,todo}_retry_limit_source``
  ∈ ``{"task", "dispatcher"}`` for dashboards / Simone briefing.
* ``upsert_item`` round-trips the value; absent override → ``NULL``; explicit
  ``None`` clears a previously set override; non-int values fall back to ``NULL``.
"""

from __future__ import annotations

import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.task_hub import _resolve_effective_max_retries


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


# ── upsert_item round-trip ──────────────────────────────────────────────────


def test_upsert_max_retries_round_trips() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:roundtrip",
                "source_kind": "internal",
                "title": "Round-trip override",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
                "max_retries": 5,
            },
        )
        item = task_hub.get_item(conn, "task:roundtrip")
        assert item is not None
        assert item["max_retries"] == 5
    finally:
        conn.close()


def test_upsert_without_max_retries_defaults_to_null() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:default-null",
                "source_kind": "internal",
                "title": "No override set",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
            },
        )
        item = task_hub.get_item(conn, "task:default-null")
        assert item is not None
        assert item["max_retries"] is None
    finally:
        conn.close()


def test_upsert_inherits_existing_max_retries_on_partial_update() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:inherit",
                "source_kind": "internal",
                "title": "Override set initially",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
                "max_retries": 4,
            },
        )
        # Second upsert without the field — must NOT clobber the existing override.
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:inherit",
                "source_kind": "internal",
                "title": "Refreshed title",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
            },
        )
        item = task_hub.get_item(conn, "task:inherit")
        assert item is not None
        assert item["max_retries"] == 4
    finally:
        conn.close()


def test_upsert_explicit_none_clears_existing_max_retries() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:clear",
                "source_kind": "internal",
                "title": "Override then clear",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
                "max_retries": 2,
            },
        )
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:clear",
                "source_kind": "internal",
                "title": "Override then clear",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
                "max_retries": None,
            },
        )
        item = task_hub.get_item(conn, "task:clear")
        assert item is not None
        assert item["max_retries"] is None
    finally:
        conn.close()


def test_upsert_invalid_max_retries_falls_back_to_null() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:invalid",
                "source_kind": "internal",
                "title": "Invalid override",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
                "max_retries": "not-an-int",
            },
        )
        item = task_hub.get_item(conn, "task:invalid")
        assert item is not None
        assert item["max_retries"] is None
    finally:
        conn.close()


def test_upsert_coerces_max_retries_to_minimum_of_one() -> None:
    # Phase A.1: zero/negative is documented as "fall back to dispatcher default"
    # in the resolver (zero retries doesn't make sense as a budget). At the
    # upsert layer we clamp to >=1 to make the operator's intent obvious in the
    # stored value rather than silently treating 0 as "default" downstream.
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:zero",
                "source_kind": "internal",
                "title": "Zero override",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
                "max_retries": 0,
            },
        )
        item = task_hub.get_item(conn, "task:zero")
        assert item is not None
        assert item["max_retries"] == 1
    finally:
        conn.close()


# ── _resolve_effective_max_retries helper ───────────────────────────────────


def test_resolver_returns_task_override_when_set() -> None:
    item = {"max_retries": 5}
    limit, source = _resolve_effective_max_retries(item, fallback_limit=3)
    assert limit == 5
    assert source == "task"


def test_resolver_falls_back_when_task_override_is_none() -> None:
    item = {"max_retries": None}
    limit, source = _resolve_effective_max_retries(item, fallback_limit=7)
    assert limit == 7
    assert source == "dispatcher"


def test_resolver_falls_back_when_task_item_missing() -> None:
    limit, source = _resolve_effective_max_retries(None, fallback_limit=4)
    assert limit == 4
    assert source == "dispatcher"


def test_resolver_falls_back_on_invalid_task_value() -> None:
    item = {"max_retries": "garbage"}
    limit, source = _resolve_effective_max_retries(item, fallback_limit=3)
    assert limit == 3
    assert source == "dispatcher"


def test_resolver_falls_back_on_zero_or_negative_task_value() -> None:
    # Zero/negative retry budget doesn't make sense — fall back to dispatcher
    # default rather than treat as "no retries ever."
    for value in (0, -1, -100):
        limit, source = _resolve_effective_max_retries({"max_retries": value}, fallback_limit=3)
        assert limit == 3, f"value={value} should fall back to 3"
        assert source == "dispatcher", f"value={value} source should be dispatcher"


def test_resolver_clamps_fallback_to_minimum_of_one() -> None:
    # If somehow caller passes 0 / negative as fallback, the resolver clamps
    # to at least 1 so the "exhausted" check never trips spuriously.
    limit, source = _resolve_effective_max_retries(None, fallback_limit=0)
    assert limit == 1
    assert source == "dispatcher"


# ── End-to-end: heartbeat policy with per-task override ─────────────────────


def test_heartbeat_per_task_override_trips_on_first_failure() -> None:
    """Task with ``max_retries=1`` is parked on the FIRST failure, even when
    the dispatcher default would allow 3 retries."""
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:override-1",
                "source_kind": "internal",
                "title": "Override=1 (first failure parks)",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
                "max_retries": 1,
            },
        )

        claimed = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id="heartbeat:trip1")
        assert len(claimed) == 1
        assignment_id = str(claimed[0]["assignment_id"])

        result = task_hub.finalize_assignments(
            conn,
            assignment_ids=[assignment_id],
            state="failed",
            result_summary="first attempt failed",
            reopen_in_progress=True,
            policy="heartbeat",
            heartbeat_max_retries=3,  # dispatcher default is 3
        )

        assert result["reopened"] == 0
        assert result["reviewed"] == 1
        assert result["retry_exhausted"] == 1
        item = task_hub.get_item(conn, "task:override-1")
        assert item is not None
        assert item["status"] == task_hub.TASK_STATUS_REVIEW
        dispatch_meta = (item.get("metadata") or {}).get("dispatch") or {}
        assert dispatch_meta.get("heartbeat_retry_limit") == 1
        assert dispatch_meta.get("heartbeat_retry_limit_source") == "task"
        assert dispatch_meta.get("last_disposition_reason") == "heartbeat_retry_exhausted"
    finally:
        conn.close()


def test_heartbeat_per_task_override_extends_budget() -> None:
    """Task with ``max_retries=5`` allows more retries than the dispatcher
    default of 2 — first two failures reopen rather than parking."""
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:override-5",
                "source_kind": "internal",
                "title": "Override=5 (default=2 would already trip)",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
                "max_retries": 5,
            },
        )

        for attempt in range(1, 4):
            claimed = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id=f"heartbeat:budget-{attempt}")
            assert len(claimed) == 1, f"attempt {attempt} could not claim"
            assignment_id = str(claimed[0]["assignment_id"])
            result = task_hub.finalize_assignments(
                conn,
                assignment_ids=[assignment_id],
                state="failed",
                result_summary=f"attempt {attempt} failed",
                reopen_in_progress=True,
                policy="heartbeat",
                heartbeat_max_retries=2,  # dispatcher default would normally trip
            )
            # Each of the first 4 attempts should reopen (override is 5).
            assert result["reopened"] == 1, f"attempt {attempt}: {result}"
            assert result["reviewed"] == 0
            item = task_hub.get_item(conn, "task:override-5")
            assert item is not None
            assert item["status"] == task_hub.TASK_STATUS_OPEN
            dispatch_meta = (item.get("metadata") or {}).get("dispatch") or {}
            assert dispatch_meta.get("heartbeat_retry_limit") == 5
            assert dispatch_meta.get("heartbeat_retry_limit_source") == "task"
            assert dispatch_meta.get("heartbeat_retry_count") == attempt
    finally:
        conn.close()


def test_heartbeat_no_override_uses_dispatcher_default() -> None:
    """Task with ``max_retries=NULL`` falls back to ``heartbeat_max_retries=2``
    — second failure parks per the dispatcher default."""
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:no-override",
                "source_kind": "internal",
                "title": "Default-bucket task",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
            },
        )

        first_claim = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id="heartbeat:default1")
        first = task_hub.finalize_assignments(
            conn,
            assignment_ids=[str(first_claim[0]["assignment_id"])],
            state="failed",
            result_summary="first failed",
            reopen_in_progress=True,
            policy="heartbeat",
            heartbeat_max_retries=2,
        )
        assert first["reopened"] == 1
        assert first["reviewed"] == 0
        item_after_1 = task_hub.get_item(conn, "task:no-override")
        assert item_after_1 is not None
        dispatch_meta = (item_after_1.get("metadata") or {}).get("dispatch") or {}
        assert dispatch_meta.get("heartbeat_retry_limit") == 2
        assert dispatch_meta.get("heartbeat_retry_limit_source") == "dispatcher"

        second_claim = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id="heartbeat:default2")
        second = task_hub.finalize_assignments(
            conn,
            assignment_ids=[str(second_claim[0]["assignment_id"])],
            state="failed",
            result_summary="second failed",
            reopen_in_progress=True,
            policy="heartbeat",
            heartbeat_max_retries=2,
        )
        assert second["reopened"] == 0
        assert second["reviewed"] == 1
        assert second["retry_exhausted"] == 1
        item_after_2 = task_hub.get_item(conn, "task:no-override")
        assert item_after_2 is not None
        assert item_after_2["status"] == task_hub.TASK_STATUS_REVIEW
        dispatch_meta = (item_after_2.get("metadata") or {}).get("dispatch") or {}
        assert dispatch_meta.get("heartbeat_retry_limit_source") == "dispatcher"
    finally:
        conn.close()


# ── End-to-end: todo policy with per-task override ──────────────────────────


def test_todo_per_task_override_trips_on_first_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same as heartbeat-trip-on-first-failure but for ``policy='todo'``."""
    # Set the env var so the dispatcher default is generous; task override should override.
    monkeypatch.setenv("UA_TASK_HUB_TODO_MAX_RETRIES", "5")
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:todo-override-1",
                "source_kind": "internal",
                "title": "ToDo override=1",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
                "max_retries": 1,
            },
        )

        claimed = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id="todo:trip1")
        assignment_id = str(claimed[0]["assignment_id"])
        result = task_hub.finalize_assignments(
            conn,
            assignment_ids=[assignment_id],
            state="failed",
            result_summary="first todo failure",
            reopen_in_progress=True,
            policy="todo",
        )
        assert result["reopened"] == 0
        assert result["reviewed"] == 1
        assert result["retry_exhausted"] == 1
        item = task_hub.get_item(conn, "task:todo-override-1")
        assert item is not None
        assert item["status"] == task_hub.TASK_STATUS_REVIEW
        dispatch_meta = (item.get("metadata") or {}).get("dispatch") or {}
        assert dispatch_meta.get("todo_retry_limit") == 1
        assert dispatch_meta.get("todo_retry_limit_source") == "task"
        assert dispatch_meta.get("last_disposition_reason") == "todo_retry_exhausted"
    finally:
        conn.close()
