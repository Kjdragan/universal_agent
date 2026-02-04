
from typing import Any, List
from telegram.helpers import escape_markdown

# Telegram has a 4096 character limit for messages
TELEGRAM_MAX_LENGTH = 4096
TRUNCATION_SUFFIX = "\n\n⚠️ _Message truncated due to Telegram limit_"


def truncate_message(text: str, max_length: int = TELEGRAM_MAX_LENGTH) -> str:
    """
    Truncate message to fit Telegram's limit, preserving word boundaries.
    """
    if len(text) <= max_length:
        return text
    
    # Reserve space for truncation suffix
    available = max_length - len(TRUNCATION_SUFFIX)
    
    # Find a good break point (newline or space)
    truncated = text[:available]
    
    # Try to break at last paragraph
    last_para = truncated.rfind("\n\n")
    if last_para > available * 0.5:  # Only if we keep at least 50% of content
        return truncated[:last_para] + TRUNCATION_SUFFIX
    
    # Try to break at last sentence
    for delim in [". ", "! ", "? "]:
        last_sentence = truncated.rfind(delim)
        if last_sentence > available * 0.7:
            return truncated[:last_sentence + 1] + TRUNCATION_SUFFIX
    
    # Fallback: break at last word
    last_space = truncated.rfind(" ")
    if last_space > available * 0.8:
        return truncated[:last_space] + TRUNCATION_SUFFIX
    
    return truncated + TRUNCATION_SUFFIX


def format_telegram_response(task_result: Any) -> str:
    """
    Format the execution result into a Telegram-friendly markdown message.
    """
    if isinstance(task_result, str):
        return truncate_message(task_result)
        
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
        
        full_message = "\n".join(lines)
        return truncate_message(full_message)
        
    except Exception as e:
        raw = getattr(task_result, "response_text", str(task_result))
        return truncate_message(raw)

