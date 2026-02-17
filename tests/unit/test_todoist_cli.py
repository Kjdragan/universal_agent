from __future__ import annotations

import json

from click.testing import CliRunner


class FakeTodoService:
    def __init__(self):
        self.calls = []

    def ensure_taxonomy(self):
        self.calls.append(("ensure_taxonomy", {}))
        return {"agent_project_id": "p1", "brainstorm_project_id": "p2", "agent_sections": {}, "brainstorm_sections": {}, "labels_created": []}

    def heartbeat_summary(self):
        self.calls.append(("heartbeat_summary", {}))
        return {"timestamp": "now", "actionable_count": 0}

    def get_actionable_tasks(self, filter_str=None):
        self.calls.append(("get_actionable_tasks", {"filter_str": filter_str}))
        return [{"id": "t1", "content": "x", "priority": "P4-Low"}]

    def get_task_detail(self, task_id: str):
        self.calls.append(("get_task_detail", {"task_id": task_id}))
        if task_id == "missing":
            return None
        return {"id": task_id, "content": "x", "comments": []}

    def create_task(self, **kwargs):
        self.calls.append(("create_task", kwargs))
        return {"id": "new", "content": kwargs.get("content"), "labels": ["agent-ready"]}

    def complete_task(self, task_id: str, summary=None):
        self.calls.append(("complete_task", {"task_id": task_id, "summary": summary}))
        return task_id != "fail"

    def add_comment(self, task_id: str, content: str):
        self.calls.append(("add_comment", {"task_id": task_id, "content": content}))
        return True

    def mark_blocked(self, task_id: str, reason: str):
        self.calls.append(("mark_blocked", {"task_id": task_id, "reason": reason}))
        return True

    def unblock_task(self, task_id: str):
        self.calls.append(("unblock_task", {"task_id": task_id}))
        return True

    def mark_needs_review(self, task_id: str, result_summary: str):
        self.calls.append(("mark_needs_review", {"task_id": task_id, "result_summary": result_summary}))
        return True

    def record_idea(self, **kwargs):
        self.calls.append(("record_idea", kwargs))
        return {"id": "idea1", "content": kwargs.get("content"), "description": kwargs.get("description", "")}

    def promote_idea(self, task_id: str, target_section: str = "approved"):
        self.calls.append(("promote_idea", {"task_id": task_id, "target_section": target_section}))
        return True

    def park_idea(self, task_id: str, rationale: str):
        self.calls.append(("park_idea", {"task_id": task_id, "rationale": rationale}))
        return True

    def get_pipeline_summary(self):
        self.calls.append(("get_pipeline_summary", {}))
        return {"inbox": 1, "approved": 0}


def test_cli_setup_outputs_json(monkeypatch):
    from universal_agent.cli import todoist_cli

    svc = FakeTodoService()
    monkeypatch.setattr(todoist_cli, "_get_service", lambda: svc)

    runner = CliRunner()
    result = runner.invoke(todoist_cli.cli, ["setup"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["agent_project_id"] == "p1"


def test_cli_task_missing_returns_error(monkeypatch):
    from universal_agent.cli import todoist_cli

    svc = FakeTodoService()
    monkeypatch.setattr(todoist_cli, "_get_service", lambda: svc)

    runner = CliRunner()
    result = runner.invoke(todoist_cli.cli, ["task", "missing"])
    assert result.exit_code == 2
    err = json.loads(result.stderr)
    assert err["success"] is False


def test_cli_idea_calls_service(monkeypatch):
    from universal_agent.cli import todoist_cli

    svc = FakeTodoService()
    monkeypatch.setattr(todoist_cli, "_get_service", lambda: svc)

    runner = CliRunner()
    result = runner.invoke(todoist_cli.cli, ["idea", "Try thing", "--dedupe-key", "abc", "--impact", "H", "--effort", "S"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["success"] is True
    assert svc.calls[-1][0] == "record_idea"


def test_cli_pipeline_outputs_counts(monkeypatch):
    from universal_agent.cli import todoist_cli

    svc = FakeTodoService()
    monkeypatch.setattr(todoist_cli, "_get_service", lambda: svc)

    runner = CliRunner()
    result = runner.invoke(todoist_cli.cli, ["pipeline"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["inbox"] == 1


def test_cli_create_calls_service(monkeypatch):
    from universal_agent.cli import todoist_cli

    svc = FakeTodoService()
    monkeypatch.setattr(todoist_cli, "_get_service", lambda: svc)

    runner = CliRunner()
    result = runner.invoke(todoist_cli.cli, ["create", "Hello", "--priority", "urgent", "--label", "foo", "--sub-agent", "research"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["success"] is True
    assert svc.calls
    assert svc.calls[-1][0] == "create_task"


def test_cli_complete_failure(monkeypatch):
    from universal_agent.cli import todoist_cli

    svc = FakeTodoService()
    monkeypatch.setattr(todoist_cli, "_get_service", lambda: svc)

    runner = CliRunner()
    result = runner.invoke(todoist_cli.cli, ["complete", "fail"])
    assert result.exit_code == 1
    err = json.loads(result.stderr)
    assert err["success"] is False
