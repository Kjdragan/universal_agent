import sys
import time
import subprocess
import signal
import os
from datetime import datetime

WATCHDOG_LOG = "watchdog.log"

def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] [WATCHDOG] {msg}"
    print(entry)
    with open(WATCHDOG_LOG, "a") as f:
        f.write(entry + "\n")

def monitor_process(cmd):
    log(f"Starting process: {' '.join(cmd)}")
    
    # Start the process
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1 # Line buffered
    )
    
    log(f"Process started with PID: {process.pid}")
    
    last_output_time = time.time()
    
    try:
        # Monitor loop
        while True:
            # Check if process is still running
            retcode = process.poll()
            if retcode is not None:
                log(f"Process exited with return code: {retcode}")
                if retcode != 0:
                    log("CRITICAL: Process crashed or failed!")
                else:
                    log("Process completed successfully.")
                break
            
            # Read output (non-blocking if possible, but here using readline which blocks)
            # To avoid blocking forever, we should use a separate thread or select, 
            # but for simplicity we'll just read line by line.
            line = process.stdout.readline()
            if line:
                print(line, end='') # Stream to stdout
                last_output_time = time.time()
            else:
                # No output, check if hung?
                # For now just sleep small amount
                time.sleep(0.1)
                
            # Heartbeat check (optional: check if trace.json updated?)
            
    except KeyboardInterrupt:
        log("Watchdog interrupted by user. Terminating process...")
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        log("Process terminated.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 watchdog.py <command> [args...]")
        sys.exit(1)
        
    cmd = sys.argv[1:]
    monitor_process(cmd)
