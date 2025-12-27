from Memory_System.manager import MemoryManager
from .common import AGENT_COLLEGE_NOTES_BLOCK
from datetime import datetime

class CriticAgent:
    def __init__(self, memory_manager: MemoryManager):
        self.memory = memory_manager

    def propose_correction(self, trace_id: str, suggestion: str) -> str:
        """
        Appends a correction proposal to the Sandbox memory.
        """
        try:
            timestamp = datetime.now().isoformat()
            note = f"\n[CRITIC {timestamp}] Trace {trace_id}: {suggestion}"
            
            # Append to existing block
            current_value = ""
            block = self.memory.get_memory_block(AGENT_COLLEGE_NOTES_BLOCK)
            if block:
                current_value = block.value
            
            new_value = current_value + note
            
            # Update
            self.memory.update_memory_block(AGENT_COLLEGE_NOTES_BLOCK, new_value)
            return "Correction proposed."
        except Exception as e:
            return f"Failed to propose correction: {e}"
