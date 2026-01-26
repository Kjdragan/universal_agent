"""
Message History utility for context management.

Mirrors Anthropic's MessageHistory pattern from claude-quickstarts.
Tracks per-message token counts and truncates oldest pairs when over threshold.
"""

import logfire

# Constants
CONTEXT_WINDOW_TOKENS = 200000  # Claude Sonnet 4.0 context window
TRUNCATION_THRESHOLD = 150000   # User Target 2026-01-26 - trigger handoff at 150k
TRUNCATION_NOTICE_TOKENS = 25   # Tokens for truncation notice

TRUNCATION_MESSAGE = {
    "role": "user", 
    "content": "[Earlier conversation history has been truncated to manage context window]"
}


class MessageHistory:
    """
    Manages conversation history with per-message token tracking.
    
    Truncates oldest message pairs when approaching context limit.
    Used for within-session context management, complementing
    the between-session harness handoff.
    """
    
    def __init__(self, system_prompt_tokens: int = 2000):
        """
        Initialize message history.
        
        Args:
            system_prompt_tokens: Estimated tokens for system prompt (baseline)
        """
        self.messages: list[dict] = []           # {role, content}
        self.message_tokens: list[tuple] = []    # (input_tokens, output_tokens) per turn
        self.total_tokens: int = system_prompt_tokens
        self._system_prompt_tokens = system_prompt_tokens
        self._truncation_count = 0
    
    def add_message(self, role: str, content, usage=None) -> None:
        """
        Add message and track tokens from API response.
        
        Args:
            role: "user" or "assistant"
            content: Message content (string or content blocks)
            usage: API response usage object with input_tokens/output_tokens
        """
        # Store message
        self.messages.append({"role": role, "content": content})
        
        if usage:
            inp = getattr(usage, "input_tokens", 0) or 0
            out = getattr(usage, "output_tokens", 0) or 0
            
            # Cache-related tokens (if present)
            cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            total_input = inp + cache_creation + cache_read
            
            # Calculate this turn's input contribution (exclude prior context)
            current_input = max(0, total_input - self.total_tokens)
            
            self.message_tokens.append((current_input, out))
            self.total_tokens += current_input + out
        else:
            # No usage info - estimate based on content length
            # Rough estimate: 1 token per 4 chars
            estimated = len(str(content)) // 4 if content else 0
            self.message_tokens.append((estimated, 0))
            self.total_tokens += estimated
    
    def truncate(self) -> bool:
        """
        Remove oldest message pairs if over threshold.
        
        Returns:
            True if truncation occurred, False otherwise
        """
        if self.total_tokens <= TRUNCATION_THRESHOLD:
            return False
        
        truncated = False
        
        # Remove pairs until under threshold
        while (self.message_tokens and 
               len(self.messages) >= 2 and 
               self.total_tokens > TRUNCATION_THRESHOLD):
            self._remove_oldest_pair()
            truncated = True
        
        # Replace first message with truncation notice
        if truncated and self.messages and self.message_tokens:
            old_input_tokens = self.message_tokens[0][0]
            
            # Replace message
            self.messages[0] = TRUNCATION_MESSAGE.copy()
            
            # Adjust token count
            token_diff = old_input_tokens - TRUNCATION_NOTICE_TOKENS
            self.message_tokens[0] = (TRUNCATION_NOTICE_TOKENS, self.message_tokens[0][1])
            self.total_tokens -= token_diff
            
            self._truncation_count += 1
            
            logfire.warning(
                "message_history_truncated",
                truncation_count=self._truncation_count,
                total_tokens=self.total_tokens,
                remaining_messages=len(self.messages),
            )
        
        return truncated
    
    def _remove_oldest_pair(self) -> None:
        """Remove oldest user-assistant message pair."""
        if len(self.messages) < 2:
            return
        
        # Remove first two messages (user + assistant)
        self.messages.pop(0)
        self.messages.pop(0)
        
        # Subtract their tokens
        if len(self.message_tokens) >= 2:
            t1 = self.message_tokens.pop(0)
            t2 = self.message_tokens.pop(0)
            removed_tokens = t1[0] + t1[1] + t2[0] + t2[1]
            self.total_tokens -= removed_tokens
    
    def should_handoff(self) -> bool:
        """
        Check if we should trigger harness handoff.
        
        Returns:
            True if at or over truncation threshold
        """
        return self.total_tokens >= TRUNCATION_THRESHOLD
    
    def get_stats(self) -> dict:
        """Get current history statistics."""
        return {
            "total_tokens": self.total_tokens,
            "message_count": len(self.messages),
            "truncation_count": self._truncation_count,
            "threshold": TRUNCATION_THRESHOLD,
            "remaining_capacity": max(0, TRUNCATION_THRESHOLD - self.total_tokens),
            "utilization_pct": round(100 * self.total_tokens / CONTEXT_WINDOW_TOKENS, 1),
        }
    
    def format_for_api(self) -> list[dict]:
        """
        Format messages for Anthropic API call.
        
        Returns:
            List of message dicts ready for API
        """
        return self.messages.copy()
    
    def reset(self) -> None:
        """Reset history to initial state (for new session)."""
        self.messages = []
        self.message_tokens = []
        self.total_tokens = self._system_prompt_tokens
        self._truncation_count = 0
