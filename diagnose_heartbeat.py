
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

# Target Session
SESSION_ID = "session_20260204_232844_10dadb8e"
WORKSPACE = Path(f"AGENT_RUN_WORKSPACES/{SESSION_ID}").resolve()

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def check_paths():
    log("=== 1. Path Verification ===")
    
    # 1. Workspace
    if not WORKSPACE.exists():
        log(f"❌ Workspace not found: {WORKSPACE}")
        return False
    log(f"✅ Workspace exists: {WORKSPACE}")
    
    # 2. HEARTBEAT.md
    hb_file = WORKSPACE / "HEARTBEAT.md"
    if not hb_file.exists():
        log(f"❌ HEARTBEAT.md MISSING in workspace!")
        # Check if it exists in memory/ subdir (old bug)
        if (WORKSPACE / "memory" / "HEARTBEAT.md").exists():
            log(f"⚠️  Found HEARTBEAT.md in 'memory/' subdir, but Service looks in root!")
    else:
        content = hb_file.read_text()
        log(f"✅ HEARTBEAT.md found ({len(content)} bytes)")
        if "11:30" in content:
            log("   - Contains '11:30' trigger")
        if "CRITICAL" in content:
            log("   - Contains CRITICAL priority")

    # 3. Script
    script_path = Path("scripts/generate_official_docs.py").resolve()
    if not script_path.exists():
        log(f"❌ Script MISSING: {script_path}")
    else:
        log(f"✅ Script found: {script_path}")

    # 4. State
    state_path = WORKSPACE / "heartbeat_state.json"
    if not state_path.exists():
        log("⚠️ No heartbeat_state.json (Might have been wiped)")
    else:
        try:
            state = json.loads(state_path.read_text())
            last_run = state.get("last_run", 0)
            last_run_str = datetime.fromtimestamp(last_run).strftime('%H:%M:%S')
            log(f"📄 State Loaded:")
            log(f"   - Last Run: {last_run_str} ({int(time.time() - last_run)}s ago)")
            log(f"   - Last Summary: {state.get('last_summary', {})}")
        except Exception as e:
            log(f"❌ Corrupt State File: {e}")

def check_execution_capability():
    log("\n=== 2. Execution Capability ===")
    
    # Can we run uv?
    try:
        ver = subprocess.check_output(["uv", "--version"], text=True).strip()
        log(f"✅ uv installed: {ver}")
    except Exception as e:
        log(f"❌ uv check failed: {e}")

    # Can we run the script?
    script = "scripts/generate_official_docs.py"
    if os.path.exists(script):
        log(f"🧪 Dry-run of script: {script}")
        # We don't want to actually generate everything, maybe just check if it compiles
        try:
            subprocess.check_call([sys.executable, "-m", "py_compile", script])
            log(f"✅ Script syntax is valid")
        except Exception as e:
            log(f"❌ Script syntax error: {e}")

def check_logs_for_errors():
    log("\n=== 3. Log Analysis ===")
    log_file = WORKSPACE / "run.log"
    if not log_file.exists():
        log("❌ run.log missing")
        return

    log("Scanning last 50 lines of run.log for errors...")
    try:
        lines = log_file.read_text().splitlines()[-50:]
        found_err = False
        for line in lines:
            if "ERROR" in line or "Exception" in line or "Traceback" in line:
                log(f"🚩 {line}")
                found_err = True
        if not found_err:
            log("✅ No obvious errors in tail of log")
    except Exception as e:
        log(f"❌ Failed to read log: {e}")

if __name__ == "__main__":
    check_paths()
    check_execution_capability()
    check_logs_for_errors()
