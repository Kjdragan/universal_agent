import os
import sys
import shutil
from dotenv import load_dotenv

# Add src to path
sys.path.append(os.path.abspath("src"))

from tools.workbench_bridge import WorkbenchBridge


def run_poc():
    print("--- Starting Workbench Bridge POC ---\n")

    # 1. Setup Simulation Environment
    bridge = WorkbenchBridge()
    remote_fs = bridge.simulated_remote_dir
    local_wk = os.path.abspath("tests/local_agent_workspace")

    if os.path.exists(remote_fs):
        shutil.rmtree(remote_fs)
    if os.path.exists(local_wk):
        shutil.rmtree(local_wk)

    os.makedirs(remote_fs, exist_ok=True)
    os.makedirs(local_wk, exist_ok=True)

    # 2. Mimic "Remote State" (e.g., massive log file on server)
    remote_log_path = "var/logs/app.log"
    full_remote_path = os.path.join(remote_fs, remote_log_path)
    os.makedirs(os.path.dirname(full_remote_path), exist_ok=True)

    print(f"🖥️  REMOTE: Generating logs at {remote_log_path}...")
    with open(full_remote_path, "w") as f:
        f.write(
            "ERROR: Database connection failed\nINFO: Service started\nWARN: Memory High\nERROR: Timeout"
        )

    # 3. The Local Agent "Thinks" -> Needs to analyze logs
    print("\n🤖 AGENT: 'I need to analyze the logs locally to find errors.'")

    # 4. BRIDGE DOWNLOAD
    local_log_path = os.path.join(local_wk, "analyzed_logs.txt")  # Saving to local name
    bridge.download(remote_path=remote_log_path, local_path=local_log_path)

    # 5. Local Intelligence (The "Brain")
    print(f"\n🧠 LOCAL: Processing {local_log_path} with advanced reasoning...")
    with open(local_log_path, "r") as f:
        content = f.read()

    # Simulate complex analysis (finding ERROR lines)
    errors = [line for line in content.splitlines() if "ERROR" in line]
    report_content = f"# Analysis Report\nFound {len(errors)} errors:\n" + "\n".join(
        errors
    )

    local_report_path = os.path.join(local_wk, "report.md")
    with open(local_report_path, "w") as f:
        f.write(report_content)
    print("   ✅ Report generated locally.")

    # 6. BRIDGE UPLOAD
    remote_dest_path = "home/user/final_report.md"
    print("\n🤖 AGENT: 'Uploading report back to workbench...'")
    bridge.upload(local_path=local_report_path, remote_path=remote_dest_path)

    # 7. Verification
    print("\n--- Verification ---")
    final_remote = os.path.join(remote_fs, remote_dest_path)
    if os.path.exists(final_remote):
        print(f"✅ Success! File exists on Remote: {remote_dest_path}")
        with open(final_remote, "r") as f:
            print(f"📄 Content:\n{f.read()}")
    else:
        print("❌ Failed: File not found on remote.")


if __name__ == "__main__":
    run_poc()
