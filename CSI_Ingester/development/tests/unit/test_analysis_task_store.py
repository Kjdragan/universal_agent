from __future__ import annotations

from csi_ingester.store import analysis_tasks
from csi_ingester.store.sqlite import connect, ensure_schema


def test_analysis_task_lifecycle(tmp_path):
    conn = connect(tmp_path / "csi.db")
    ensure_schema(conn)

    created = analysis_tasks.create_task(
        conn,
        request_type="category_deep_dive",
        payload={"category": "ai", "lookback_hours": 48},
        priority=75,
        request_source="ua",
    )
    assert created["status"] == "pending"
    assert created["request_type"] == "category_deep_dive"
    assert created["payload"]["category"] == "ai"

    pending = analysis_tasks.list_tasks(conn, status="pending", limit=10)
    assert len(pending) == 1
    assert pending[0]["task_id"] == created["task_id"]

    claim_token = "claim_test_1"
    claimed = analysis_tasks.claim_next_task(conn, claim_token=claim_token)
    assert claimed is not None
    assert claimed["task_id"] == created["task_id"]
    assert claimed["status"] == "running"
    assert claimed["attempts"] == 1

    ok = analysis_tasks.complete_task(
        conn,
        task_id=created["task_id"],
        claim_token=claim_token,
        result={"totals": {"rows": 12}, "markdown": "done"},
    )
    assert ok is True
    completed = analysis_tasks.get_task(conn, created["task_id"])
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["result"]["totals"]["rows"] == 12


def test_analysis_task_cancel_pending(tmp_path):
    conn = connect(tmp_path / "csi.db")
    ensure_schema(conn)

    created = analysis_tasks.create_task(
        conn,
        request_type="trend_followup",
        payload={"lookback_hours": 24},
    )
    canceled = analysis_tasks.cancel_task(conn, task_id=created["task_id"], reason="operator request")
    assert canceled is not None
    assert canceled["status"] == "canceled"
    assert "operator request" in canceled["error_text"]
