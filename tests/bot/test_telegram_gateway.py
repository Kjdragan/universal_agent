import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
from universal_agent.bot.agent_adapter import AgentAdapter
from universal_agent.gateway import GatewaySession, GatewayResult

class TestTelegramGateway(unittest.TestCase):
    def setUp(self):
        self.adapter = AgentAdapter()
        
    async def async_test(self, coro):
        return await coro
        
    def test_session_mapping(self):
        """Verify user_id is mapped to tg_{user_id}."""
        async def run():
            # Mock Gateway
            mock_gateway = AsyncMock()
            self.adapter.gateway = mock_gateway
            self.adapter.initialized = True
            
            # Setup resume failure to force create
            mock_gateway.resume_session.side_effect = ValueError("Not found")
            mock_gateway.create_session.return_value = GatewaySession(
                session_id="tg_12345", 
                user_id="telegram_12345", 
                workspace_dir="/tmp/ws"
            )
            
            session = await self.adapter._get_or_create_session("12345")
            
            # Verify resume attempt
            mock_gateway.resume_session.assert_called_with("tg_12345")
            
            # Verify create attempt
            mock_gateway.create_session.assert_called_with(
                user_id="telegram_12345", 
                workspace_dir=None
            )
            
            self.assertEqual(session.session_id, "tg_12345")
            
        asyncio.run(run())

    def test_execute_flow(self):
        """Verify execution flow sends request to gateway."""
        async def run():
            # 1. Setup Adapter and Mock Gateway
            self.adapter.gateway = AsyncMock()
            self.adapter.initialized = True
            
            # Mock Session
            session = GatewaySession(session_id="tg_user1", user_id="user1", workspace_dir="/tmp")
            self.adapter.gateway.resume_session.return_value = session
            
            # Mock Result
            expected_result = GatewayResult(
                response_text="Hello from Gateway",
                tool_calls=1,
                trace_id="trace_123"
            )
            self.adapter.gateway.run_query.return_value = expected_result
            
            # 2. Start Actor Loop in background
            self.adapter._shutdown_event.clear()
            worker_task = asyncio.create_task(self.adapter._client_actor_loop())
            self.adapter.worker_task = worker_task

            # 3. Create Request
            future = asyncio.Future()
            req = MagicMock()
            req.prompt = "Hello"
            req.user_id = "user1"
            req.workspace_dir = None
            req.reply_future = future
            
            # 4. Enqueue
            await self.adapter.request_queue.put(req)
            
            # 5. Wait for result
            result = await asyncio.wait_for(future, timeout=2.0)
            
            # 6. Verify
            self.assertEqual(result.response_text, "Hello from Gateway")
            self.assertEqual(result.trace_id, "trace_123")
            
            # 7. Cleanup
            await self.adapter.shutdown()
            
        asyncio.run(run())

if __name__ == "__main__":
    unittest.main()
