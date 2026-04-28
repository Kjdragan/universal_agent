def _has_substantive_mutating_tool_calls(tool_calls_by_id: dict | None) -> bool:
    if not tool_calls_by_id:
        return False
    
    mutating_tools = {
        "run_command", "bash", "edit", "replace_file_content", "multi_replace_file_content",
        "write_to_file", "mcp__agentmail__send_message", "mcp__agentmail__reply_to_message",
    }
    
    for tc in tool_calls_by_id.values():
        name = str(tc.get("name") or "").strip().lower()
        if name in mutating_tools:
            return True
        # Catch github mutating tools
        if "github" in name and any(x in name for x in ["create", "update", "push", "merge", "add", "delete"]):
            return True
    return False
