from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from universal_agent import proactive_signals, task_hub
from universal_agent.services.email_task_bridge import EmailTaskBridge
from universal_agent.services.intelligence_reporter import IntelligenceReporter
from universal_agent.services.proactive_artifacts import (
    ARTIFACT_STATUS_ACCEPTED,
    DELIVERY_EMAILED,
    DELIVERY_REVIEWED,
    get_artifact,
    make_artifact_id,
    record_email_delivery,
    record_feedback,
    sync_from_proactive_signal_cards,
    update_artifact_state,
    upsert_artifact,
)
from universal_agent.services.proactive_feedback import parse_feedback_text
from universal_agent.services.proactive_preferences import (
    build_weekly_preference_report,
    get_delegation_context,
    get_preference_snapshot,
    record_artifact_feedback_signal,
)
from universal_agent.services.proactive_work_recap import ensure_schema as ensure_recap_schema


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


def test_daily_digest_includes_completed_proactive_task_recap_and_audit_link(tmp_path, monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://app.clearspringcg.com")
    db_path = tmp_path / "activity_state.db"
    workspace = tmp_path / "workspace"
    (workspace / "work_products").mkdir(parents=True)
    (workspace / "work_products" / "brief.md").write_text("brief", encoding="utf-8")
    with _connect(db_path) as conn:
        ensure_recap_schema(conn)
        task = task_hub.upsert_item(
            conn,
            {
                "task_id": "digest-proactive-task",
                "source_kind": "proactive_codie",
                "title": "Digest proactive task",
                "description": "Completed proactive work for digest.",
                "project_key": "proactive",
                "status": task_hub.TASK_STATUS_IN_PROGRESS,
                "priority": 4,
            },
        )
        conn.execute(
            """
            INSERT INTO task_hub_assignments (
                assignment_id, task_id, agent_id, provider_session_id,
                workspace_dir, state, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "asg-digest-proactive",
                task["task_id"],
                "vp.general.primary",
                "session-digest-proactive",
                str(workspace),
                "running",
                "2026-04-29T14:00:00+00:00",
            ),
        )
        conn.commit()
        task_hub.perform_task_action(
            conn,
            task_id=task["task_id"],
            action="complete",
            reason="Completed digest-worthy proactive work.",
            agent_id="vp.general.primary",
        )

        digest = IntelligenceReporter(conn).compose_daily_digest(
            recipient="kevinjdragan@gmail.com",
            limit=5,
        )

    assert "Digest proactive task" in digest.text
    assert "Assessment:" in digest.text
    assert "Audit: https://app.clearspringcg.com/dashboard/proactive-task-history?task_id=digest-proactive-task" in digest.text
    assert "proactive review candidates" in digest.subject.lower()


def test_digest_task_sync_preserves_existing_review_state(tmp_path, monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://app.clearspringcg.com")
    db_path = tmp_path / "activity_state.db"
    with _connect(db_path) as conn:
        task = task_hub.upsert_item(
            conn,
            {
                "task_id": "reviewed-proactive-task",
                "source_kind": "proactive_codie",
                "title": "Reviewed proactive task",
                "description": "Already reviewed proactive work.",
                "project_key": "proactive",
                "status": task_hub.TASK_STATUS_COMPLETED,
                "priority": 3,
            },
        )
        artifact_id = make_artifact_id(
            source_kind="proactive_codie",
            source_ref=task["task_id"],
            artifact_type="proactive_work_item",
            title="Reviewed proactive task",
        )

        IntelligenceReporter(conn).compose_daily_digest(recipient="kevinjdragan@gmail.com", limit=5)
        update_artifact_state(
            conn,
            artifact_id=artifact_id,
            status=ARTIFACT_STATUS_ACCEPTED,
            delivery_state=DELIVERY_REVIEWED,
        )
        IntelligenceReporter(conn).compose_daily_digest(recipient="kevinjdragan@gmail.com", limit=5)
        artifact = get_artifact(conn, artifact_id)

    assert artifact is not None
    assert artifact["status"] == ARTIFACT_STATUS_ACCEPTED
    assert artifact["delivery_state"] == DELIVERY_REVIEWED


def test_review_email_includes_task_recap_context_for_task_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://app.clearspringcg.com")
    db_path = tmp_path / "activity_state.db"
    with _connect(db_path) as conn:
        ensure_recap_schema(conn)
        task = task_hub.upsert_item(
            conn,
            {
                "task_id": "review-email-task",
                "source_kind": "proactive_codie",
                "title": "Review email task",
                "description": "Completed proactive task.",
                "project_key": "proactive",
                "status": task_hub.TASK_STATUS_COMPLETED,
            },
        )
        conn.execute(
            """
            INSERT INTO proactive_work_recaps (
                recap_id, task_id, assignment_id, session_id, workspace_dir,
                source_kind, evaluation_status, idea, implemented, known_issues,
                success_assessment, recommended_next_action, confidence,
                raw_model_output_json, created_at, updated_at
            ) VALUES (?, ?, '', '', '', ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
            """,
            (
                "pwr-review-email",
                task["task_id"],
                "proactive_codie",
                "llm_evaluated",
                "Review email idea",
                "Implemented useful proactive work.",
                "",
                "Successful and ready for review.",
                "Review the artifact.",
                0.91,
                "2026-04-29T15:00:00+00:00",
                "2026-04-29T15:00:00+00:00",
            ),
        )
        artifact = upsert_artifact(
            conn,
            artifact_type="proactive_work_item",
            source_kind="proactive_codie",
            source_ref=task["task_id"],
            title="Review email task",
            summary="Task artifact summary.",
            metadata={"task_id": task["task_id"]},
        )
        payload = IntelligenceReporter(conn).compose_review_email(
            artifact_id=artifact["artifact_id"],
            recipient="kevinjdragan@gmail.com",
        )

    assert "Task audit:" in payload.text
    assert "Implemented useful proactive work." in payload.text
    assert "Proactive history: https://app.clearspringcg.com/dashboard/proactive-task-history?task_id=review-email-task" in payload.text


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
