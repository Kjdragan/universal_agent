
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
logger = logging.getLogger("verify_complex")

async def main():
    load_dotenv()
    
    # Setup workspace
    workspace_dir = Path("AGENT_RUN_WORKSPACES/verify_complex_trigger")
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Workspace: {workspace_dir}")
    
    # Initialize Adapter
    adapter = UniversalAgentAdapter({"verbose": True})
    
    # Create a complex task that naturally forces multiple tool calls
    # Task: Analyze source code structure and content.
    task = Task(
        id="verify_complex_hook",
        title="Complex Source Code Analysis",
        description=(
            "Perform the following source code analysis in the 'src' directory of this repository:\n"
            "1. Find all Python files recursively in 'src/universal_agent'.\n"
            "2. For each file, calculate the number of lines.\n"
            "3. Identify the top 3 largest files by line count.\n"
            "4. For each of these 3 files, read and print the first 10 lines to the console.\n"
            "5. Finally, save a summary report of these 3 files (name and line count) to 'largest_files_summary.txt'.\n"
            "Do NOT use a single python script to do all of this. Use Bash commands (find, wc, sort, head) or individual steps to demonstrate your reasoning and tool usage capabilities."
        )
    )
    
    logger.info("Starting agent execution for complex task...")
    result = await adapter.execute_task(task, "Verification Context", workspace_dir)
    
    logger.info(f"Execution successful: {result.success}")
    logger.info("Checking for skill candidate log in central directory...")
    
    # Check for log in <repo_root>/logs/skill_candidates/
    repo_root = Path(__file__).resolve().parent.parent
    log_dir = repo_root / "logs" / "skill_candidates"
    
    found_log = False
    content = ""
    log_path = None
    
    if log_dir.exists():
        # Find the most recent file
        files = list(log_dir.glob("candidate_*.log"))
        if files:
            latest_file = max(files, key=os.path.getctime)
            content = latest_file.read_text()
            found_log = True
            log_path = latest_file
            print(f"\n--- Found log: {latest_file.name} ---")
        else:
            print(f"❌ Log directory exists but is empty: {log_dir}")
    else:
        print(f"❌ Log directory found: {log_dir}")
        
    if found_log:
        print(content)
        print("----------------------------")
        if "Potential Skill Candidate Detected" in content:
            print("✅ VERIFIED: Skill candidate hook fired and logged to central dir!")
        else:
            print("❌ FAILURE: Log file exists but message missing.")
    else:
        print("❌ FAILURE: Central log file NOT found.")

if __name__ == "__main__":
    asyncio.run(main())
