"""Unit tests for the Mission Control "Mark Complete" card endpoint.

Verifies the
`POST /api/v1/dashboard/mission-control/cards/{card_id}/complete`
handler added to support the operator backstop button. Tests call the
handler coroutine directly, mirroring the style of
`test_dashboard_failure_context_endpoint.py`.

Coverage:
  - happy path: task-kind card → task flips to completed, card retires
  - mission-kind card behaves identically (subject_id IS the task_id
    for VP missions)
  - infra/alert-kind card → 400 with a useful message
  - card with no subject_id → 400
  - subject_id with no task_hub row → 404
  - delivery-verification-gate parks task in review → endpoint forces
    completion via operator_override
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3
from typing import Any

from fastapi import HTTPException
import pytest

from universal_agent import task_hub
from universal_agent.gateway_server import (
    dashboard_mission_control_card_complete,
)
from universal_agent.services.mission_control_cards import (
    CardUpsert,
    get_card,
    make_card_id,
    upsert_card,
)
from universal_agent.services.mission_control_db import open_store


@pytest.fixture
def setup_dbs(tmp_path: Path, monkeypatch):
    """Point both the activity DB and the mission_control intel DB at
    tmp_path. The endpoint resolves them lazily via env-var lookups, so
    monkeypatching at the env level is enough."""
    activity_path = tmp_path / "activity.db"
    intel_path = tmp_path / "mc.db"
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(activity_path))
    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", str(intel_path))

    # Initialize task_hub schema.
    th_conn = sqlite3.connect(str(activity_path))
    th_conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(th_conn)

    # Initialize mission_control_cards schema (open_store does this).
    mc_conn = open_store(intel_path)

    yield {
        "activity_path": activity_path,
        "intel_path": intel_path,
        "th_conn": th_conn,
        "mc_conn": mc_conn,
    }

    th_conn.close()
    mc_conn.close()


def _seed_task(conn: sqlite3.Connection, *, task_id: str, source_kind: str = "internal",
               status: str = task_hub.TASK_STATUS_BLOCKED,
               metadata: dict[str, Any] | None = None) -> None:
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": source_kind,
            "title": f"test {task_id}",
            "status": status,
            "metadata": metadata or {},
        },
    )
    conn.commit()


def _seed_card(conn: sqlite3.Connection, *, subject_kind: str, subject_id: str) -> str:
    """Insert a live card and return its card_id. CardUpsert manages
    server-side fields (recurrence_count, first_observed_at, etc.); we
    only provide the operator-facing payload."""
    upsert_card(
        conn,
        CardUpsert(
            subject_kind=subject_kind,
            subject_id=subject_id,
            severity="warning",
            title=f"Test card for {subject_id}",
            narrative="Synthetic test card.",
            why_it_matters="It's a test.",
            recommended_next_step="Click Mark Complete.",
            tags=["test"],
            evidence_refs=[],
            evidence_payload={},
        ),
    )
    conn.commit()
    return make_card_id(subject_kind, subject_id)


def _call(card_id: str) -> dict:
    return asyncio.run(dashboard_mission_control_card_complete(card_id))


# ── Happy path ───────────────────────────────────────────────────────


def test_complete_task_kind_card_closes_task_and_retires_card(setup_dbs):
    th = setup_dbs["th_conn"]
    mc = setup_dbs["mc_conn"]
    _seed_task(th, task_id="task:ABC")
    card_id = _seed_card(mc, subject_kind="task", subject_id="task:ABC")

    result = _call(card_id)

    assert result["status"] == "ok"
    assert result["result"] == "completed"
    assert result["task_id"] == "task:ABC"

    # Reopen the task hub connection — the endpoint used its own.
    th2 = sqlite3.connect(str(setup_dbs["activity_path"]))
    th2.row_factory = sqlite3.Row
    item = task_hub.get_item(th2, "task:ABC")
    th2.close()
    assert item["status"] == task_hub.TASK_STATUS_COMPLETED

    # Card should be retired.
    mc2 = open_store(setup_dbs["intel_path"])
    card = get_card(mc2, card_id)
    mc2.close()
    assert card["current_state"] == "retired"


def test_complete_mission_kind_card_closes_mission(setup_dbs):
    """For VP missions, subject_id IS the task_id (verified by the
    2026-05-12 production probe). The endpoint handles 'mission' kind
    identically to 'task' kind."""
    th = setup_dbs["th_conn"]
    mc = setup_dbs["mc_conn"]
    _seed_task(th, task_id="vp-mission-deadbeef", source_kind="vp_mission")
    card_id = _seed_card(mc, subject_kind="mission", subject_id="vp-mission-deadbeef")

    result = _call(card_id)
    assert result["status"] == "ok"
    assert result["task_id"] == "vp-mission-deadbeef"

    th2 = sqlite3.connect(str(setup_dbs["activity_path"]))
    th2.row_factory = sqlite3.Row
    item = task_hub.get_item(th2, "vp-mission-deadbeef")
    th2.close()
    assert item["status"] == task_hub.TASK_STATUS_COMPLETED


# ── Rejection cases ──────────────────────────────────────────────────


def test_complete_rejects_infrastructure_kind_card(setup_dbs):
    """Infra-tile cards (e.g. CronPipelines red) don't have a sensible
    'complete' semantics — they're auto-managed by tier-0. Reject with
    a useful message pointing the operator at /dismiss."""
    mc = setup_dbs["mc_conn"]
    card_id = _seed_card(mc, subject_kind="infrastructure", subject_id="infra:cron_pipelines")

    with pytest.raises(HTTPException) as exc:
        _call(card_id)
    assert exc.value.status_code == 400
    assert "dismiss" in str(exc.value.detail).lower()


def test_complete_rejects_card_without_subject_id(setup_dbs):
    """A card with subject_kind=task but no subject_id is malformed.
    Reject so we don't silently `perform_task_action("")`."""
    mc = setup_dbs["mc_conn"]
    # upsert_card requires non-empty subject_id at the schema level, so
    # we synthesize the malformed shape by tampering after insert.
    card_id = _seed_card(mc, subject_kind="task", subject_id="task:to-be-cleared")
    mc.execute(
        "UPDATE mission_control_cards SET subject_id='' WHERE card_id=?",
        (card_id,),
    )
    mc.commit()

    with pytest.raises(HTTPException) as exc:
        _call(card_id)
    assert exc.value.status_code == 400


