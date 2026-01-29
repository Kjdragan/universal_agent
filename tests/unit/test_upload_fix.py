#!/usr/bin/env python3
"""
Quick test of the fixed upload_to_composio function via MCP.
"""
import os
import sys
import json
from datetime import datetime

# Add paths
sys.path.insert(0, os.path.dirname(__file__) + "/../src")
from dotenv import load_dotenv
load_dotenv()

# Import the fixed function directly
from mcp_server import upload_to_composio

def main():
    print("="*60)
    print("TEST: Fixed upload_to_composio Function")
    print("="*60)
    
    # Create test file
    test_path = "/tmp/upload_test_fix.md"
    content = f"# Upload Test\nCreated: {datetime.now().isoformat()}\nThis tests the fixed upload_to_composio function."
    with open(test_path, "w") as f:
        f.write(content)
    print(f"✅ Created test file: {test_path} ({len(content)} bytes)")
    
    # Call the function
    print("\n--- Calling upload_to_composio ---")
    result = upload_to_composio(test_path)
    
    print(f"\n--- Result ---")
    print(result)
    
    # Parse and check
    try:
        data = json.loads(result)
        if "error" in data:
            print(f"\n❌ FAILED: {data['error']}")
            return 1
        elif "s3key" in data:
            print(f"\n✅ SUCCESS!")
            print(f"   S3 Key: {data['s3key']}")
            print(f"   S3 URL: {data.get('s3_url', 'N/A')}")
            print(f"   Remote Path: {data.get('remote_path', 'N/A')}")
            return 0
        else:
            print(f"\n⚠️ UNCLEAR: {data}")
            return 1
    except json.JSONDecodeError:
        print(f"\n❌ FAILED: Could not parse result as JSON")
        return 1

if __name__ == "__main__":
    sys.exit(main())
