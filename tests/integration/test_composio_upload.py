#!/usr/bin/env python3
"""
Standalone test script to figure out reliable file upload to Composio cloud.
This tests various approaches to find what actually works.

Run: cd /home/kjdragan/lrepos/universal_agent && uv run tests/test_composio_upload.py
"""

import os
import sys
import json
import base64
import pytest
from dotenv import load_dotenv

pytest.skip(
    "Composio upload tests are pending fixture setup; skipping for now.",
    allow_module_level=True,
)

# Load environment
load_dotenv()

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from composio import Composio

# Test file content
TEST_FILE_CONTENT = """# Test Upload File
This is a test file created at {timestamp}
to verify Composio upload functionality.
"""

def create_test_file():
    """Create a local test file"""
    from datetime import datetime
    content = TEST_FILE_CONTENT.format(timestamp=datetime.now().isoformat())
    filepath = "/tmp/composio_upload_test.md"
    with open(filepath, "w") as f:
        f.write(content)
    print(f"✅ Created test file: {filepath} ({len(content)} bytes)")
    return filepath, content


def test_method_1_direct_filetool_upload(client, local_path):
    """
    Method 1: Use FILETOOL_UPLOAD_FILE if it exists
    """
    print("\n" + "="*60)
    print("METHOD 1: FILETOOL_UPLOAD_FILE")
    print("="*60)
    
    try:
        with open(local_path, "rb") as f:
            content = f.read()
        
        # Try FILETOOL_UPLOAD_FILE
        resp = client.tools.execute(
            slug="FILETOOL_UPLOAD_FILE",
            arguments={
                "file_path": "/home/user/test_method1.md",
                "content": base64.b64encode(content).decode("utf-8"),
            },
            dangerously_skip_version_check=True,
        )
        
        if hasattr(resp, "model_dump"):
            resp = resp.model_dump()
        
        print(f"Response: {json.dumps(resp, indent=2)}")
        return resp
    except Exception as e:
        print(f"❌ Failed: {e}")
        return None


