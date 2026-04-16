import subprocess, re
output = subprocess.check_output(["journalctl", "-u", "universal-agent-api.service", "--since", "13:00"]).decode("utf-8")
tracebacks = output.split("Traceback (most recent call last):")
if len(tracebacks) > 1:
    for tb in tracebacks[-3:]:
        lines = tb.split("\n")
        print("Traceback (most recent call last):")
        # Print first 2 and last 15 lines of the traceback
        print("\n".join(lines[:2]))
        print("...")
        print("\n".join(lines[-15:]))
        print("="*40)
