
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import json

# Try to import types from SDK, or fallback to dicts
try:
    from claude_agent_sdk.types import Message, UserMessage, AssistantMessage, ToolUseBlock, ToolResultBlock, TextBlock
except ImportError:
    # Fallback types for pure logic if SDK not present (unlikely)
    Message = Dict[str, Any]

logger = logging.getLogger(__name__)

@dataclass
class PruningStats:
    original_tokens: int
    pruned_tokens: int
    messages_removed: int
    tools_summarized: int

class ContextManager:
    """
    Manages conversation history and implements 'Micro-Pruning' to maintain context 
    within token limits without losing critical information.
    """
    
    def __init__(self, target_token_limit: int = 200000):
        self.target_token_limit = target_token_limit
        # Simple heuristic: 4 chars ~= 1 token
        self.chars_per_token = 4

    def estimate_tokens(self, messages: List[Any]) -> int:
        """Estimate token count for a list of messages."""
        total_chars = 0
        for msg in messages:
            # Handle SDK objects
            if hasattr(msg, 'content'):
                content = msg.content
                if isinstance(content, str):
                    total_chars += len(content)
                elif isinstance(content, list):
                    for block in content:
                        if hasattr(block, 'text'):
                            total_chars += len(block.text)
                        elif hasattr(block, 'json_patch'): # patches
                            total_chars += len(str(block.json_patch))
                        # Tool use inputs
                        if hasattr(block, 'input') and block.input:
                            total_chars += len(str(block.input))
                        # Tool results
                        if hasattr(block, 'content') and block.content:
                            total_chars += len(str(block.content))
            # Handle dicts
            elif isinstance(msg, dict):
                content = msg.get('content', '')
                if isinstance(content, str):
                    total_chars += len(content)
                else:
                    total_chars += len(str(content))
                    
        return total_chars // self.chars_per_token

    def prune_history(self, history: List[Any]) -> tuple[List[Any], PruningStats]:
        """
        Prune the history by identifying closed tool loops and summarizing them.
        
        Strategy:
        1. Keep the first Message (System/User init) intact.
        2. Keep the last N messages (Buffer) intact.
        3. Scanning the middle:
           - Identify ToolUse -> ToolResult pairs.
           - Replace them with a summary note if they are "closed" (success).
           - Remove valid but verbose outputs.
        """
        if not history:
            return [], PruningStats(0, 0, 0, 0)

        original_tokens = self.estimate_tokens(history)
        
        # Configuration
        KEEP_HEAD = 1 # Always keep the very first prompt
        KEEP_TAIL = 5 # Keep last 5 messages as working memory
        
        if len(history) <= (KEEP_HEAD + KEEP_TAIL):
            return history, PruningStats(original_tokens, original_tokens, 0, 0)
            
        head = history[:KEEP_HEAD]
        tail = history[-KEEP_TAIL:]
        middle = history[KEEP_HEAD:-KEEP_TAIL]
        
        pruned_middle = []
        i = 0
        tools_summarized = 0
        
        while i < len(middle):
            msg = middle[i]
            
            # Check if this is an Assistant message with ToolUse
            is_tool_use = False
            tool_uses = []
            
            if hasattr(msg, 'content') and isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        is_tool_use = True
                        tool_uses.append(block)
            
            # Simple Pattern: Assistant(ToolUse) -> User(ToolResult)
            if is_tool_use and (i + 1 < len(middle)):
                next_msg = middle[i+1]
                is_tool_result = False
                # Check if next message is tool results matching the uses
                # Simplify: just check if next is User/ToolResult message
                if hasattr(next_msg, 'content') and isinstance(next_msg.content, list):
                    # It's a list of blocks
                    # Check if all blocks are ToolResultBlocks
                    # Actually, just check if it contains ANY result.
                    # In SDK, results come as UserMessage with ToolResultBlocks
                    result_blocks = [b for b in next_msg.content if isinstance(block, ToolResultBlock)]
                    if result_blocks or isinstance(next_msg, UserMessage): # UserMessage often holds the result
                         is_tool_result = True
                
                if is_tool_result:
                    # FOUND A LOOP: Tool Use -> Tool Result
                    # We prune this by replacing BOTH with a summary message?
                    # Or better: We keep the user intent but summarize the execution?
                    
                    # SDK constraint: We can't easily inject "fake" summaries that upset the turn structure (User-Assistant-User).
                    # Best Safe Bet: Collapse the result content.
                    
                    # 1. Keep the Assistant Tool Use (so model sees it asked)
                    pruned_middle.append(msg)
                    
                    # 2. Modify the Tool Result to be truncated
                    # We need to deep copy or assume we can modify (this is Python, distinct objects)
                    # Ideally create a new Message object with truncated content.
                    
                    # Mock truncation context
                    # For now, we will just SKIP adding the result message and the use message? 
                    # No, that breaks the conversation flow (Assistant must be followed by User).
                    
                    # Aggressive Pruning: Remove BOTH Use and Result.
                    # Replace with a User message saying "System: (Previous output summarized: Executed X, Y..)"
                    # But then we have User -> User (if previous was User).
                    # The sequence is User -> Assistant(Tool) -> User(Result) -> Assistant(Reply)
                    
                    # If we remove Assistant(Tool) and User(Result), we bridge:
                    # User -> Assistant(Reply)
                    # This is VALID structure! Assistant replies twice? No.
                    # User -> [Assistant(Tool) -> User(Result)] -> Assistant(Reply)
                    # Removing the bracket leaves User -> Assistant(Reply).
                    # The Assistant(Reply) usually interprets the result. 
                    # If we remove the result, Assistant(Reply) might look like hallucination?
                    # "Based on the search results..." (but no search results in history).
                    
                    # Logic: We cannot remove the Evidence (Result) if the subsequent reply depends on it.
                    # UNLESS we rewrite the Evidence to be a Summary.
                    
                    summary_text = f"[History Condensed: Executed {len(tool_uses)} tools. Output processed.]"
                    
                    # Construct valid replacement block
                    # If we can't easily import the SDK classes to instantiate new ones, we might need a different approach.
                    # For now, let's assume we modify the existing object's content if possible, or just skip proper pruning 
                    # until we fully support object reconstruction.
                    
                    # SAFE INITIAL IMPLEMENTATION:
                    # Just truncate large ToolResultBlocks.
                    
                    # Logic 2: Truncate Result Content
                    new_blocks = []
                    modified = False
                    if isinstance(next_msg.content, list):
                        for b in next_msg.content:
                            if isinstance(b, ToolResultBlock): # Check type name
                                # Truncate content
                                content_str = str(b.content) if b.content else ""
                                if len(content_str) > 500:
                                    # Create new block or modify
                                    # Since we can't safely mutate, we create a new block instance if we can mock it
                                    # or just modify the dict representation if we convert later.
                                    # Let's modify the object in place for now as we own the history list copy.
                                    # But wait, SDK objects might be frozen? Usually not.
                                    try:
                                        b.content = content_str[:200] + f"... [TRUNCATED: {len(content_str)} chars]"
                                        modified = True
                                        tools_summarized += 1
                                    except Exception:
                                        # If immutable, we might need a different strategy
                                        pass
                    
                    if modified:
                        # We successfully pruned in place
                        pass
                    
                    pruned_middle.append(next_msg) 
                    i += 2 # Skip both (we processed them)
                    continue
            
            # Default: Keep message
            pruned_middle.append(msg)
            i += 1
            
        final_history = head + pruned_middle + tail
        pruned_tokens = self.estimate_tokens(final_history)
        
        return final_history, PruningStats(
            original_tokens=original_tokens,
            pruned_tokens=pruned_tokens,
            messages_removed=len(history) - len(final_history),
            tools_summarized=tools_summarized
        )

    def convert_to_dicts(self, messages: List[Any]) -> List[Dict[str, Any]]:
        """
        Convert SDK Message objects to dictionaries suitable for 'client.connect(prompt=...)'.
        SDK objects usually have to_dict() or we can extract fields.
        """
        dicts = []
        for msg in messages:
            if hasattr(msg, 'to_dict'):
                dicts.append(msg.to_dict())
            elif isinstance(msg, dict):
                dicts.append(msg)
            else:
                # Fallback extraction
                pass
        return dicts
