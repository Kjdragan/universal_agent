from Memory_System.manager import MemoryManager
from .common import AGENT_COLLEGE_NOTES_BLOCK
import logging

logger = logging.getLogger(__name__)

def setup_agent_college(memory_manager: MemoryManager):
    """
    Initializes the Agent College subsystems.
    1. Ensures the 'Sandbox' memory block exists.
    """
    try:
        # Ensure the Sandbox block exists
        block = memory_manager.get_memory_block(AGENT_COLLEGE_NOTES_BLOCK)
        if not block:
            logger.info("Initializing Agent College Sandbox memory block.")
            initial_content = "Scratchpad for Agent College (Professor, Critic, Scribe).\n"
            memory_manager.update_memory_block(AGENT_COLLEGE_NOTES_BLOCK, initial_content)

        logger.info("Agent College setup complete: Sandbox ready.")
        
    except Exception as e:
        logger.error(f"Failed to setup Agent College: {e}")
