from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("query", "expected_target"),
    [
        ("/use-brainstorm-tonight dedupe:retry-policy", "dedupe:retry-policy"),
        ("/brainstorm-tonight task_42", "task_42"),
        ("use this brainstorm tonight dedupe:cache-cleanup", "dedupe:cache-cleanup"),
        ("promote brainstorm idea tonight task_99", "task_99"),
    ],
)
def test_parse_use_brainstorm_tonight_command(query: str, expected_target: str):
    import universal_agent.api.server as server

    matched, target = server._parse_use_brainstorm_tonight_command(query)
    assert matched is True
    assert target == expected_target


def test_parse_use_brainstorm_tonight_command_non_match():
    import universal_agent.api.server as server

    matched, target = server._parse_use_brainstorm_tonight_command("research this repo")
    assert matched is False
    assert target == ""


@pytest.mark.asyncio
async def test_try_handle_todoist_quick_command_success(monkeypatch):
    import universal_agent.api.server as server
    from universal_agent.api.events import EventType as WSEventType

    sent_events: list[tuple[str, object]] = []

    class DummyManager:
        async def send_event(self, connection_id: str, event):
            sent_events.append((connection_id, event))

    class FakeTodoService:
        def promote_idea_to_heartbeat_candidate(self, target: str):
            assert target == "dedupe:retry-policy"
            return {
                "success": True,
                "task_id": "task_1",
                "content": "Retry policy",
                "previous_section": "inbox",
            }

    monkeypatch.setattr(server, "manager", DummyManager())
    monkeypatch.setattr("universal_agent.services.todoist_service.TodoService", FakeTodoService)

    handled = await server._try_handle_todoist_quick_command(
        "conn_1", "/use-brainstorm-tonight dedupe:retry-policy"
    )

    assert handled is True
    assert len(sent_events) == 2
    assert sent_events[0][0] == "conn_1"
    assert sent_events[0][1].type == WSEventType.TEXT
    assert "Promoted brainstorm to Heartbeat Candidate" in str(sent_events[0][1].data.get("text"))
    assert sent_events[1][1].type == WSEventType.QUERY_COMPLETE


@pytest.mark.asyncio
async def test_try_handle_todoist_quick_command_missing_target(monkeypatch):
    import universal_agent.api.server as server
    from universal_agent.api.events import EventType as WSEventType

    sent_events: list[tuple[str, object]] = []

    class DummyManager:
        async def send_event(self, connection_id: str, event):
            sent_events.append((connection_id, event))

    monkeypatch.setattr(server, "manager", DummyManager())

    handled = await server._try_handle_todoist_quick_command("conn_2", "/use-brainstorm-tonight")

    assert handled is True
    assert len(sent_events) == 2
    assert sent_events[0][1].type == WSEventType.TEXT
    assert "Usage:" in str(sent_events[0][1].data.get("text"))
    assert sent_events[1][1].type == WSEventType.QUERY_COMPLETE
