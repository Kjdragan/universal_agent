import sys
import os

# Add src to path
sys.path.append(os.path.abspath("/home/kjdragan/lrepos/universal_agent/src"))
sys.path.append(os.path.abspath("/home/kjdragan/lrepos/universal_agent/skills/agent_interview"))

try:
    from interview_tool import ask_user, finish_interview
    print("✅ Successfully imported interview_tool")
    print(f"ask_user: {ask_user}")
    print(f"finish_interview: {finish_interview}")
except Exception as e:
    print(f"❌ Failed to import interview_tool: {e}")
    sys.exit(1)
