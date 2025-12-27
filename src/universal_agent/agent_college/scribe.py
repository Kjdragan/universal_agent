from Memory_System.manager import MemoryManager
from .common import AGENT_COLLEGE_NOTES_BLOCK
from datetime import datetime

class ScribeAgent:
    def __init__(self, memory_manager: MemoryManager):
        self.memory = memory_manager

    def propose_fact(self, content: str) -> str:
        """
        Appends a fact proposal to the Sandbox memory.
        """
        try:
            timestamp = datetime.now().isoformat()
            note = f"\n[SCRIBE {timestamp}] Fact: {content}"
            
            # Append to existing block
            current_value = ""
            block = self.memory.get_memory_block(AGENT_COLLEGE_NOTES_BLOCK)
            if block:
                current_value = block.value
            
            new_value = current_value + note
            
            self.memory.update_memory_block(AGENT_COLLEGE_NOTES_BLOCK, new_value)
            return "Fact proposed."
        except Exception as e:
            return f"Failed to propose fact: {e}"
