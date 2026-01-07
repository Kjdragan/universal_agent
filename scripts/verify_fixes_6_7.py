import sys
import os

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))

try:
    from universal_agent.main import TRUNCATION_THRESHOLD
    print(f"✅ Imported TRUNCATION_THRESHOLD: {TRUNCATION_THRESHOLD}")
except ImportError as e:
    print(f"❌ Failed to import TRUNCATION_THRESHOLD: {e}")

try:
    from universal_agent.durable.state import update_run_tokens
    print(f"✅ Imported update_run_tokens")
except ImportError as e:
    print(f"❌ Failed to import update_run_tokens: {e}")

try:
    import re
    test_output = "Some text\n<promise>TASK_COMPLETE</promise>\nMore text"
    match = re.search(r'<promise>(.*?)</promise>', test_output, re.DOTALL)
    if match and match.group(1) == "TASK_COMPLETE":
        print("✅ Regex validation works")
    else:
        print("❌ Regex validation failed")
except Exception as e:
    print(f"❌ Regex validation error: {e}")
