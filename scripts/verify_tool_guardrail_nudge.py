
import sys
import os
import asyncio

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))

from universal_agent.guardrails.tool_schema import pre_tool_use_schema_guardrail

async def test_guardrail_nudge():
    malformed_input = {
        "tool_name": 'mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOLtools</arg_key><arg_value>...',
        "tool_input": {"some": "arg"}
    }
    
    print(f"Testing Guardrail Input: {malformed_input['tool_name'][:80]}...")
    
    result = await pre_tool_use_schema_guardrail(malformed_input)
    
    print(f"Guardrail Result: {result}")
    
    assert "systemMessage" in result, "Expected systemMessage in result"
    assert "malformed/hallucinated" in result["systemMessage"], "Expected 'malformed/hallucinated' in message"
    assert "Did you mean 'COMPOSIO_MULTI_EXECUTE_TOOL'?" in result["systemMessage"], "Expected suggestion to use correct tool name"
    
    print("✅ Verification Successful: Guardrail correctly nudges agent.")

if __name__ == "__main__":
    asyncio.run(test_guardrail_nudge())
