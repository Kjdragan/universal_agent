from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from universal_agent import task_hub
from universal_agent import proactive_signals
from universal_agent.services.email_task_bridge import EmailTaskBridge
from universal_agent.services.intelligence_reporter import IntelligenceReporter
from universal_agent.services.proactive_artifacts import (
    ARTIFACT_STATUS_ACCEPTED,
    DELIVERY_EMAILED,
    DELIVERY_REVIEWED,
    get_artifact,
    record_feedback,
    record_email_delivery,
    sync_from_proactive_signal_cards,
    upsert_artifact,
)
from universal_agent.services.proactive_feedback import parse_feedback_text
from universal_agent.services.proactive_preferences import record_artifact_feedback_signal
from universal_agent.services.proactive_preferences import (
    build_weekly_preference_report,
    get_delegation_context,
    get_preference_snapshot,
)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


class _DummyMailService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send_email(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "sent", "message_id": "msg-review-1", "thread_id": "thread-review-1"}


def test_proactive_artifact_registry_records_review_delivery(tmp_path):
    db_path = tmp_path / "activity_state.db"
    with _connect(db_path) as conn:
        artifact = upsert_artifact(
            conn,
            artifact_type="signal_brief",
            source_kind="csi",
            source_ref="video-123",
            title="MCP convergence brief",
            summary="Three channels covered the same MCP pattern.",
            artifact_uri="https://example.test/artifact",
            topic_tags=["mcp", "agents"],
        )
        updated = record_email_delivery(
            conn,
            artifact_id=artifact["artifact_id"],
            message_id="msg-review-1",
            thread_id="thread-review-1",
            subject=f"[Simone Review] MCP convergence brief [{artifact['artifact_id']}]",
            recipient="kevinjdragan@gmail.com",
        )

    assert updated["delivery_state"] == DELIVERY_EMAILED
    assert updated["status"] == "surfaced"


def test_parse_feedback_text_handles_numbered_and_freeform_replies():
    numbered = parse_feedback_text("3 wrong topic for me right now")
    freeform = parse_feedback_text("This needs more code examples next time.")

    assert numbered.score == 3
    assert numbered.text == "wrong topic for me right now"
    assert freeform.score is None
    assert "code examples" in freeform.text


def test_email_task_bridge_intercepts_proactive_feedback_without_task_creation(tmp_path):
    db_path = tmp_path / "activity_state.db"
    with _connect(db_path) as conn:
        task_hub.ensure_schema(conn)
        artifact = upsert_artifact(
            conn,
            artifact_type="signal_brief",
            source_kind="csi",
            source_ref="video-123",
            title="MCP convergence brief",
            topic_tags=["mcp"],
        )
        record_email_delivery(
            conn,
            artifact_id=artifact["artifact_id"],
            message_id="msg-review-1",
            thread_id="thread-review-1",
            subject=f"[Simone Review] MCP convergence brief [{artifact['artifact_id']}]",
            recipient="kevinjdragan@gmail.com",
        )
        bridge = EmailTaskBridge(db_conn=conn)
        result = bridge.materialize(
            thread_id="thread-review-1",
            message_id="msg-feedback-1",
            real_thread_id="thread-review-1",
            real_message_id="msg-feedback-1",
            sender_email="kevinjdragan@gmail.com",
            subject=f"Re: [Simone Review] MCP convergence brief [{artifact['artifact_id']}]",
            reply_text="1 useful, more like this",
            session_key="agentmail_thread_review",
            sender_trusted=True,
        )
        task_rows = conn.execute("SELECT COUNT(*) AS count FROM task_hub_items").fetchone()
        mapping_rows = conn.execute("SELECT COUNT(*) AS count FROM email_task_mappings").fetchone()
        preference_rows = conn.execute("SELECT COUNT(*) AS count FROM proactive_preference_signals").fetchone()
        updated = get_artifact(conn, artifact["artifact_id"])

    assert result["handled_as"] == "proactive_feedback"
    assert result["task_id"] == ""
    assert task_rows["count"] == 0
    assert mapping_rows["count"] == 0
    assert preference_rows["count"] > 0
    assert updated is not None
    assert updated["status"] == ARTIFACT_STATUS_ACCEPTED
    assert updated["delivery_state"] == DELIVERY_REVIEWED
    assert updated["feedback"]["last_score"] == 1


def test_email_task_bridge_leaves_normal_email_flow_unchanged(tmp_path):
    db_path = tmp_path / "activity_state.db"
    with _connect(db_path) as conn:
        task_hub.ensure_schema(conn)
        bridge = EmailTaskBridge(db_conn=conn)
        result = bridge.materialize(
            thread_id="thread-normal",
            message_id="msg-normal",
            sender_email="kevinjdragan@gmail.com",
            subject="Please research this",
            reply_text="Please research this and email me a report.",
            session_key="agentmail_thread_normal",
            sender_trusted=True,
        )
        item = task_hub.get_item(conn, result["task_id"])

    assert result["task_id"].startswith("email:")
    assert item is not None
    assert item["source_kind"] == "email"


@pytest.mark.asyncio
async def test_intelligence_reporter_sends_review_email_and_records_mapping(tmp_path):
    db_path = tmp_path / "activity_state.db"
    mail = _DummyMailService()
    with _connect(db_path) as conn:
        artifact = upsert_artifact(
            conn,
            artifact_type="tutorial_build",
            source_kind="youtube",
            source_ref="video-456",
            title="Private MCP demo repo",
            summary="CODIE built a runnable demo from the tutorial.",
            artifact_uri="https://github.com/Kjdragan/private-demo",
            topic_tags=["mcp", "tutorial"],
        )
        reporter = IntelligenceReporter(conn)
        result = await reporter.send_review_email(
            artifact_id=artifact["artifact_id"],
            recipient="kevinjdragan@gmail.com",
            mail_service=mail,
        )
        updated = get_artifact(conn, artifact["artifact_id"])

    assert result["message_id"] == "msg-review-1"
    assert mail.calls
    assert "[UA Build Review]" in mail.calls[0]["subject"]
    assert "Quick feedback" in mail.calls[0]["text"]
    assert updated is not None
    assert updated["delivery_state"] == DELIVERY_EMAILED


