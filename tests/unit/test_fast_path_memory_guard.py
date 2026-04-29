from __future__ import annotations

from claude_agent_sdk.types import AssistantMessage, ToolUseBlock
import pytest


class _FakeClient:
    def __init__(self, messages):
        self.messages = messages
        self.queries: list[str] = []

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)

    async def receive_response(self):
        for message in self.messages:
            yield message


@pytest.mark.asyncio
async def test_simple_query_ignores_memory_tool_when_memory_disabled(monkeypatch):
    from universal_agent import main as ua_main

    monkeypatch.setenv("UA_DISABLE_MEMORY", "1")
    client = _FakeClient(
        [
            AssistantMessage(
                content=[
                    ToolUseBlock(id="toolu_memory", name="memory_search", input={"query": "heartbeat"})
                ],
                model="test",
            )
        ]
    )

    handled, response_text = await ua_main.handle_simple_query(client, "what changed?")

    assert handled is True
    assert response_text == ""


@pytest.mark.asyncio
async def test_simple_query_falls_back_for_non_memory_tool(monkeypatch):
    from universal_agent import main as ua_main

    monkeypatch.setenv("UA_DISABLE_MEMORY", "1")
    client = _FakeClient(
        [
            AssistantMessage(
                content=[ToolUseBlock(id="toolu_bash", name="Bash", input={"command": "date"})],
                model="test",
            )
        ]
    )

    handled, response_text = await ua_main.handle_simple_query(client, "what time is it?")

    assert handled is False
    assert response_text == ""
