from universal_agent import main as agent_main


def test_injects_idempotency_for_composio_multi_execute():
    tool_input = {
        "tools": [
            {"tool_slug": "GMAIL_SEND_EMAIL", "arguments": {"recipient_email": "a@b.com"}}
        ]
    }
    agent_main._inject_provider_idempotency(
        "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL", tool_input, "idem-123"
    )
    assert tool_input["client_request_id"] == "idem-123"
    args = tool_input["tools"][0]["arguments"]
    assert args["client_request_id"] == "idem-123:0"