def test_method_2_remote_workbench_base64(client, local_path, session_id=None):
    """
    Method 2: Use COMPOSIO_REMOTE_WORKBENCH with base64-encoded Python script
    """
    print("\n" + "="*60)
    print("METHOD 2: COMPOSIO_REMOTE_WORKBENCH + Base64 Python")
    print("="*60)
    
    try:
        with open(local_path, "rb") as f:
            content = f.read()
        
        encoded = base64.b64encode(content).decode("utf-8")
        remote_path = "/home/user/test_method2.md"
        
        # Python script to decode and write
        script = (
            "import os, base64\n"
            f"path = '{remote_path}'\n"
            "os.makedirs(os.path.dirname(path), exist_ok=True)\n"
            f"with open(path, 'wb') as f: f.write(base64.b64decode('{encoded}'))\n"
            "print(f'SUCCESS: Wrote file to {path}')\n"
            "print(f'SIZE: {os.path.getsize(path)} bytes')"
        )
        
        payload = {"code_to_execute": script}
        if session_id:
            payload["session_id"] = session_id
        
        resp = client.tools.execute(
            slug="COMPOSIO_REMOTE_WORKBENCH",
            arguments=payload,
            dangerously_skip_version_check=True,
        )
        
        if hasattr(resp, "model_dump"):
            resp = resp.model_dump()
        
        print(f"Response: {json.dumps(resp, indent=2)}")
        
        # Verify it exists
        print("\n--- Verifying file exists ---")
        verify = client.tools.execute(
            slug="COMPOSIO_REMOTE_WORKBENCH",
            arguments={
                "code_to_execute": f"import os; print('EXISTS:', os.path.exists('{remote_path}')); print('FILES:', os.listdir('/home/user/'))",
                "session_id": session_id
            } if session_id else {
                "code_to_execute": f"import os; print('EXISTS:', os.path.exists('{remote_path}')); print('FILES:', os.listdir('/home/user/'))"
            },
            dangerously_skip_version_check=True,
        )
        if hasattr(verify, "model_dump"):
            verify = verify.model_dump()
        print(f"Verify Response: {json.dumps(verify, indent=2)}")
        
        return resp
    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_method_3_s3_upload_direct(client, local_path, session_id=None):
    """
    Method 3: Write file to workbench, then use upload_local_file helper for S3
    """
    print("\n" + "="*60)
    print("METHOD 3: Workbench Write + S3 upload_local_file()")
    print("="*60)
    
    try:
        with open(local_path, "rb") as f:
            content = f.read()
        
        encoded = base64.b64encode(content).decode("utf-8")
        remote_path = "/home/user/test_method3.md"
        
        # Step 1: Write file to workbench
        write_script = (
            "import os, base64\n"
            f"path = '{remote_path}'\n"
            "os.makedirs(os.path.dirname(path), exist_ok=True)\n"
            f"with open(path, 'wb') as f: f.write(base64.b64decode('{encoded}'))\n"
            "print(f'WRITE_SUCCESS: {path}')"
        )
        
        payload = {"code_to_execute": write_script}
        if session_id:
            payload["session_id"] = session_id
            
        resp1 = client.tools.execute(
            slug="COMPOSIO_REMOTE_WORKBENCH",
            arguments=payload,
            dangerously_skip_version_check=True,
        )
        if hasattr(resp1, "model_dump"):
            resp1 = resp1.model_dump()
        print(f"Step 1 (Write): {json.dumps(resp1, indent=2)}")
        
        # Step 2: Use upload_local_file helper for S3
        s3_script = (
            f"result, error = upload_local_file('{remote_path}')\n"
            "if error:\n"
            "    print(f'S3_ERROR: {error}')\n"
            "else:\n"
            "    import json\n"
            "    print('S3_SUCCESS')\n"
            "    print(json.dumps(result))"
        )
        
        payload2 = {"code_to_execute": s3_script}
        if session_id:
            payload2["session_id"] = session_id
            
        resp2 = client.tools.execute(
            slug="COMPOSIO_REMOTE_WORKBENCH",
            arguments=payload2,
            dangerously_skip_version_check=True,
        )
        if hasattr(resp2, "model_dump"):
            resp2 = resp2.model_dump()
        print(f"Step 2 (S3): {json.dumps(resp2, indent=2)}")
        
        return resp1, resp2
    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_method_4_tool_router_session(client, local_path):
    """
    Method 4: Create a Tool Router session first, then use that for uploads
    """
    print("\n" + "="*60)
    print("METHOD 4: Tool Router Session + Upload")
    print("="*60)
    
    try:
        # Create a session via tool router
        session = client.tool_router.create()
        session_id = session.session_id if hasattr(session, 'session_id') else session.get('session_id')
        print(f"✅ Created Tool Router Session: {session_id}")
        
        # Now try upload with this session
        return test_method_3_s3_upload_direct(client, local_path, session_id)
        
    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_method_5_composio_multi_execute(client, local_path, session_id=None):
    """
    Method 5: Use COMPOSIO_MULTI_EXECUTE_TOOL with the internal file operations
    """
    print("\n" + "="*60)
    print("METHOD 5: COMPOSIO_MULTI_EXECUTE_TOOL")
    print("="*60)
    
    try:
        with open(local_path, "rb") as f:
            content = f.read()
        
        payload = {
            "tools": [
                {
                    "tool_slug": "FILETOOL_CREATE_FILE",
                    "arguments": {
                        "path": "/home/user/test_method5.md",
                        "content": content.decode("utf-8"),
                    }
                }
            ],
            "thought": "Creating test file for upload verification"
        }
        
        if session_id:
            payload["session_id"] = session_id
        
        resp = client.tools.execute(
            slug="COMPOSIO_MULTI_EXECUTE_TOOL",
            arguments=payload,
            dangerously_skip_version_check=True,
        )
        
        if hasattr(resp, "model_dump"):
            resp = resp.model_dump()
        
        print(f"Response: {json.dumps(resp, indent=2)}")
        return resp
    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    print("="*60)
    print("COMPOSIO UPLOAD TEST SUITE")
    print("="*60)
    
    # Initialize client
    api_key = os.environ.get("COMPOSIO_API_KEY")
    if not api_key:
        print("❌ COMPOSIO_API_KEY not set")
        return
    
    client = Composio(api_key=api_key)
    print(f"✅ Composio client initialized")
    
    # Create test file
    local_path, content = create_test_file()
    
    # Run tests
    results = {}
    
    # Method 1: Direct FILETOOL_UPLOAD_FILE
    results["method1"] = test_method_1_direct_filetool_upload(client, local_path)
    
    # Method 2: Remote Workbench + Base64
    results["method2"] = test_method_2_remote_workbench_base64(client, local_path)
    
    # Method 3: Workbench Write + S3 Helper (no session)
    results["method3_no_session"] = test_method_3_s3_upload_direct(client, local_path, session_id=None)
    
    # Method 4: With Tool Router Session
    results["method4"] = test_method_4_tool_router_session(client, local_path)
    
    # Method 5: Multi-Execute with FILETOOL_CREATE_FILE
    results["method5"] = test_method_5_composio_multi_execute(client, local_path)
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    for method, result in results.items():
        status = "✅ Success" if result else "❌ Failed"
        print(f"{method}: {status}")


if __name__ == "__main__":
    main()