def test_complete_404_when_task_hub_row_missing(setup_dbs):
    """Card references a subject_id that has no task_hub row. The card
    might be stale from a recurrence wave whose underlying task was
    purged. Return 404 so the UI can show a useful error instead of
    completing a non-existent task."""
    mc = setup_dbs["mc_conn"]
    card_id = _seed_card(mc, subject_kind="task", subject_id="task:never-existed")

    with pytest.raises(HTTPException) as exc:
        _call(card_id)
    assert exc.value.status_code == 404


# ── Verification-gate bypass ─────────────────────────────────────────


def test_complete_overrides_delivery_verification_gate(setup_dbs):
    """When `perform_task_action(complete)` would park the task in
    needs_review because the email-delivery gate fired, the operator's
    explicit click must still close the task with an operator_override
    audit marker. This is the FULL fix the user requested — without it,
    the button would silently park the task instead of closing it."""
    th = setup_dbs["th_conn"]
    mc = setup_dbs["mc_conn"]
    # Seed an email-task that REQUIRES verified delivery. The gate at
    # task_hub.py:4583 inspects source_kind + outbound_delivery metadata.
    # An "email" source_kind with no email side-effects + no outbound
    # delivery is the canonical "gate would park this" shape.
    _seed_task(
        th,
        task_id="task:gated",
        source_kind="email",
        status=task_hub.TASK_STATUS_IN_PROGRESS,
        metadata={"dispatch": {"expected_channel": "email"}},
    )
    card_id = _seed_card(mc, subject_kind="task", subject_id="task:gated")

    result = _call(card_id)
    assert result["result"] == "completed"

    th2 = sqlite3.connect(str(setup_dbs["activity_path"]))
    th2.row_factory = sqlite3.Row
    item = task_hub.get_item(th2, "task:gated")
    th2.close()
    assert item["status"] == task_hub.TASK_STATUS_COMPLETED, (
        f"expected operator override to force completion; got "
        f"status={item['status']} (gate may have parked it)"
    )
    # Operator override metadata captured for audit.
    dispatch = item["metadata"]["dispatch"]
    override = dispatch.get("operator_override")
    assert override is not None, (
        "operator_override marker missing — audit trail incomplete"
    )
    assert override["via"] == "mission_control_card_button"
    assert override["card_id"] == card_id
    assert dispatch["last_disposition"] == "completed"
