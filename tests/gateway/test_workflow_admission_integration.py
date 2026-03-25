import json

import pytest

from universal_agent import gateway_server


def _insert_specialist_loop(topic_key: str, *, updated_at: str = "2026-03-24T12:00:00+00:00") -> None:
    conn = gateway_server._activity_connect()
    try:
        gateway_server._ensure_activity_schema(conn)
        conn.execute(
            """
            INSERT INTO csi_specialist_loops (
                topic_key, topic_label, status, confidence_target, confidence_score,
                follow_up_budget_total, follow_up_budget_remaining, events_count, source_mix_json,
                confidence_method, evidence_json,
                last_event_type, last_event_id, last_event_at, last_followup_requested_at,
                low_signal_streak, suppressed_until,
                created_at, updated_at, closed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic_key,
                "RSS Trend",
                "open",
                0.72,
                0.31,
                3,
                3,
                4,
                json.dumps({"rss": 4}),
                "heuristic",
                json.dumps({"signal_volume": 20}),
                "rss_trend_report",
                f"evt-{topic_key}",
                updated_at,
                None,
                0,
                None,
                updated_at,
                updated_at,
                None,
            ),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_csi_followup_dedupes_via_workflow_admission(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str((tmp_path / "runtime_state.db").resolve()))
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str((tmp_path / "activity_state.db").resolve()))

    class _HooksStub:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def dispatch_internal_action(self, action_payload: dict):
            self.calls.append(action_payload)
            return True, "agent"

    hooks = _HooksStub()
    monkeypatch.setattr(gateway_server, "_hooks_service", hooks)

    _insert_specialist_loop("rss:trend:admission")

    first = await gateway_server._csi_operator_request_followup(
        topic_key="rss:trend:admission",
        actor="tester",
        note="please follow up",
        trigger="operator_manual",
    )
    second = await gateway_server._csi_operator_request_followup(
        topic_key="rss:trend:admission",
        actor="tester",
        note="please follow up",
        trigger="operator_manual",
    )

    assert first["ok"] is True
    assert first["dispatched"] is True
    assert second["ok"] is True
    assert second["dispatched"] is False
    assert second["reason"] == "skip_duplicate"
    assert len(hooks.calls) == 1
