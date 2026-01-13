
import asyncio
from universal_agent.agent_core import malformed_tool_guardrail_hook

async def test_guard_logic():
    # Test Case 1: Empty Content
    input_bad = {"tool_name": "Write", "tool_input": {"content": ""}}
    result_bad = await malformed_tool_guardrail_hook(input_bad, "test-id", {})
    
    if result_bad.get("decision") == "block" and "0-byte" in result_bad["hookSpecificOutput"]["permissionDecisionReason"]:
        print("✅ Test 1 Passed: Blocked empty content")
    else:
        print(f"❌ Test 1 Failed: {result_bad}")

    # Test Case 2: Whitespace Content
    input_ws = {"tool_name": "Write", "tool_input": {"content": "   "}}
    result_ws = await malformed_tool_guardrail_hook(input_ws, "test-id", {})
    
    if result_ws.get("decision") == "block":
        print("✅ Test 2 Passed: Blocked whitespace content")
    else:
        print(f"❌ Test 2 Failed: {result_ws}")

    # Test Case 3: Valid Content
    input_good = {"tool_name": "Write", "tool_input": {"content": "valid data"}}
    result_good = await malformed_tool_guardrail_hook(input_good, "test-id", {})
    
    if not result_good:
        print("✅ Test 3 Passed: Allowed valid content")
    else:
        print(f"❌ Test 3 Failed: Blocked valid content {result_good}")

if __name__ == "__main__":
    asyncio.run(test_guard_logic())
