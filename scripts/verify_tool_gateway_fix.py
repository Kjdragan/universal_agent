
import sys
import os

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))

from universal_agent.durable.tool_gateway import parse_tool_identity

def test_malformed_tool_parsing():
    malformed_input = 'mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOLtools</arg_key><arg_value>[{"tool_slug": "COMPOSIO_SEARCH_WEB", "arguments": {"query": "Fortune 100 2025 list top 10 companies by revenue"}}]</arg_value>'
    
    print(f"Testing input: {malformed_input[:100]}...")
    
    identity = parse_tool_identity(malformed_input)
    
    print(f"Result: {identity}")
    
    assert identity.tool_name == "COMPOSIO_MULTI_EXECUTE_TOOL", f"Expected COMPOSIO_MULTI_EXECUTE_TOOL, got {identity.tool_name}"
    assert identity.tool_namespace == "mcp", f"Expected mcp, got {identity.tool_namespace}"
    
    print("✅ Verification Successful: Malformed tool name correctly sanitized.")

if __name__ == "__main__":
    test_malformed_tool_parsing()
