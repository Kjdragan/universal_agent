from __future__ import annotations

import json


class _DummyConn:
    row_factory = None

    def close(self) -> None:
        return None


async def test_task_hub_task_action_impl_accepts_lifecycle_action(monkeypatch):
    from universal_agent.tools import task_hub_bridge as mod

    monkeypatch.setattr(mod, "connect_runtime_db", lambda _path: _DummyConn())
    monkeypatch.setattr(mod, "get_activity_db_path", lambda: "ignored.db")
    monkeypatch.setattr(
        mod.task_hub,
        "perform_task_action",
        lambda conn, **kwargs: {"task_id": kwargs["task_id"], "status": "needs_review"},
    )

    res = await mod._task_hub_task_action_impl({"task_id": "task-1", "action": "review"})
    payload = json.loads(res["content"][0]["text"])
    assert payload["success"] is True
    assert payload["task_id"] == "task-1"
    assert payload["action"] == "review"
    assert payload["item"]["status"] == "needs_review"


async def test_task_hub_task_action_impl_rejects_unsupported_action():
    from universal_agent.tools import task_hub_bridge as mod

    res = await mod._task_hub_task_action_impl({"task_id": "task-1", "action": "seize"})
    assert "error:" in res["content"][0]["text"]
    assert "unsupported action" in res["content"][0]["text"]