def test_daily_digest_ranks_candidates_with_preference_feedback(tmp_path):
    db_path = tmp_path / "activity_state.db"
    with _connect(db_path) as conn:
        first = upsert_artifact(
            conn,
            artifact_type="signal_brief",
            source_kind="csi",
            source_ref="video-a",
            title="Generic model news",
            summary="A normal model-release summary.",
            priority=2,
            topic_tags=["model-news"],
        )
        second = upsert_artifact(
            conn,
            artifact_type="signal_brief",
            source_kind="csi",
            source_ref="video-b",
            title="MCP implementation pattern",
            summary="A concrete MCP implementation pattern.",
            priority=2,
            topic_tags=["mcp"],
        )
        updated = record_feedback(
            conn,
            artifact_id=second["artifact_id"],
            score=5,
            text="more like this",
        )
        record_artifact_feedback_signal(conn, artifact=updated, score=5, text="more like this")

        digest = IntelligenceReporter(conn).compose_daily_digest(
            recipient="kevinjdragan@gmail.com",
            limit=2,
        )

    assert "1. MCP implementation pattern" in digest.text
    assert digest.text.index("MCP implementation pattern") < digest.text.index("Generic model news")
    assert first["artifact_id"] in digest.text or "Generic model news" in digest.text


def test_preference_snapshot_and_delegation_context_update_from_feedback(tmp_path):
    db_path = tmp_path / "activity_state.db"
    with _connect(db_path) as conn:
        artifact = upsert_artifact(
            conn,
            artifact_type="convergence_brief",
            source_kind="convergence_detection",
            source_ref="conv-1",
            title="MCP convergence",
            topic_tags=["mcp", "convergence"],
        )
        updated = record_feedback(conn, artifact_id=artifact["artifact_id"], score=5, text="more like this")
        record_artifact_feedback_signal(conn, artifact=updated, score=5, text="more like this")
        snapshot = get_preference_snapshot(conn)
        context = get_delegation_context(conn, task_type="convergence_brief", topic_tags=["mcp"])

    assert snapshot["meta"]["total_signals_processed"] >= 1
    assert "topic:mcp" in snapshot["topic_preferences"]
    assert "Kevin's preference context" in context
    assert "topic:mcp" in context


def test_weekly_preference_report_and_email_payload(tmp_path):
    db_path = tmp_path / "activity_state.db"
    with _connect(db_path) as conn:
        artifact = upsert_artifact(
            conn,
            artifact_type="tutorial_build",
            source_kind="tutorial_build",
            source_ref="video-1",
            title="Tutorial preference seed",
            topic_tags=["tutorial"],
        )
        updated = record_feedback(conn, artifact_id=artifact["artifact_id"], score=5, text="more tutorials")
        record_artifact_feedback_signal(conn, artifact=updated, score=5, text="more tutorials")
        report = build_weekly_preference_report(conn)
        payload = IntelligenceReporter(conn).compose_weekly_preference_report(
            recipient="kevinjdragan@gmail.com",
        )

    assert "Weekly preference model update" in report["report_text"]
    assert "topic:tutorial" in report["report_text"]
    assert "[UA Weekly]" in payload.subject
    assert "Silence is treated as neutral" in payload.text


def test_existing_proactive_signal_cards_sync_into_artifact_inventory(tmp_path):
    db_path = tmp_path / "activity_state.db"
    with _connect(db_path) as conn:
        proactive_signals.upsert_generated_card(
            conn,
            {
                "card_id": "card-1",
                "source": "youtube",
                "card_type": "signal_card",
                "title": "Interesting agent release",
                "summary": "A release worth reviewing.",
                "priority": 3,
                "evidence": [{"url": "https://example.test/video"}],
            },
        )

        result = sync_from_proactive_signal_cards(conn)
        digest = IntelligenceReporter(conn).compose_daily_digest(
            recipient="kevinjdragan@gmail.com",
            limit=5,
        )

    assert result["seen"] == 1
    assert result["upserted"] == 1
    assert "Interesting agent release" in digest.text


def test_daily_digest_can_include_calendar_context(tmp_path):
    db_path = tmp_path / "activity_state.db"
    with _connect(db_path) as conn:
        digest = IntelligenceReporter(conn).compose_daily_digest(
            recipient="kevinjdragan@gmail.com",
            calendar_events=[
                {"start": "2026-04-15T09:00:00-05:00", "summary": "Planning review"},
            ],
        )

    assert "Calendar context:" in digest.text
    assert "Planning review" in digest.text


def test_proactive_signal_card_upsert_immediately_creates_artifact(tmp_path):
    db_path = tmp_path / "activity_state.db"
    with _connect(db_path) as conn:
        card = proactive_signals.upsert_generated_card(
            conn,
            {
                "card_id": "card-immediate",
                "source": "youtube",
                "card_type": "signal_card",
                "title": "Immediate artifact candidate",
                "summary": "This should enter artifact inventory immediately.",
                "priority": 3,
                "evidence": [{"url": "https://example.test/immediate"}],
            },
        )
        artifacts = [item for item in IntelligenceReporter(conn)._rank_digest_artifacts(limit=20) if item["source_ref"] == card["card_id"]]

    assert artifacts
    assert artifacts[0]["title"] == "Immediate artifact candidate"
