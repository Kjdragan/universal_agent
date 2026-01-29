import pytest
import sys
import os
import pexpect

@pytest.mark.asyncio
async def test_fast_path_context_retention_cli():
    """
    Pexpect test:
    1. Run main.py
    2. Send Complex Query
    3. Accept Latch
    4. Send Simple Query
    5. Verify Context
    """
    
    test_file = "amnesia_test.txt"
    if os.path.exists(test_file):
        os.remove(test_file)

    cmd = f"{sys.executable} -m universal_agent.main"
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    
    # Spawn process
    child = pexpect.spawn(cmd, env=env, encoding='utf-8', timeout=60)
    
    try:
        # 1. Wait for startup
        child.expect("🤖 Enter your request")
        
        # 2. Send Complex Query
        child.sendline(f"Create a file named {test_file} with content 'context check'.")
        
        # 3. Wait for Latch
        child.expect("Keep session history\? \(Y/n\)")
        
        # 4. Accept Latch
        child.sendline("Y")
        
        # 5. Wait for Next Prompt (Previous turn complete)
        child.expect("🤖 Enter your request")
        
        # 6. Send Simple Query (Fast Path)
        # We try to make it look very simple to trigger "SIMPLE" classification.
        child.sendline("What is the filename?")
        
        # 7. Expect answer
        # It might print "⚡ Direct Answer (Fast Path):"
        child.expect("⚡ Direct Answer")
        
        # 8. Expect the filename in the output BEFORE the next prompt
        index = child.expect([test_file, "🤖 Enter your request"])
        if index == 0:
            print("✅ Context Preserved! Found filename.")
        else:
            print(f"❌ Context Lost! Output: {child.before}")
            pytest.fail("Context Lost! Filename not found in response.")
            
    except pexpect.exceptions.TIMEOUT:
        print(f"❌ Timeout! Buffer: {child.before}")
        with open("pexpect_debug.log", "w") as f:
            f.write(str(child.before))
        pytest.fail("Test timed out")
    except pexpect.exceptions.EOF:
        print(f"❌ EOF! Buffer: {child.before}")
        with open("pexpect_debug.log", "w") as f:
            f.write(str(child.before))
        pytest.fail("Process exited unexpectedly")
    finally:
        child.close()
        if os.path.exists(test_file):
            os.remove(test_file)

if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(test_fast_path_context_retention_cli())
        print("\nTest Finished Successfully.")
    except Exception as e:
        print(f"\nTest Failed: {e}")
        sys.exit(1)
