"""Phase 2 regression tests: Reporting & Visibility for the Autonomous Proactive Pipeline.

Covers:
  2A — ProactiveIntelligenceReport: data gathering, LLM reasoning, dual delivery
  2B — Utilization tracking: heartbeat occupancy sampling, queue depth
  2C — Cron scheduling: 3x daily report generation
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from universal_agent import proactive_signals, task_hub
from universal_agent.services.proactive_budget import (
    get_budget_remaining,
    get_daily_proactive_count,
    increment_daily_proactive_count,
)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_proactive_tasks(conn: sqlite3.Connection, count: int = 3, source_kind: str = "proactive_signal") -> list[str]:
    """Insert N proactive task hub items and return their IDs."""
    import uuid
    task_hub.ensure_schema(conn)
    task_ids = []
    for i in range(count):
        tid = f"test-{source_kind}-{uuid.uuid4().hex[:8]}"
        item = task_hub.upsert_item(conn, {
            "task_id": tid,
            "title": f"Proactive task {i+1}",
            "description": f"Proactive test task #{i+1}",
            "source_kind": source_kind,
            "source_ref": f"test-{source_kind}-{i+1}",
            "priority": 3,
            "status": "open",
        })
        task_ids.append(item["task_id"])
    return task_ids


def _seed_completed_proactive(conn: sqlite3.Connection, count: int = 2) -> list[str]:
    """Insert N completed proactive task hub items."""
    import uuid
    task_hub.ensure_schema(conn)
    task_ids = []
    for i in range(count):
        tid = f"test-completed-{uuid.uuid4().hex[:8]}"
        item = task_hub.upsert_item(conn, {
            "task_id": tid,
            "title": f"Completed proactive {i+1}",
            "description": f"Completed proactive test #{i+1}",
            "source_kind": "proactive_signal",
            "source_ref": f"test-completed-{i+1}",
            "priority": 3,
            "status": "completed",
        })
        task_ids.append(item["task_id"])
    return task_ids


def _seed_failed_proactive(conn: sqlite3.Connection, count: int = 1) -> list[str]:
    """Insert N parked (failed) proactive task hub items."""
    import uuid
    task_hub.ensure_schema(conn)
    task_ids = []
    for i in range(count):
        tid = f"test-failed-{uuid.uuid4().hex[:8]}"
        item = task_hub.upsert_item(conn, {
            "task_id": tid,
            "title": f"Failed proactive {i+1}",
            "description": f"Failed proactive test #{i+1}",
            "source_kind": "reflection",
            "source_ref": f"test-failed-{i+1}",
            "priority": 3,
            "status": "parked",
        })
        task_ids.append(item["task_id"])
    return task_ids


def _seed_signal_cards(conn: sqlite3.Connection, count: int = 5) -> list[str]:
    """Insert N pending signal cards."""
    proactive_signals.ensure_schema(conn)
    card_ids = []
    for i in range(count):
        card = proactive_signals.upsert_generated_card(conn, {
            "card_id": f"card-test-{i+1}",
            "source": "youtube",
            "card_type": "signal_card",
            "title": f"Test signal card {i+1}",
            "summary": f"Signal card summary #{i+1}",
            "priority": 3,
        })
        card_ids.append(card["card_id"])
    return card_ids


# =========================================================================
# 2A — ProactiveIntelligenceReport: Data Gathering
# =========================================================================

class TestReportDataGathering:
    """Report gathers accurate stats from Task Hub and Signal Cards."""

    def test_gather_stats_returns_proactive_task_counts(self, tmp_path):
        """Report includes counts of open, completed, and failed proactive tasks."""
        from universal_agent.services.proactive_intelligence_report import (
            gather_pipeline_stats,
        )

        db_path = tmp_path / "runtime_state.db"
        with _connect(db_path) as conn:
            _seed_proactive_tasks(conn, count=3, source_kind="proactive_signal")
            _seed_completed_proactive(conn, count=2)
            _seed_failed_proactive(conn, count=1)

            stats = gather_pipeline_stats(conn)

        assert stats["proactive_tasks"]["open"] == 3
        assert stats["proactive_tasks"]["completed"] == 2
        assert stats["proactive_tasks"]["failed"] == 1
        assert stats["proactive_tasks"]["total"] == 6

    def test_gather_stats_includes_budget_consumption(self, tmp_path):
        """Report includes daily budget used vs remaining."""
        from universal_agent.services.proactive_intelligence_report import (
            gather_pipeline_stats,
        )

        db_path = tmp_path / "runtime_state.db"
        with _connect(db_path) as conn:
            task_hub.ensure_schema(conn)
            increment_daily_proactive_count(conn, 4)

            stats = gather_pipeline_stats(conn)

        assert stats["budget"]["used"] == 4
        assert stats["budget"]["remaining"] >= 0
        assert stats["budget"]["daily_limit"] > 0

    def test_gather_stats_includes_signal_card_counts(self, tmp_path):
        """Report includes pending/promoted signal card counts."""
        from universal_agent.services.proactive_intelligence_report import (
            gather_pipeline_stats,
        )

        db_path = tmp_path / "runtime_state.db"
        with _connect(db_path) as conn:
            task_hub.ensure_schema(conn)
            _seed_signal_cards(conn, count=5)

            stats = gather_pipeline_stats(conn)

        assert stats["signal_cards"]["pending"] >= 5

    def test_gather_stats_counts_promoted_as_actioned_tracking_approved(self, tmp_path):
        """T19a: 'promoted' is a legacy status no current writer produces (see
        proactive_signals.py's writer inventory) -- counting status='promoted'
        directly always read ~0. The stat must count cards the operator
        actually acted on positively: actioned, tracking, or approved."""
        from universal_agent.services.proactive_intelligence_report import (
            gather_pipeline_stats,
        )

        db_path = tmp_path / "runtime_state.db"
        with _connect(db_path) as conn:
            proactive_signals.ensure_schema(conn)
            for i, status in enumerate(("actioned", "tracking", "approved", "pending", "rejected")):
                proactive_signals.upsert_generated_card(conn, {
                    "card_id": f"card-status-{i}",
                    "source": "youtube",
                    "card_type": "signal_card",
                    "title": f"Card {i}",
                    "summary": "x",
                    "priority": 3,
                })
                conn.execute(
                    "UPDATE proactive_signal_cards SET status = ? WHERE card_id = ?",
                    (status, f"card-status-{i}"),
                )
            conn.commit()

            stats = gather_pipeline_stats(conn)

        # actioned + tracking + approved = 3; pending and rejected excluded.
        assert stats["signal_cards"]["promoted"] == 3
        assert stats["signal_cards"]["pending"] == 1

    def test_gather_stats_promoted_excludes_legacy_promoted_status(self, tmp_path):
        """A literal legacy status='promoted' row (predating the rename) must
        NOT be double-counted alongside the new actioned/tracking/approved
        set -- it belongs to neither the old nor new active vocabulary."""
        from universal_agent.services.proactive_intelligence_report import (
            gather_pipeline_stats,
        )

        db_path = tmp_path / "runtime_state.db"
        with _connect(db_path) as conn:
            proactive_signals.ensure_schema(conn)
            proactive_signals.upsert_generated_card(conn, {
                "card_id": "legacy-promoted",
                "source": "youtube",
                "card_type": "signal_card",
                "title": "Legacy relic",
                "summary": "x",
                "priority": 3,
            })
            conn.execute(
                "UPDATE proactive_signal_cards SET status = 'promoted' WHERE card_id = 'legacy-promoted'"
            )
            conn.commit()

            stats = gather_pipeline_stats(conn)

        assert stats["signal_cards"]["promoted"] == 0

    def test_gather_stats_includes_source_kind_breakdown(self, tmp_path):
        """Report breaks down tasks by source_kind."""
        from universal_agent.services.proactive_intelligence_report import (
            gather_pipeline_stats,
        )

        db_path = tmp_path / "runtime_state.db"
        with _connect(db_path) as conn:
            _seed_proactive_tasks(conn, count=2, source_kind="proactive_signal")
            _seed_proactive_tasks(conn, count=1, source_kind="reflection")

            stats = gather_pipeline_stats(conn)

        breakdown = stats["proactive_tasks"]["by_source"]
        assert breakdown.get("proactive_signal", 0) >= 2
        assert breakdown.get("reflection", 0) >= 1

    def test_gather_stats_includes_report_timestamp_and_period(self, tmp_path):
        """Report carries timestamp and period identifier."""
        from universal_agent.services.proactive_intelligence_report import (
            gather_pipeline_stats,
        )

        db_path = tmp_path / "runtime_state.db"
        with _connect(db_path) as conn:
            task_hub.ensure_schema(conn)
            stats = gather_pipeline_stats(conn)

        assert "timestamp" in stats
        assert "period" in stats


# =========================================================================
# 2A — ProactiveIntelligenceReport: LLM Reasoning Pass
# =========================================================================

class TestReportLLMReasoning:
    """The report includes a non-empty LLM-generated analysis section."""

    @pytest.mark.asyncio
    async def test_compose_report_includes_llm_analysis(self, tmp_path):
        """Report contains an 'analysis' section generated by LLM reasoning."""
        from universal_agent.services.proactive_intelligence_report import (
            compose_intelligence_report,
        )

        db_path = tmp_path / "runtime_state.db"
        with _connect(db_path) as conn:
            task_hub.ensure_schema(conn)
            _seed_proactive_tasks(conn, count=2)
            _seed_completed_proactive(conn, count=1)

            # Mock the LLM call to return a deterministic analysis
            mock_analysis = "The proactive pipeline is performing well. Recommend increasing budget allocation for signal cards."
            with patch(
                "universal_agent.services.proactive_intelligence_report._call_reasoning_llm",
                new_callable=AsyncMock,
                return_value=mock_analysis,
            ):
                report = await compose_intelligence_report(conn, period="morning")

        assert report["analysis"]
        assert len(report["analysis"]) > 20
        assert "proactive" in report["analysis"].lower() or "pipeline" in report["analysis"].lower()

    @pytest.mark.asyncio
    async def test_compose_report_includes_stats_and_analysis(self, tmp_path):
        """Report contains both deterministic stats AND LLM analysis."""
        from universal_agent.services.proactive_intelligence_report import (
            compose_intelligence_report,
        )

        db_path = tmp_path / "runtime_state.db"
        with _connect(db_path) as conn:
            task_hub.ensure_schema(conn)
            _seed_proactive_tasks(conn, count=1)

            with patch(
                "universal_agent.services.proactive_intelligence_report._call_reasoning_llm",
                new_callable=AsyncMock,
                return_value="All systems nominal. Consider exploring new signal sources.",
            ):
                report = await compose_intelligence_report(conn, period="noon")

        assert "stats" in report
        assert "analysis" in report
        assert "period" in report
        assert report["period"] == "noon"


# =========================================================================
# 2A — ProactiveIntelligenceReport: Dual Delivery
# =========================================================================

class TestReportDualDelivery:
    """Report is delivered both as email and stored for dashboard."""

    @pytest.mark.asyncio
    async def test_deliver_report_sends_email(self, tmp_path):
        """Report delivery sends formatted email via AgentMail."""
        from universal_agent.services.proactive_intelligence_report import (
            deliver_intelligence_report,
        )

        db_path = tmp_path / "runtime_state.db"
        mail_service = AsyncMock()
        mail_service.send_email.return_value = {"status": "sent", "message_id": "msg-rpt-1", "thread_id": "thread-rpt-1"}

        with _connect(db_path) as conn:
            task_hub.ensure_schema(conn)

            report = {
                "stats": {"proactive_tasks": {"open": 1, "completed": 2, "failed": 0, "total": 3}},
                "analysis": "Pipeline running smoothly. Budget utilization at 40%.",
                "period": "morning",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            with patch(
                "universal_agent.services.proactive_intelligence_report.compose_intelligence_report",
                new_callable=AsyncMock,
                return_value=report,
            ):
                result = await deliver_intelligence_report(
                    conn=conn,
                    mail_service=mail_service,
                    recipient="kevinjdragan@gmail.com",
                    period="morning",
                )

        assert mail_service.send_email.called
        call_kwargs = mail_service.send_email.call_args[1]
        assert "[UA Proactive]" in call_kwargs.get("subject", "")
        assert result.get("email_sent")

    @pytest.mark.asyncio
    async def test_deliver_report_stores_for_dashboard(self, tmp_path):
        """Report is persisted to the DB for dashboard retrieval."""
        from universal_agent.services.proactive_intelligence_report import (
            deliver_intelligence_report,
        )

        db_path = tmp_path / "runtime_state.db"
        mail_service = AsyncMock()
        mail_service.send_email.return_value = {"status": "sent", "message_id": "msg-rpt-2", "thread_id": "thread-rpt-2"}

        with _connect(db_path) as conn:
            task_hub.ensure_schema(conn)

            report = {
                "stats": {"proactive_tasks": {"open": 0, "completed": 1, "failed": 0, "total": 1}},
                "analysis": "Light activity day. System under-utilized.",
                "period": "afternoon",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            with patch(
                "universal_agent.services.proactive_intelligence_report.compose_intelligence_report",
                new_callable=AsyncMock,
                return_value=report,
            ):
                result = await deliver_intelligence_report(
                    conn=conn,
                    mail_service=mail_service,
                    recipient="kevinjdragan@gmail.com",
                    period="afternoon",
                )

        assert result.get("stored_for_dashboard")
        assert result.get("report_id")


# =========================================================================
# 2B — System Utilization Tracking
# =========================================================================

class TestUtilizationTracking:
    """Heartbeat-sampled utilization metrics."""

    def test_record_utilization_sample(self, tmp_path):
        """Utilization sample records slot occupancy and queue depth."""
        from universal_agent.services.proactive_intelligence_report import (
            get_utilization_stats,
            record_utilization_sample,
        )

        db_path = tmp_path / "runtime_state.db"
        with _connect(db_path) as conn:
            task_hub.ensure_schema(conn)

            record_utilization_sample(conn, active_slots=1, max_slots=2, queue_depth=5)
            record_utilization_sample(conn, active_slots=2, max_slots=2, queue_depth=3)
            record_utilization_sample(conn, active_slots=0, max_slots=2, queue_depth=0)

            stats = get_utilization_stats(conn, window_hours=24)

        assert stats["sample_count"] == 3
        assert 0 <= stats["avg_occupancy_pct"] <= 100
        assert stats["peak_occupancy_slots"] == 2
        assert stats["avg_queue_depth"] >= 0

    def test_utilization_stats_returns_empty_for_no_data(self, tmp_path):
        """Gracefully returns zero stats when no samples exist."""
        from universal_agent.services.proactive_intelligence_report import (
            get_utilization_stats,
        )

        db_path = tmp_path / "runtime_state.db"
        with _connect(db_path) as conn:
            task_hub.ensure_schema(conn)
            stats = get_utilization_stats(conn, window_hours=24)

        assert stats["sample_count"] == 0
        assert stats["avg_occupancy_pct"] == 0
        assert stats["peak_occupancy_slots"] == 0

    def test_active_slots_counts_running_vp_missions_and_in_progress_tasks(
        self, tmp_path, monkeypatch
    ):
        """T17: active_slots must reflect REAL running state, not
        CapacityGovernor.snapshot().active_slots — that counter is
        permanently 0 in production because nothing calls
        CapacityGovernor.acquire_slot(). count_active_agent_slots() sources
        it from running vp_missions + in-progress task_hub_items instead."""
        from universal_agent.durable.migrations import ensure_schema as ensure_vp_schema
        from universal_agent.services.proactive_activity_report import (
            count_active_agent_slots,
        )

        vp_db_path = tmp_path / "vp_state.db"
        vp_conn = sqlite3.connect(str(vp_db_path))
        vp_conn.row_factory = sqlite3.Row
        ensure_vp_schema(vp_conn)
        now = datetime.now(timezone.utc).isoformat()
        vp_conn.execute(
            """
            INSERT INTO vp_missions
              (mission_id, vp_id, status, mission_type, objective, priority_tier,
               created_at, updated_at)
            VALUES ('t17-running-mission', 'vp.coder.primary', 'running', 'task',
                    'test objective', 'background', ?, ?)
            """,
            (now, now),
        )
        vp_conn.commit()
        vp_conn.close()
        monkeypatch.setenv("UA_VP_DB_PATH", str(vp_db_path))

        activity_db_path = tmp_path / "activity_state.db"
        with _connect(activity_db_path) as conn:
            task_hub.ensure_schema(conn)
            task_hub.upsert_item(conn, {
                "task_id": "t17-in-progress-task",
                "title": "In-progress task",
                "description": "T17 regression fixture",
                "source_kind": "proactive_signal",
                "source_ref": "t17-in-progress-task",
                "priority": 3,
                "status": task_hub.TASK_STATUS_IN_PROGRESS,
            })

            active_slots = count_active_agent_slots(conn)

        # 1 running VP mission + 1 in-progress Task Hub item.
        assert active_slots == 2

    def test_active_slots_is_zero_when_nothing_is_running(self, tmp_path, monkeypatch):
        """No running VP missions and no in-progress Task Hub items → 0,
        not a fabricated placeholder."""
        from universal_agent.durable.migrations import ensure_schema as ensure_vp_schema
        from universal_agent.services.proactive_activity_report import (
            count_active_agent_slots,
        )

        vp_db_path = tmp_path / "vp_state.db"
        vp_conn = sqlite3.connect(str(vp_db_path))
        ensure_vp_schema(vp_conn)
        vp_conn.close()
        monkeypatch.setenv("UA_VP_DB_PATH", str(vp_db_path))

        activity_db_path = tmp_path / "activity_state.db"
        with _connect(activity_db_path) as conn:
            task_hub.ensure_schema(conn)
            # Only an OPEN task — must not be counted as active.
            task_hub.upsert_item(conn, {
                "task_id": "t17-open-task",
                "title": "Open task",
                "description": "T17 regression fixture",
                "source_kind": "proactive_signal",
                "source_ref": "t17-open-task",
                "priority": 3,
                "status": task_hub.TASK_STATUS_OPEN,
            })

            active_slots = count_active_agent_slots(conn)

        assert active_slots == 0


# =========================================================================
# 2A — Report Composition (Email Format)
# =========================================================================

class TestReportEmailFormat:
    """The email body includes structured, readable content."""

    @pytest.mark.asyncio
    async def test_report_email_includes_key_sections(self, tmp_path):
        """Email body includes stats summary, analysis, and feedback prompt."""
        from universal_agent.services.proactive_intelligence_report import (
            format_report_email,
        )

        report = {
            "stats": {
                "proactive_tasks": {"open": 3, "completed": 5, "failed": 1, "total": 9, "by_source": {"proactive_signal": 6, "reflection": 3}},
                "budget": {"used": 7, "remaining": 3, "daily_limit": 10},
                "signal_cards": {"pending": 12, "promoted": 6},
                "utilization": {"avg_occupancy_pct": 45, "peak_occupancy_slots": 2, "avg_queue_depth": 3, "sample_count": 50},
            },
            "analysis": "The pipeline had a productive morning. Signal curator promoted 6 cards. Recommend focusing afternoon on reflection-originated tasks.",
            "period": "morning",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        subject, text_body, html_body = format_report_email(report)

        assert "[UA Proactive]" in subject
        assert "morning" in subject.lower() or "Morning" in subject
        assert "Budget" in text_body or "budget" in text_body
        assert "analysis" in text_body.lower() or report["analysis"][:30] in text_body
        assert "<html>" in html_body
