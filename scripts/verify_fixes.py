
import sys
import os

# Add src to path
sys.path.append(os.path.abspath("src"))

def test_hooks_initialization():
    print("Testing hooks initialization...")
    try:
        from universal_agent import hooks
        print(f"✅ hooks imported successfully.")
        
        # Check if _TOOL_EVENT_START_TS is defined
        if hasattr(hooks, "_TOOL_EVENT_START_TS"):
            print(f"✅ _TOOL_EVENT_START_TS is defined: {hooks._TOOL_EVENT_START_TS}")
        else:
            print("❌ _TOOL_EVENT_START_TS is NOT defined at module level.")
            return False
            
        # Call a function that uses it
        offset = hooks._tool_time_offset()
        print(f"✅ _tool_time_offset() returned: {offset}")
        
    except NameError as e:
        print(f"❌ NameError caught: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False
        
    print("✨ All tests passed!")
    return True

if __name__ == "__main__":
    if test_hooks_initialization():
        sys.exit(0)
    else:
        sys.exit(1)
