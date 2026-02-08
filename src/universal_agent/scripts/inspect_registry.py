
from universal_agent.tools.internal_registry import get_internal_tool_slugs, get_all_internal_tools

print("--- Slugs ---")
slugs = get_internal_tool_slugs(enable_memory=True)
for s in slugs[:3]:
    print(f"Slug: {s} (Type: {type(s)})")

print("\n--- Tools ---")
tools = get_all_internal_tools(enable_memory=True)
for t in tools[:3]:
    print(f"Tool: {t} (Type: {type(t)})")
    if hasattr(t, 'name'):
        print(f"Tool.name: {t.name}")
    if hasattr(t, '__name__'):
        print(f"Tool.__name__: {t.__name__}")
