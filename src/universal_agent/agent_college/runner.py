import asyncio
from datetime import datetime
import logging
import os
import signal
import sys

# Adjust path to find root (universal_agent/)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

# Import from root packages
import logfire

from Memory_System.manager import MemoryManager

# Import from src packages
from src.universal_agent.agent_college.critic import CriticAgent
from src.universal_agent.agent_college.logfire_reader import LogfireReader
from src.universal_agent.agent_college.professor import ProfessorAgent

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [AgentCollege] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Configure Logfire
if os.getenv("LOGFIRE_TOKEN"):
    logfire.configure(service_name="agent-college-worker")
else:
    logger.warning("LOGFIRE_TOKEN not set. Logfire will not be configured.")

# Suppress noisy HTTP libraries during polling
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from src.universal_agent.agent_college.logfire_reader import LogfireReader


async def main():
    logger.info("🎓 Agent College Worker Starting...")
    
    # 1. Initialize Memory
    try:
        memory_manager = MemoryManager()
        logger.info("✅ Memory System connected.")
    except Exception as e:
        logger.error(f"❌ Failed to connect to Memory System: {e}")
        return

    # 2. Initialize Agents & Logfire
    try:
        critic = CriticAgent(memory_manager)
        professor = ProfessorAgent(memory_manager)
        reader = LogfireReader()
        logger.info("✅ Critic, Professor & Logfire Reader initialized.")
    except Exception as e:
        logger.error(f"❌ Failed to initialize components: {e}")
        return

    # 3. Main Loop
    logger.info("🚀 Agent College is active and monitoring...")
    
    running = True
    # processed_traces = set() # Removed in favor of persistent memory
    
    def signal_handler(sig, frame):
        nonlocal running
        logger.info("🛑 Shutdown signal received.")
        running = False
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    while running:
        try:
            # Poll Logfire for recent failures
            failures = reader.get_failures(limit=5)
            
            for fail in failures:
                tid = fail.get('trace_id')
                if tid and not memory_manager.has_trace_been_processed(tid):
                    # New failure found!
                    msg = f"Exception: {fail.get('exception_type')}: {fail.get('exception_message')}"
                    logger.info(f"🔍 Critic analyzing trace {tid}: {msg}")
                    
                    # Propose correction to Memory
                    result = critic.propose_correction(tid, msg)
                    logger.info(f"   -> {result}")
                    
                    # Mark as processed in persistent memory
                    memory_manager.mark_trace_processed(tid)
            
            # Sleep
            await asyncio.sleep(60) 
            
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            await asyncio.sleep(5)

    logger.info("👋 Agent College Worker Shutting Down.")

if __name__ == "__main__":
    asyncio.run(main())
