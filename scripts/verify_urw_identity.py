
import asyncio
import os
import sys

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))

from universal_agent.urw.integration import GatewayURWAdapter
from universal_agent.identity.resolver import resolve_user_id

async def verify_urw_identity():
    print("Locked & Loaded: Verifying URW Identity Integration")
    
    # 1. Verify Resolver
    user_id = resolve_user_id()
    print(f"Default User ID: {user_id}")
    
    # 2. Instantiate Wrapper
    config = {"gateway_url": None} # In-process
    try:
        adapter = GatewayURWAdapter(config)
        print("Adapter instantiated successfully")
        
        # 3. Check Session (Mocking workspace path)
        from pathlib import Path
        workspace = Path("/tmp/urw_test_workspace")
        workspace.mkdir(exist_ok=True)
        
        session = await adapter._ensure_session(workspace)
        print(f"Session Created: {session}")
        
        # Verify session user ID matches resolved ID? 
        # The session object might not expose it directly depending on implementation, 
        # but if this runs without "urw_harness" hardcode error or similar, that's a win.
        # We can inspect the session object if possible.
        
        print(f"Session User ID (if accessible): {getattr(session, 'user_id', 'N/A')}")
        
    except Exception as e:
        print(f"FAILED: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(verify_urw_identity())
