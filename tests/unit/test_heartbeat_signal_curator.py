import pytest
from unittest.mock import patch, MagicMock
from universal_agent.heartbeat_service import HeartbeatService, GatewaySession

@pytest.mark.asyncio
async def test_heartbeat_dispatches_curator_mission():
    from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
    
    # Mock everything out
    with patch("universal_agent.heartbeat_service.get_activity_db_path", return_value=":memory:"), \
         patch("universal_agent.heartbeat_service.connect_runtime_db") as mock_connect, \
         patch("universal_agent.tools.vp_orchestration.dispatch_vp_mission") as mock_dispatch:
         
        # Make the should_run_curation return True and get_pending_cards return items
        with patch("universal_agent.services.signal_curator.should_run_curation", return_value=True), \
             patch("universal_agent.services.signal_curator.get_pending_cards", return_value=[{"card_id": "1"}, {"card_id": "2"}]), \
             patch("universal_agent.services.signal_curator.record_curation_run") as mock_record:
             
            # Initialize minimal dependencies to avoid crashes
            mock_cm = MagicMock()
            mock_cm.broadcast = MagicMock()
            mock_gateway = MagicMock()
            mock_gateway.active_sessions = MagicMock()
            mock_gateway.invoke_agent = MagicMock()
            mock_gateway.invoke_agent_sync = MagicMock()
            service = HeartbeatService(
                gateway=mock_gateway,
                connection_manager=mock_cm,
            )
            
            # The actual test: trigger the block that checks curation
            session = GatewaySession(
                session_id="test_session",
                user_id="test_user",
                workspace_dir="/tmp"
            )
            
            # Since process_heartbeat_cycle is large, we can just patch it out to fail fast or just mock enough
            # It's easier to mock out the top level of process_heartbeat_cycle or just trigger the logic
            # Let's mock out CapacityGovernor to avoid issues
            with patch("universal_agent.services.capacity_governor.CapacityGovernor.get_instance") as mock_gov_instance:
                mock_gov = MagicMock()
                mock_gov.can_dispatch.return_value = (True, "")
                mock_gov_instance.return_value = mock_gov
                # Let it reach the signal curator block by NOT raising an exception
                with patch("universal_agent.heartbeat_service._heartbeat_guard_policy", return_value={"autonomous_enabled": True, "skip_reason": ""}):
                    try:
                        from universal_agent.heartbeat_service import HeartbeatState, HeartbeatScheduleConfig, HeartbeatDeliveryConfig, HeartbeatVisibilityConfig
                        await service._run_heartbeat(
                            session=session,
                            state=MagicMock(),
                            state_path=MagicMock(),
                            heartbeat_content="",
                            schedule=MagicMock(),
                            delivery=MagicMock(),
                            visibility=MagicMock()
                        )
                    except Exception as e:
                        import traceback
                        traceback.print_exc()
                        raise

            # Check if record_curation_run was called to see if we reached the block
            import sys
            print(f"record_curation_run call count: {mock_record.call_count}", file=sys.stderr)


            # Give the event loop a moment to execute the background task created by asyncio.create_task
            import asyncio
            await asyncio.sleep(0.1)
            
            # Check if dispatch_vp_mission was called
            mock_dispatch.assert_called_once()
            args, kwargs = mock_dispatch.call_args
            assert kwargs["mission_type"] == "curation"
            assert kwargs["vp_id"] == "vp.general.primary"
            assert kwargs["source_session_id"] == "test_session"
            assert "2 pending proactive signal cards" in kwargs["objective"]
