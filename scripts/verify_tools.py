
import sys
import os
import asyncio
import json

# Add src to path
sys.path.append(os.path.abspath("src"))

import mcp_server

def verify_tools():
    print("Verifying Tool Rationalization...")
    
    # 1. Verify append_to_file exists
    if hasattr(mcp_server, "append_to_file"):
        print("✅ append_to_file found in module")
        # Test functionality
        try:
            test_file = "test_append_verify.txt"
            if os.path.exists(test_file):
                os.remove(test_file)
                
            # Create file
            with open(test_file, "w") as f:
                f.write("Initial")
                
            # Append
            res = mcp_server.append_to_file(test_file, "|Appended")
            print(f"Append result: {res}")
            
            with open(test_file, "r") as f:
                content = f.read()
                
            if content == "Initial|Appended":
                print("✅ append_to_file functionality verified")
            else:
                print(f"❌ functionality failed. Content: {content}")
                sys.exit(1)
                
            os.remove(test_file)
            
        except Exception as e:
            print(f"❌ Exception testing append: {e}")
            sys.exit(1)
    else:
        print("❌ append_to_file MISSING in module")
        sys.exit(1)

    # 2. Verify write_local_file is GONE
    if hasattr(mcp_server, "write_local_file"):
        print("❌ write_local_file STILL PRESENT in module")
        sys.exit(1)
    else:
        print("✅ write_local_file correctly removed")

    # 3. Verify read_local_file is GONE
    if hasattr(mcp_server, "read_local_file"):
        print("❌ read_local_file STILL PRESENT in module")
        sys.exit(1)
    else:
        print("✅ read_local_file correctly removed")

if __name__ == "__main__":
    verify_tools()
