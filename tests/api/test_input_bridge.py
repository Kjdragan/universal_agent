import pytest

from universal_agent.api import input_bridge


@pytest.mark.asyncio
async def test_request_user_input_uses_global_handler():
    called = {"ok": False}

    async def handler(question: str, category: str, options):
        called["ok"] = True
        return "ack"

    input_bridge.set_input_handler(handler)

    # Simulate a context where the ContextVar was not propagated.
    input_bridge._input_handler_var.set(None)

    result = await input_bridge.request_user_input("Test question?")
    assert result == "ack"
    assert called["ok"] is True

    input_bridge.set_input_handler(None)
