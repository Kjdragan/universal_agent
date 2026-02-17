from __future__ import annotations

import json


class FakeTodoService:
    def ensure_taxonomy(self):
        return {"agent_project_id": "p1", "brainstorm_project_id": "p2"}

    def get_actionable_tasks(self, filter_str=None):
        return [{"id": "t1", "content": "x", "priority": "P4-Low"}]

    def get_task_detail(self, task_id: str):
        if task_id == "missing":
            return None
        return {"id": task_id, "comments": []}

    def create_task(self, **kwargs):
        return {"id": "new", "content": kwargs.get("content")}

    def complete_task(self, task_id: str, summary=None):
        return task_id != "fail"

    def delete_task(self, task_id: str):
        return True

    def add_comment(self, task_id: str, content: str):
        return True

    def mark_blocked(self, task_id: str, reason: str):
        return True

    def unblock_task(self, task_id: str):
        return True

    def mark_needs_review(self, task_id: str, result_summary: str):
        return True

    def update_task(self, task_id: str, **kwargs):
        return True

    def record_idea(self, **kwargs):
        return {"id": "idea1", "content": kwargs.get("content")}

    def promote_idea(self, task_id: str, target_section: str = "approved"):
        return True

    def park_idea(self, task_id: str, rationale: str):
        return True

    def get_pipeline_summary(self):
        return {"inbox": 1, "approved": 0}


async def test_todoist_query_wrapper_returns_json(monkeypatch):
    from universal_agent.tools import todoist_bridge as mod

    monkeypatch.setattr(mod, "_service", lambda: FakeTodoService())
    res = await mod._todoist_query_impl({"filter": "today"})
    payload = json.loads(res["content"][0]["text"])
    assert payload["count"] == 1


async def test_todoist_task_action_create(monkeypatch):
    from universal_agent.tools import todoist_bridge as mod

    monkeypatch.setattr(mod, "_service", lambda: FakeTodoService())
    res = await mod._todoist_task_action_impl({"action": "create", "content": "hello"})
    payload = json.loads(res["content"][0]["text"])
    assert payload["success"] is True
    assert payload["task"]["id"] == "new"


async def test_todoist_task_action_complete_failure(monkeypatch):
    from universal_agent.tools import todoist_bridge as mod

    monkeypatch.setattr(mod, "_service", lambda: FakeTodoService())
    res = await mod._todoist_task_action_impl({"action": "complete", "task_id": "fail"})
    payload = json.loads(res["content"][0]["text"])
    assert payload["success"] is False


async def test_todoist_idea_action_pipeline(monkeypatch):
    from universal_agent.tools import todoist_bridge as mod

    monkeypatch.setattr(mod, "_service", lambda: FakeTodoService())
    res = await mod._todoist_idea_action_impl({"action": "pipeline"})
    payload = json.loads(res["content"][0]["text"])
    assert payload["counts"]["inbox"] == 1


async def test_todoist_idea_action_record_requires_content(monkeypatch):
    from universal_agent.tools import todoist_bridge as mod

    monkeypatch.setattr(mod, "_service", lambda: FakeTodoService())
    res = await mod._todoist_idea_action_impl({"action": "record"})
    assert "error:" in res["content"][0]["text"]
