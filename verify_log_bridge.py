
import asyncio
import sys
import os

# Add src to path
sys.path.append(os.path.abspath("src"))

from universal_agent.agent_core import AgentEvent, EventType
from mcp_server import mcp_log, set_mcp_log_callback

async def test_log_bridging():
    print("🧪 Testing Log Bridging System...")
    
    event_queue = asyncio.Queue()
    
    # 1. Define the bridge callback (logic copied from agent_core.py)
    def bridge_log_to_queue(message: str, level: str, prefix: str):
        event = AgentEvent(
            type=EventType.STATUS,
            data={
                "status": message,
                "level": level,
                "prefix": prefix,
                "is_log": True,
            }
        )
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(event_queue.put_nowait, event)
            print(f"   -> Pushed event to queue: [{level}] {message}")
        except Exception as e:
            print(f"Error pushing to queue: {e}")

    # 2. Register callback
    print("   Registering callback...")
    set_mcp_log_callback(bridge_log_to_queue)
    
    # 3. Simulate Backend Logs
    print("   Simulating mcp_log calls...")
    mcp_log("Test Info Log", level="INFO", prefix="[TEST]")
    mcp_log("Test Debug Log", level="DEBUG", prefix="[TEST]")
    
    # 4. Verify Queue
    print("   Verifying queue contents...")
    
    # Allow a moment for the loop to process call_soon_threadsafe if needed, 
    # but since mcp_log is sync, the callback runs sync, and put_nowait is sync.
    # We just need to await get()
    
    try:
        event1 = await asyncio.wait_for(event_queue.get(), timeout=1.0)
        print(f"✅ Received Event 1: {event1.data}")
        assert event1.data["status"] == "Test Info Log"
        assert event1.data["level"] == "INFO"
        assert event1.data["is_log"] is True
        
        # Note: DEBUG log might be filtered by UA_LOG_LEVEL env var if set to INFO
        # forcing it here for the test context might require checking default
        
        # Let's check if we get the second one
        if not event_queue.empty():
            event2 = event_queue.get_nowait()
            print(f"✅ Received Event 2: {event2.data}")
    except asyncio.TimeoutError:
        print("❌ Timeout waiting for event!")
        return
        
    print("\n🎉 Backend Log Bridging Verification PASSED!")

if __name__ == "__main__":
    asyncio.run(test_log_bridging())
