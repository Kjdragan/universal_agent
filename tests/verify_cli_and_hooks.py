
import asyncio
import os
import shutil
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add src to path
sys.path.append(str(Path(__file__).resolve().parent.parent / "src"))

from universal_agent.urw.integration import UniversalAgentAdapter
from universal_agent.urw.state import Task

# Setup logging
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_cli")

async def main():
    load_dotenv()
    
    # Setup workspace
    workspace_dir = Path("AGENT_RUN_WORKSPACES/verify_cli_hooks")
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Workspace: {workspace_dir}")
    
    # Initialize Adapter
    adapter = UniversalAgentAdapter({"verbose": True})
    
    # Create a task that forces > 5 tool calls
    # We ask it to run 6 separate bash commands.
    task = Task(
        id="verify_hook",
        title="Verify Skill Hook",
        description=(
            "Execute the following 6 bash commands strictly sequentially, one by one. "
            "Do NOT combine them. You MUST use the Bash tool 6 times.\n"
            "1. echo 'Command 1'\n"
            "2. echo 'Command 2'\n"
            "3. echo 'Command 3'\n"
            "4. echo 'Command 4'\n"
            "5. echo 'Command 5'\n"
            "6. echo 'Command 6'\n"
            "Finally, verify they ran."
        )
    )
    
    logger.info("Starting agent execution...")
    result = await adapter.execute_task(task, "Verification Context", workspace_dir)
    
    logger.info(f"Execution successful: {result.success}")
    logger.info("Checking for skill candidate log...")
    
    # Check for skill_candidates.log in workspace or root
    log_file = workspace_dir / "skill_candidates.log"
    if log_file.exists():
        content = log_file.read_text()
        print("\n--- skill_candidates.log ---")
        print(content)
        print("----------------------------")
        if "Potential Skill Candidate Detected" in content:
            print("✅ VERIFIED: Skill candidate hook fired!")
        else:
            print("❌ FAILURE: Log file exists but message missing.")
    else:
        # Fallback check in current dir
        fallback = Path("skill_candidates.log")
        if fallback.exists():
            content = fallback.read_text()
            print("\n--- skill_candidates.log (root) ---")
            print(content)
            if "Potential Skill Candidate Detected" in content:
                print("✅ VERIFIED: Skill candidate hook fired (in root)!")
            else:
                 print("❌ FAILURE: Log file exists in root but message missing.")
        else:
            print("❌ FAILURE: skill_candidates.log NOT found.")

if __name__ == "__main__":
    asyncio.run(main())
