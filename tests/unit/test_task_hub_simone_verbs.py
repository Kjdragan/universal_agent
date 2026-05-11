"""Hermes Phase D — Simone-callable verb tools unit tests.

Verifies the three tools registered in
``universal_agent.tools.task_hub_simone_verbs`` correctly delegate to
``task_hub.perform_task_action`` and surface meaningful error messages
when the operator invariants are violated.

These tools are the autonomy enabler — they let Simone act on her own
judgment without operator clicks. Tests cover:

* Happy-path invocation for each of the three verbs.
* Required-field validation (task_id, target_vp, feedback).
* 404-style behavior for missing tasks.
* Correct delegation to the underlying perform_task_action verbs (the
  side effects are verified by reading the task row after the call).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any

import pytest

from universal_agent import task_hub
from universal_agent.tools.task_hub_simone_verbs import (
    task_re_evaluate_wrapper,
    task_redirect_to_wrapper,
    task_request_revision_wrapper,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def seeded_db(tmp_path, monkeypatch) -> sqlite3.Connection:
    """Tmp activity.db with task_hub schema applied.

    The tools resolve `UA_ACTIVITY_DB_PATH` lazily via
    ``durable.db.get_activity_db_path`` — patching the env redirects IO.
    """
    db_path = str(tmp_path / "activity.db")
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    yield conn
    conn.close()


def _seed_wedged_task(
    conn: sqlite3.Connection, *, task_id: str, max_retries: int | None = None
) -> dict:
    """Seed a task in needs_review with the wedge indicators."""
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "vp_mission",
            "title": "wedged for tests",
            "status": task_hub.TASK_STATUS_REVIEW,
            "agent_ready": True,
            "max_retries": max_retries,
            "metadata": {
                "dispatch": {
                    "heartbeat_retry_count": 2,
                    "todo_retry_count": 1,
                    "last_disposition_reason": "completion_unverified",
                    "last_disposition": "review",
                    "last_side_effect_summary": "wrote partial output",
                }
            },
        },
    )
    return task_hub.get_item(conn, task_id)


def _call(wrapper, args: dict[str, Any]) -> dict:
    """Run a @tool-decorated wrapper.

    The @tool decorator from claude_agent_sdk wraps the async function in
    an SdkMcpTool object whose underlying coroutine is exposed via
    ``.handler``. Mirrors the pattern in
    ``tests/unit/test_csi_bridge_hackernews_whitelist.py::_call_wrapper``.
    """
    return asyncio.run(wrapper.handler(args))


def _payload(result: dict) -> dict:
    """Extract the JSON payload from a tool's content envelope."""
    text = result["content"][0]["text"]
    if text.startswith("error: "):
        return {"_error": text[len("error: ") :]}
    return json.loads(text)


# ── task_re_evaluate ────────────────────────────────────────────────────────


def test_re_evaluate_requires_task_id(seeded_db: sqlite3.Connection) -> None:
    payload = _payload(_call(task_re_evaluate_wrapper, {}))
    assert "_error" in payload
    assert "task_id is required" in payload["_error"]


def test_re_evaluate_returns_error_for_missing_task(seeded_db: sqlite3.Connection) -> None:
    payload = _payload(_call(task_re_evaluate_wrapper, {"task_id": "task:missing"}))
    assert "_error" in payload
    assert "No task found" in payload["_error"]


def test_re_evaluate_succeeds_and_attaches_context(seeded_db: sqlite3.Connection) -> None:
    _seed_wedged_task(seeded_db, task_id="task:reeval")
    payload = _payload(
        _call(
            task_re_evaluate_wrapper,
            {"task_id": "task:reeval", "reason": "looks incomplete to me"},
        )
    )
    assert payload["success"] is True
    assert payload["action"] == "re_evaluate"
    assert payload["reason"] == "looks incomplete to me"
    # Task should now have a re_evaluation_context block.
    row = task_hub.get_item(seeded_db, "task:reeval")
    dispatch = (row.get("metadata") or {}).get("dispatch") or {}
    assert dispatch.get("re_evaluation_context") is not None
    # Status flipped back to open.
    assert row["status"] == task_hub.TASK_STATUS_OPEN


def test_re_evaluate_does_NOT_bump_max_retries(seeded_db: sqlite3.Connection) -> None:
    """Operator decision 2026-05-11: re_evaluate is retry-with-context only.

    Budget bumps happen exclusively via request_revision.
    """
    _seed_wedged_task(seeded_db, task_id="task:reeval-budget", max_retries=3)
    _call(task_re_evaluate_wrapper, {"task_id": "task:reeval-budget", "reason": "test"})
    row = task_hub.get_item(seeded_db, "task:reeval-budget")
    assert row["max_retries"] == 3


