"""Unit tests for the autonomous execution framework in todoist_service.

Covers: human-only labels, escalation loop (escalate → resolve → memory),
filter exclusion of human-only and escalated tasks, and the escalation
memory check.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


@pytest.fixture
def db_conn(tmp_path) -> sqlite3.Connection:
    """SQLite connection with Row factory for escalation tests."""
    conn = sqlite3.connect(str(tmp_path / "test_escalations.db"))
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def fake_api():
    """Fake Todoist API (matches test_todoist_service.py pattern)."""
    from dataclasses import dataclass, field

    @dataclass
    class FakeLabel:
        id: str
        name: str

    @dataclass
    class FakeProject:
        id: str
        name: str

    @dataclass
    class FakeSection:
        id: str
        name: str
        project_id: str

    @dataclass
    class FakeTask:
        id: str
        content: str
        description: str = ""
        priority: int = 1
        project_id: str = ""
        section_id: str | None = None
        parent_id: str | None = None
        labels: list[str] = field(default_factory=list)
        due: None = None
        url: str = ""
        created_at: str = ""
        comment_count: int = 0

    class FakeTodoistAPI:
        def __init__(self):
            self.projects: list = []
            self.sections: list = []
            self.labels: list = []
            self.tasks: dict = {}
            self.comments: dict = {}

        def get_projects(self):
            return list(self.projects)

        def add_project(self, name):
            proj = FakeProject(id=f"proj_{len(self.projects)+1}", name=name)
            self.projects.append(proj)
            return proj

        def get_sections(self, project_id):
            return [s for s in self.sections if s.project_id == project_id]

        def add_section(self, name, project_id):
            sec = FakeSection(id=f"sec_{len(self.sections)+1}", name=name, project_id=project_id)
            self.sections.append(sec)
            return sec

        def get_labels(self):
            return list(self.labels)

        def add_label(self, name):
            lbl = FakeLabel(id=f"lbl_{len(self.labels)+1}", name=name)
            self.labels.append(lbl)
            return lbl

        def get_tasks(self, **kwargs):
            return list(self.tasks.values())

        def add_task(self, **kwargs):
            task_id = f"task_{len(self.tasks)+1}"
            task = FakeTask(
                id=task_id,
                content=kwargs.get("content") or "",
                description=kwargs.get("description") or "",
                priority=int(kwargs.get("priority") or 1),
                project_id=str(kwargs.get("project_id") or ""),
                section_id=kwargs.get("section_id"),
                parent_id=kwargs.get("parent_id"),
                labels=list(kwargs.get("labels") or []),
            )
            self.tasks[task_id] = task
            return task

        def get_task(self, task_id):
            return self.tasks[task_id]

        def update_task(self, task_id, **kwargs):
            task = self.tasks[task_id]
            for key, value in kwargs.items():
                setattr(task, key, value)

        def close_task(self, task_id):
            self.tasks.pop(task_id, None)

        def add_comment(self, task_id, content):
            self.comments.setdefault(task_id, []).append(
                {"id": f"c_{len(self.comments.get(task_id, []))+1}", "content": content}
            )

    return FakeTodoistAPI()


@pytest.fixture
def todoist_service(fake_api):
    """TodoService with fake API."""
    from universal_agent.services.todoist_service import TodoService

    svc = TodoService(api_token="test", api=fake_api)
    svc.ensure_taxonomy()
    return svc


# ── Local Filter Tests ────────────────────────────────────────────────────


class TestLocalFilterExclusion:
    def test_excludes_human_only_tasks(self):
        from universal_agent.services.todoist_service import _apply_local_filter

        tasks = [
            {"id": "1", "labels": ["agent-ready"]},
            {"id": "2", "labels": ["agent-ready", "human-only"]},
            {"id": "3", "labels": ["agent-ready"]},
        ]
        filtered = _apply_local_filter(
            tasks, "(overdue | today | no date) & @agent-ready & !@blocked & !@human-only & !@escalated"
        )
        ids = [t["id"] for t in filtered]
        assert "2" not in ids
        assert "1" in ids
        assert "3" in ids

    def test_excludes_escalated_tasks(self):
        from universal_agent.services.todoist_service import _apply_local_filter

        tasks = [
            {"id": "1", "labels": ["agent-ready"]},
            {"id": "2", "labels": ["escalated"]},
        ]
        filtered = _apply_local_filter(
            tasks, "@agent-ready & !@escalated"
        )
        ids = [t["id"] for t in filtered]
        assert "2" not in ids
        assert "1" in ids

    def test_old_filter_still_works(self):
        """Backward compatibility: old filter without human-only/escalated still functions."""
        from universal_agent.services.todoist_service import _apply_local_filter

        tasks = [
            {"id": "1", "labels": ["agent-ready"]},
            {"id": "2", "labels": ["agent-ready", "blocked"]},
        ]
        filtered = _apply_local_filter(tasks, "@agent-ready & !@blocked")
        ids = [t["id"] for t in filtered]
        assert "1" in ids
        assert "2" not in ids


# ── Human-Only Label Tests ────────────────────────────────────────────────


class TestHumanOnlyLabel:
    def test_mark_human_only(self, todoist_service, fake_api):
        task = fake_api.add_task(content="Sensitive task", project_id="p", labels=["agent-ready"])
        result = todoist_service.mark_human_only(task.id, reason="Sensitive financial decision")
        assert result is True
        updated = fake_api.get_task(task.id)
        assert "human-only" in updated.labels
        assert "agent-ready" not in updated.labels

    def test_release_to_agents(self, todoist_service, fake_api):
        task = fake_api.add_task(content="Released task", project_id="p", labels=["human-only"])
        result = todoist_service.release_to_agents(task.id)
        assert result is True
        updated = fake_api.get_task(task.id)
        assert "agent-ready" in updated.labels
        assert "human-only" not in updated.labels


# ── Escalation Loop Tests ─────────────────────────────────────────────────


class TestEscalationLoop:
    def test_escalate_task_swaps_labels(self, todoist_service, fake_api):
        task = fake_api.add_task(content="Failing deploy", project_id="p", labels=["agent-ready"])
        result = todoist_service.escalate_task(
            task.id,
            reason="Cannot determine correct deploy target",
            issue_pattern="deploy_ambiguity",
        )
        assert result is True
        updated = fake_api.get_task(task.id)
        assert "escalated" in updated.labels
        assert "agent-ready" not in updated.labels

    def test_escalate_task_writes_to_db(self, todoist_service, fake_api, db_conn):
        task = fake_api.add_task(content="Missing creds", project_id="p", labels=["agent-ready"])
        todoist_service.escalate_task(
            task.id,
            reason="Missing API key for external service",
            context="Tried to call the N8N webhook but got 401",
            issue_pattern="missing_credentials",
            db_conn=db_conn,
        )
        row = db_conn.execute(
            "SELECT * FROM task_escalations WHERE task_id = ?",
            (task.id,),
        ).fetchone()
        assert row is not None
        assert row["issue_pattern"] == "missing_credentials"
        assert row["status"] == "open"
        assert "Missing API key" in row["escalation_reason"]

    def test_resolve_escalation_restores_agent_ready(self, todoist_service, fake_api):
        task = fake_api.add_task(content="Escalated task", project_id="p", labels=["escalated"])
        result = todoist_service.resolve_escalation(
            task.id,
            resolution="Added the correct API key via Infisical",
            guidance="Always check Infisical first for missing credentials",
        )
        assert result is True
        updated = fake_api.get_task(task.id)
        assert "agent-ready" in updated.labels
        assert "escalated" not in updated.labels

    def test_resolve_escalation_updates_db(self, todoist_service, fake_api, db_conn):
        """Full escalation → resolution cycle with DB persistence."""
        task = fake_api.add_task(content="Deploy fail", project_id="p", labels=["agent-ready"])
        todoist_service.escalate_task(
            task.id,
            reason="Deploy failed",
            issue_pattern="deploy_failure",
            db_conn=db_conn,
        )

        todoist_service.resolve_escalation(
            task.id,
            resolution="Fixed the systemd service file",
            guidance="Check service status with systemctl before redeploying",
            db_conn=db_conn,
        )

        row = db_conn.execute(
            "SELECT * FROM task_escalations WHERE task_id = ?",
            (task.id,),
        ).fetchone()
        assert row["status"] == "resolved"
        assert "systemd" in row["resolution"]
        assert row["resolved_by"] == "human"


# ── Escalation Memory Tests ──────────────────────────────────────────────


class TestEscalationMemory:
    def test_check_memory_finds_past_resolution(self, todoist_service, fake_api, db_conn):
        """Simulate: escalate, resolve, then check memory for same pattern."""
        task = fake_api.add_task(content="Webhook missing", project_id="p", labels=["agent-ready"])
        todoist_service.escalate_task(
            task.id,
            reason="Cannot find N8N webhook URL",
            issue_pattern="missing_webhook_url",
            db_conn=db_conn,
        )
        todoist_service.resolve_escalation(
            task.id,
            resolution="Webhook URL is in Infisical under N8N_WEBHOOK_URL",
            guidance="Always check Infisical for service URLs",
            db_conn=db_conn,
        )

        # Now check memory
        from universal_agent.services.todoist_service import TodoService
        memories = TodoService.check_escalation_memory(
            "missing_webhook_url", db_conn=db_conn
        )
        assert len(memories) >= 1
        assert "Infisical" in memories[0]["resolution"]

    def test_check_memory_returns_empty_for_unknown_pattern(self, db_conn):
        from universal_agent.services.todoist_service import TodoService
        memories = TodoService.check_escalation_memory(
            "never_seen_before", db_conn=db_conn
        )
        assert memories == []

    def test_memory_limits_results(self, todoist_service, fake_api, db_conn):
        """Verify the limit parameter works."""
        for i in range(5):
            task = fake_api.add_task(
                content=f"Error task {i}", project_id="p", labels=["agent-ready"]
            )
            todoist_service.escalate_task(
                task.id,
                reason=f"Error #{i}",
                issue_pattern="repeated_error",
                db_conn=db_conn,
            )
            todoist_service.resolve_escalation(
                task.id,
                resolution=f"Fixed #{i}",
                db_conn=db_conn,
            )

        from universal_agent.services.todoist_service import TodoService
        memories = TodoService.check_escalation_memory(
            "repeated_error", db_conn=db_conn, limit=2
        )
        assert len(memories) == 2


# ── Default Labels Test ──────────────────────────────────────────────────


class TestDefaultLabels:
    def test_new_labels_in_default_list(self):
        from universal_agent.services.todoist_service import DEFAULT_AGENT_LABELS

        assert "human-only" in DEFAULT_AGENT_LABELS
        assert "escalated" in DEFAULT_AGENT_LABELS
        assert "auto-corrected" in DEFAULT_AGENT_LABELS
