from __future__ import annotations

import os
import uuid

import pytest

from universal_agent.services.todoist_service import TodoService


_RUN_LIVE = (os.getenv("RUN_TODOIST_LIVE_TESTS", "") or "").strip().lower() in {
    "1",
    "true",
    "yes",
}

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason="Set RUN_TODOIST_LIVE_TESTS=1 to run live Todoist integration tests.",
    ),
]


def _service() -> TodoService:
    token = (os.getenv("TODOIST_API_TOKEN") or os.getenv("TODOIST_API_KEY") or "").strip()
    if not token:
        pytest.skip("TODOIST_API_TOKEN or TODOIST_API_KEY is required for RUN_TODOIST_LIVE_TESTS")
    return TodoService(api_token=token)


def test_todoist_live_task_lifecycle_roundtrip() -> None:
    svc = _service()
    suffix = uuid.uuid4().hex[:8]
    task_id: str | None = None

    try:
        taxonomy = svc.ensure_taxonomy()
        assert taxonomy.get("agent_project_id")
        assert taxonomy.get("brainstorm_project_id")

        created = svc.create_task(
            content=f"[UA-LIVE] Todoist lifecycle smoke {suffix}",
            description="Live guarded integration test",
            section="background",
            priority="low",
            labels=["needs-review"],
            sub_agent="code",
        )
        task_id = str(created.get("id") or "")
        assert task_id
        assert "agent-ready" in (created.get("labels") or [])

        detail = svc.get_task_detail(task_id)
        assert detail is not None
        assert str(detail.get("id") or "") == task_id

        summary = svc.heartbeat_summary()
        assert "actionable_count" in summary
        assert isinstance(summary.get("tasks"), list)
        assert any(str(row.get("id") or "") == task_id for row in summary["tasks"])
    finally:
        if task_id:
            svc.delete_task(task_id)


def test_todoist_live_idea_dedupe_roundtrip() -> None:
    svc = _service()
    suffix = uuid.uuid4().hex[:8]
    dedupe_key = f"ua-live-idea-{suffix}"
    idea_id: str | None = None

    try:
        first = svc.record_idea(
            content=f"[UA-LIVE IDEA] Evaluate retry policy {suffix}",
            description="First capture",
            dedupe_key=dedupe_key,
            source_session_id="session_live_guarded",
        )
        second = svc.record_idea(
            content=f"[UA-LIVE IDEA] Evaluate retry policy {suffix}",
            description="Second capture",
            dedupe_key=dedupe_key,
            source_session_id="session_live_guarded",
        )

        idea_id = str(first.get("id") or "")
        assert idea_id
        assert idea_id == str(second.get("id") or "")

        detail = svc.get_task_detail(idea_id)
        assert detail is not None
        assert "confidence: 2" in str(detail.get("description") or "")

        pipeline = svc.get_pipeline_summary()
        assert "inbox" in pipeline
    finally:
        if idea_id:
            svc.delete_task(idea_id)
