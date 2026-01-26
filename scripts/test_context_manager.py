
import sys
import os
import unittest
from dataclasses import dataclass, field
from typing import List, Any

# Mock SDK classes
@dataclass
class ToolUseBlock:
    name: str
    id: str
    input: dict

@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False

@dataclass
class TextBlock:
    text: str

@dataclass
class Message:
    role: str
    content: List[Any]

@dataclass
class AssistantMessage:
    content: List[Any]
    role: str = "assistant"

@dataclass
class UserMessage:
    content: List[Any]
    role: str = "user"

# Mock Sys Path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

# Mocking the missing SDK types in context_manager if needed
# But context_manager handles Dicts too.

from universal_agent.memory.context_manager import ContextManager

class TestContextManager(unittest.TestCase):
    def test_pruning(self):
        cm = ContextManager()
        
        # Create a history:
        # 1. System (kept)
        # 2. User Query
        # 3. Assistant (Tool Use) -> TO BE PRUNED
        # 4. User (Tool Result) -> TO BE PRUNED
        # 5. Assistant (Reply) -> KEPT (Tail)
        
        history = [
            {"role": "user", "content": "System Prompt"}, # Head
            {"role": "user", "content": "Find apples"},
            AssistantMessage(content=[TextBlock("Thinking..."), ToolUseBlock("search", "id_1", {"q": "apples"})]),
            UserMessage(content=[ToolResultBlock("id_1", "Found 100 apples including Granny Smith...", False)]),
            AssistantMessage(content=[TextBlock("I found 100 apples. Granny smith is one.")]), # Tail
        ]
        
        # We need more messages to trigger pruning because KEEP_HEAD=1 and KEEP_TAIL=5
        # Total len = 5. Limit is HEAD(1) + TAIL(5) = 6. So it won't prune.
        # Let's add more junk in the middle.
        
        history = [
            {"role": "user", "content": "System Prompt"}, # 0: Head
            {"role": "user", "content": "Find apples"}, # 1: Middle
            AssistantMessage(content=[ToolUseBlock("search", "id_1", {"q": "apples"})]), # 2: Tool Use
            UserMessage(content=[ToolResultBlock("id_1", "Found 100 apples...", False)]), # 3: Tool Result
            AssistantMessage(content=[ToolUseBlock("search", "id_2", {"q": "pears"})]), # 4: Tool Use
            UserMessage(content=[ToolResultBlock("id_2", "Found 50 pears...", False)]), # 5: Tool Result
            AssistantMessage(content=[TextBlock("Summary of apples and pears.")]), # 6: Tail (last 5?)
        ]
        
        # Tail = 5. So indices [2,3,4,5,6] are tail?
        # len=7. Head=1. Middle=[1]. Tail=5 ([2,3,4,5,6]).
        # So it won't prune the tools because they are in the TAIL!
        
        # We need a longer history to test pruning.
        history = [{"role": "user", "content": "System"}] # Head
        for i in range(10):
            # Add closed loops
            history.append(AssistantMessage(content=[ToolUseBlock("search", f"id_{i}", {"q": f"q_{i}"})]))
            history.append(UserMessage(content=[ToolResultBlock(f"id_{i}", "Result "*100, False)]))
            
        history.append(AssistantMessage(content=[TextBlock("Final Answer")]))
        
        print(f"Original Length: {len(history)}")
        
        pruned, stats = cm.prune_history(history)
        
        print(f"Pruned Length: {len(pruned)}")
        print(f"Stats: {stats}")
        
        # Expectation: 
        # Head (1) kept.
        # Tail (5) kept.
        # Middle has ~15 messages.
        # They are pairs of ToolUse/ToolResult.
        # Should be kept currently (as my implementation just appends them?)
        # Wait, my implementation said: 
        # "SAFE INITIAL IMPLEMENTATION: Just truncate large ToolResultBlocks."
        # And "Skip for now, just append original."
        
        # So I expect length to be SAME, but stats.original_tokens > stats.pruned_tokens IF truncation happens?
        # But I didn't verify if I implemented truncation or just "pass".
        
        # Let's check logic:
        # if len(content_str) > 500: pass (commented out)
        # pruned_middle.append(next_msg) 
        
        # Ah, so currently it does NOTHING!
        # I need to ENABLE the truncation in context_manager.py for this to work.
        
if __name__ == "__main__":
    unittest.main()
