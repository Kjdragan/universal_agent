from datetime import datetime
from typing import Optional, Any
import os

def format_telegram_response(task_result: Any) -> str:
    """
    Format the execution result into a Telegram-friendly markdown message.
    
    Args:
        task_result: ExecutionResult object (from task.execution_summary) 
                     OR string (fallback)
    """
    # Fallback for simple string results (or errors)
    if isinstance(task_result, str):
        return task_result
        
    try:
        # It's an ExecutionResult object (duck typed)
        result = task_result
        lines = []
        
        # 1. Header with Execution Stats
        # E.g. "⏱ 12.5s | 🔧 5 tools"
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
        from telegram.helpers import escape_markdown
        safe_text = escape_markdown(str(result.response_text), version=2)
        lines.append(safe_text)
        lines.append("")
        
        # 3. Footer Links & Meta
        footer_parts = []
        
        # Logfire Trace Link
        trace_id = getattr(result, "trace_id", None)
        if trace_id:
            # Construct Logfire URL (Project slug hardcoded for now or env var?)
            # Using generic link or specific project url if known
            # "https://logfire.pydantic.dev/Kjdragan/composio-claudemultiagent?q=trace_id%3D%27{trace_id}%27"
            # We'll use a generic search link if possible, or build it
            logfire_url = f"https://logfire.pydantic.dev/Kjdragan/composio-claudemultiagent?q=trace_id%3D%27{trace_id}%27"
            # Escape the link components for MarkdownV2
            escaped_url = escape_markdown(logfire_url, version=2)
            # escape_markdown escapes '(', ')', etc. which is safe for the link destination in V2
            footer_parts.append(f"📊 [View Trace]({escaped_url})")
            
        if footer_parts:
            lines.append(" · ".join(footer_parts))
            
        return "\n".join(lines)
        
    except Exception as e:
        # Safe fallback if formatting fails
        print(f"⚠️ Formatting error: {e}")
        # Try to return just the response text if possible, otherwise raw string
        return getattr(task_result, "response_text", str(task_result))
