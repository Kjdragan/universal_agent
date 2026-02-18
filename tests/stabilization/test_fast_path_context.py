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
    child = pexpect.spawn(cmd, env=env, encoding='utf-8', timeout=180)
    
    try:
        # 1. Wait for startup
        child.expect("🤖 Enter your request")
        
        # 2. Send Complex Query
        child.sendline(f"Create a file named {test_file} with content 'context check'.")
        
        # 3. Handle both legacy and current UX:
        # - legacy flow asks for latch confirmation
        # - current flow can continue directly to next prompt
        idx = child.expect(
            [r"Keep session history\? \(Y/n\)", r"🤖 Enter your request"],
            timeout=180,
        )
        if idx == 0:
            child.sendline("Y")
            child.expect("🤖 Enter your request", timeout=180)

        # 4. Ask a minimal context retrieval query
        child.sendline("What is the filename I just asked you to create?")

        # 5. Wait for turn completion and inspect response body
        child.expect("🤖 Enter your request", timeout=180)
        response = child.before

        if test_file in response:
            print("✅ Context Preserved! Found filename.")
        else:
            print(f"❌ Context Lost! Output: {response}")
            pytest.fail("Context lost: filename not found in second-turn response.")
            
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