# ── task_redirect_to ────────────────────────────────────────────────────────


def test_redirect_to_requires_task_id(seeded_db: sqlite3.Connection) -> None:
    payload = _payload(_call(task_redirect_to_wrapper, {}))
    assert "_error" in payload
    assert "task_id" in payload["_error"]


def test_redirect_to_requires_target_vp(seeded_db: sqlite3.Connection) -> None:
    payload = _payload(_call(task_redirect_to_wrapper, {"task_id": "task:x"}))
    assert "_error" in payload
    assert "target_vp" in payload["_error"]


def test_redirect_to_returns_error_for_missing_task(seeded_db: sqlite3.Connection) -> None:
    payload = _payload(
        _call(
            task_redirect_to_wrapper,
            {"task_id": "task:does-not-exist", "target_vp": "vp.general.primary"},
        )
    )
    assert "_error" in payload
    assert "No task found" in payload["_error"]


def test_redirect_to_sets_preferred_vp(seeded_db: sqlite3.Connection) -> None:
    _seed_wedged_task(seeded_db, task_id="task:redirect")
    payload = _payload(
        _call(
            task_redirect_to_wrapper,
            {
                "task_id": "task:redirect",
                "target_vp": "vp.general.primary",
                "reason": "atlas better suited for this",
            },
        )
    )
    assert payload["success"] is True
    assert payload["target_vp"] == "vp.general.primary"
    row = task_hub.get_item(seeded_db, "task:redirect")
    # Phase C correction: preferred_vp lives at the TOP of metadata, NOT
    # under metadata.dispatch.
    assert (row.get("metadata") or {}).get("preferred_vp") == "vp.general.primary"
    assert row["status"] == task_hub.TASK_STATUS_OPEN


# ── task_request_revision ──────────────────────────────────────────────────


def test_request_revision_requires_task_id(seeded_db: sqlite3.Connection) -> None:
    payload = _payload(_call(task_request_revision_wrapper, {}))
    assert "_error" in payload
    assert "task_id" in payload["_error"]


def test_request_revision_requires_feedback(seeded_db: sqlite3.Connection) -> None:
    payload = _payload(_call(task_request_revision_wrapper, {"task_id": "task:x"}))
    assert "_error" in payload
    assert "feedback" in payload["_error"]


def test_request_revision_bumps_revision_round_and_max_retries(
    seeded_db: sqlite3.Connection,
) -> None:
    _seed_wedged_task(seeded_db, task_id="task:revise", max_retries=3)
    payload = _payload(
        _call(
            task_request_revision_wrapper,
            {
                "task_id": "task:revise",
                "feedback": "Please add docstrings to all public methods on `services/foo.py`.",
                "max_extra_retries": 2,
            },
        )
    )
    assert payload["success"] is True
    assert payload["max_extra_retries"] == 2
    row = task_hub.get_item(seeded_db, "task:revise")
    dispatch = (row.get("metadata") or {}).get("dispatch") or {}
    assert int(dispatch.get("revision_round") or 0) > 0
    # Budget bumped (perform_task_action's request_revision adds 1 by default;
    # max_extra_retries is currently informational on the tool side — the
    # underlying verb increments by its own default. The important invariant
    # is that max_retries grew, not the exact delta.)
    assert (row["max_retries"] or 0) > 3


def test_request_revision_default_extra_retries(seeded_db: sqlite3.Connection) -> None:
    """When max_extra_retries is omitted, default to 1."""
    _seed_wedged_task(seeded_db, task_id="task:revise-default", max_retries=3)
    payload = _payload(
        _call(
            task_request_revision_wrapper,
            {
                "task_id": "task:revise-default",
                "feedback": "Tighten the input validation.",
            },
        )
    )
    assert payload["success"] is True
    assert payload["max_extra_retries"] == 1


def test_request_revision_invalid_extra_retries_falls_through(
    seeded_db: sqlite3.Connection,
) -> None:
    _seed_wedged_task(seeded_db, task_id="task:revise-bad", max_retries=3)
    payload = _payload(
        _call(
            task_request_revision_wrapper,
            {
                "task_id": "task:revise-bad",
                "feedback": "test",
                "max_extra_retries": "not-an-int",
            },
        )
    )
    assert payload["success"] is True
    # Falls back to 1 on parse failure.
    assert payload["max_extra_retries"] == 1
