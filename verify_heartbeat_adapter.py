
import asyncio
import logging
from universal_agent.bot.heartbeat_adapter import BotConnectionAdapter

# Mock Logging
logging.basicConfig(level=logging.DEBUG)

async def test_heartbeat_adapter():
    print("🚀 Testing BotConnectionAdapter...")
    
    # 1. Mock Callback
    received_messages = []
    async def mock_send_callback(user_id: str, text: str):
        print(f"📨 Mock Send: To={user_id}, Text='{text}'")
        received_messages.append((user_id, text))

    adapter = BotConnectionAdapter(mock_send_callback)
    
    # 2. Test Registration
    session_id = "tg_12345"
    adapter.register_active_session(session_id)
    assert session_id in adapter.session_connections
    print("✅ Session Registration Verified")
    
    # 3. Test Broadcast (Valid Heartbeat)
    event = {
        "type": "heartbeat_summary",
        "data": {
            "text": "Hello form proactive agent!",
            "ok_only": False
        }
    }
    await adapter.broadcast(session_id, event)
    
    assert len(received_messages) == 1
    assert received_messages[0][0] == "12345"
    assert received_messages[0][1] == "Hello form proactive agent!"
    print("✅ Broadcast Delivery Verified")
    
    # 4. Test Broadcast (Ignored Event)
    await adapter.broadcast(session_id, {"type": "other_event", "data": {}})
    assert len(received_messages) == 1
    print("✅ Ignored Event Verified")
    
    # 5. Test Unregister
    adapter.unregister_active_session(session_id)
    assert session_id not in adapter.session_connections
    print("✅ Unregister Verified")
    
    print("🎉 All Heartbeat Adapter Tests Passed!")

if __name__ == "__main__":
    asyncio.run(test_heartbeat_adapter())
