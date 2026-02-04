
from typing import Any
from telegram.helpers import escape_markdown

def format_telegram_response(task_result: Any) -> str:
    """
    Format the execution result into a Telegram-friendly markdown message.
    """
    if isinstance(task_result, str):
        return task_result
        
    try:
        result = task_result
        lines = []
        
        # 1. Header with Execution Stats
        stats = []
        if getattr(result, "execution_time_seconds", 0) > 0:
            stats.append(f"⏱ {result.execution_time_seconds:.1f}s")
        
        tool_count = getattr(result, "tool_calls", 0)
        if tool_count > 0:
            stats.append(f"🔧 {tool_count} tools")
            
        if getattr(result, "code_execution_used", False):
            stats.append("🏭 Code")
            
        if stats:
            lines.append(" | ".join(stats))
            lines.append("")  # Spacer
            
        # 2. Main Response
        # Escape markdown V2 special characters in the response text
        # Note: We assume the response text is NOT already markdown formatted unless escaping logic is handled upstream.
        # But telegram.helpers.escape_markdown with version=2 is aggressive.
        
        safe_text = escape_markdown(str(result.response_text), version=2)
        lines.append(safe_text)
        lines.append("")
        
        # 3. Footer Links & Meta
        footer_parts = []
        
        trace_id = getattr(result, "trace_id", None)
        if trace_id:
            logfire_url = f"https://logfire.pydantic.dev/Kjdragan/composio-claudemultiagent?q=trace_id%3D%27{trace_id}%27"
            escaped_url = escape_markdown(logfire_url, version=2)
            footer_parts.append(f"📊 [View Trace]({escaped_url})")
            
        if footer_parts:
            lines.append(" · ".join(footer_parts))
            
        return "\n".join(lines)
        
    except Exception as e:
        return getattr(task_result, "response_text", str(task_result))
