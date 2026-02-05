
import os
import shutil
import sys
import subprocess
import time
from pathlib import Path

# Paths
REPO_ROOT = "/home/kjdragan/lrepos/universal_agent"
GLOBAL_MEMORY_DIR = os.path.join(REPO_ROOT, "memory")
GLOBAL_MEMORY_FILE = os.path.join(GLOBAL_MEMORY_DIR, "MEMORY.md")
BACKUP_DIR = os.path.join(REPO_ROOT, "memory_backup_test")

def backup_global_memory():
    if os.path.exists(GLOBAL_MEMORY_DIR):
        if os.path.exists(BACKUP_DIR):
            shutil.rmtree(BACKUP_DIR)
        shutil.copytree(GLOBAL_MEMORY_DIR, BACKUP_DIR)
        print(f"✅ Backed up global memory to {BACKUP_DIR}")
    else:
        print("⚠️ No global memory to backup.")

def restore_global_memory():
    if os.path.exists(BACKUP_DIR):
        if os.path.exists(GLOBAL_MEMORY_DIR):
            shutil.rmtree(GLOBAL_MEMORY_DIR)
        shutil.copytree(BACKUP_DIR, GLOBAL_MEMORY_DIR)
        shutil.rmtree(BACKUP_DIR)
        print(f"✅ Restored global memory from {BACKUP_DIR}")
    else:
        print("⚠️ No backup found to restore.")

def run_test():
    print("🚀 Starting Global Memory Sync Verification...")
    
    # 1. Backup
    backup_global_memory()
    
    try:
        # 2. Seed Global Memory
        os.makedirs(GLOBAL_MEMORY_DIR, exist_ok=True)
        seed_marker = f"Global Marker {time.time()}"
        with open(GLOBAL_MEMORY_FILE, "w") as f:
            f.write(f"# Core Memory\n\n## [TEST_MARKER]\n{seed_marker}\n")
        print(f"🌱 Seeded global memory with: {seed_marker}")
        
        # 3. Run Agent (Short Session)
        # We'll use a prompt that asks the agent to READ core memory and then WRITE to it.
        # But for 'main.py' logic, we just need to ensure the files move.
        # We can just check the workspace directory after the run if we want, 
        # but to test persistence we need the agent to WRITE something.
        # 
        # Let's run a simple prompt: "Please add 'My favorite number is 42' to my core memory using the tool."
        prompt = "Add 'User favorite number is 42' to my core memory. Do it now."
        
        print("🏃 Running Agent...")
        # Current directory must be universal_agent root for imports to work nicely
        cmd = [
            sys.executable, "-m", "universal_agent.main",
            "--harness", prompt,
            "--interview-auto", "1", # Bypass interview if triggered
            # "--auto-approve" not needed for harness usually, or handled internally?
            # main.py args don't show auto-approve, maybe it's env var or harness default?
            # Harness mode usually is autonomous.
        ]
        
        env = os.environ.copy()
        env["UA_MEMORY_ENABLED"] = "1"
        env["UA_MEMORY_FLUSH_ON_EXIT"] = "1"
        
        # We need to capture the output to find the session workspace
        result = subprocess.run(
            cmd, 
            cwd=REPO_ROOT,
            env=env, 
            capture_output=True, 
            text=True
        )
        
        if result.returncode != 0:
            print("❌ Agent run failed!")
            print(result.stdout)
            print(result.stderr)
            return
            
        print("✅ Agent run completed.")
        
        # 4. Check Persistence
        # Read GLOBAL_MEMORY_FILE again
        with open(GLOBAL_MEMORY_FILE, "r") as f:
            content = f.read()
            
        print(f"📄 New Global Memory Content:\n{content}")
        
        if "favorite number is 42" in content or "User favorite number is 42" in content:
            print("✅ SUCCESS: Agent wrote to session memory, and it synced back to Global!")
        else:
            print("❌ FAILURE: New memory NOT found in Global storage.")
            # Check debug info
            print("--- STDOUT ---")
            print(result.stdout[-2000:])
            
    finally:
        # 5. Restore
        restore_global_memory()

if __name__ == "__main__":
    run_test()
