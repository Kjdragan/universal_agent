
import os
import sys
from universal_agent.agent_setup import AgentSetup
from universal_agent.prompt_assets import discover_skills

# Mock environment
os.environ["UA_ARTIFACTS_DIR"] = "/tmp/ua_artifacts"
os.environ["CURRENT_SESSION_WORKSPACE"] = "/tmp/ua_session"

class MockAgentSetup(AgentSetup):
    def __init__(self):
        # Bypass real init entirely
        self.src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # Adjust src_dir up to project root relative to this script location?
        # Script is in lrepos/universal_agent/scripts/
        # self.src_dir should be lrepos/universal_agent
        self.src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        self.enable_skills = True
        self._discovered_skills = []
        self._discovered_apps = []
        
        # Discover skills using our fixed function
        print("🔍 Discovering skills...")
        self._discovered_skills = discover_skills()
        print(f"✅ Found {len(self._discovered_skills)} skills")

def main():
    print("🚀 Regenerating Capabilities (Mock Mode)...")
    
    setup = MockAgentSetup()
    
    # Trigger generation using the inherited method
    setup._generate_capabilities_doc()
    
    # Verify
    cap_path = os.path.join(setup.src_dir, "src", "universal_agent", "prompt_assets", "capabilities.md")
    print(f"Checking {cap_path}...")
    
    if os.path.exists(cap_path):
        print("✅ File created!")
        with open(cap_path, "r") as f:
            content = f.read()
            if "- No skills discovered." not in content:
                print("✅ SUCCESS: 'No skills discovered' is GONE.")
                # Show first few skills
                import re
                skills = re.findall(r"- \*\*(.*?)\*\*", content)
                print(f"Skills in file: {skills[:5]}...")
            else:
                 print("❌ FAILED: Still says 'No skills discovered'.")
    else:
        print("❌ FAILED: File not found.")

if __name__ == "__main__":
    main()
