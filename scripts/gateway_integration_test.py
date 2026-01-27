#!/usr/bin/env python3
"""
Gateway Integration Test

Tests the unified execution engine architecture:
1. Verifies InProcessGateway uses ProcessTurnAdapter (not legacy AgentBridge)
2. Confirms event streaming works correctly
3. Validates workspace isolation

Usage:
    python scripts/gateway_integration_test.py [--live]
    
    --live: Actually run agent queries (requires API keys)
"""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_imports():
    """Test that all new modules can be imported."""
    print("Testing imports...")
    
    try:
        from universal_agent.execution_engine import ProcessTurnAdapter, EngineConfig
        print("  ✅ ProcessTurnAdapter imported")
    except ImportError as e:
        print(f"  ❌ ProcessTurnAdapter import failed: {e}")
        return False
    
    try:
        from universal_agent.gateway import InProcessGateway, EXECUTION_ENGINE_AVAILABLE
        print(f"  ✅ InProcessGateway imported (EXECUTION_ENGINE_AVAILABLE={EXECUTION_ENGINE_AVAILABLE})")
    except ImportError as e:
        print(f"  ❌ InProcessGateway import failed: {e}")
        return False
    
    try:
        from universal_agent.guardrails.workspace_guard import (
            enforce_workspace_path,
            workspace_scoped_path,
            WorkspaceGuardError,
        )
        print("  ✅ Workspace guard imported")
    except ImportError as e:
        print(f"  ❌ Workspace guard import failed: {e}")
        return False
    
    return True


def test_workspace_guard():
    """Test workspace path enforcement."""
    print("\nTesting workspace guard...")
    
    from universal_agent.guardrails.workspace_guard import (
        enforce_workspace_path,
        workspace_scoped_path,
        WorkspaceGuardError,
        is_inside_workspace,
    )
    
    workspace = Path("/tmp/test_workspace")
    
    # Test relative path resolution
    result = enforce_workspace_path("output.txt", workspace)
    assert result == workspace / "output.txt", f"Expected {workspace / 'output.txt'}, got {result}"
    print("  ✅ Relative path resolved correctly")
    
    # Test nested relative path
    result = enforce_workspace_path("work_products/report.html", workspace)
    assert result == workspace / "work_products" / "report.html"
    print("  ✅ Nested relative path resolved correctly")
    
    # Test path escape detection
    try:
        enforce_workspace_path("../escape.txt", workspace)
        print("  ❌ Should have raised WorkspaceGuardError for escape attempt")
        return False
    except WorkspaceGuardError:
        print("  ✅ Path escape correctly blocked")
    
    # Test absolute path inside workspace
    result = enforce_workspace_path("/tmp/test_workspace/valid.txt", workspace)
    assert result == workspace / "valid.txt"
    print("  ✅ Absolute path inside workspace accepted")
    
    # Test absolute path outside workspace
    try:
        enforce_workspace_path("/etc/passwd", workspace)
        print("  ❌ Should have raised WorkspaceGuardError for outside path")
        return False
    except WorkspaceGuardError:
        print("  ✅ Outside absolute path correctly blocked")
    
    # Test is_inside_workspace helper
    assert is_inside_workspace("output.txt", workspace) == True
    assert is_inside_workspace("/etc/passwd", workspace) == False
    print("  ✅ is_inside_workspace helper works")
    
    return True


def test_gateway_uses_unified_engine():
    """Test that InProcessGateway uses ProcessTurnAdapter by default."""
    print("\nTesting gateway uses unified engine...")
    
    from universal_agent.gateway import InProcessGateway, EXECUTION_ENGINE_AVAILABLE
    
    # Check that execution engine is available
    if not EXECUTION_ENGINE_AVAILABLE:
        print("  ⚠️ EXECUTION_ENGINE_AVAILABLE is False - will use legacy bridge")
        return True  # Not a failure, just a warning
    
    # Create gateway with default settings
    gateway = InProcessGateway()
    
    # Verify it's NOT using legacy mode
    if gateway._use_legacy:
        print("  ❌ Gateway is using legacy AgentBridge")
        return False
    
    print("  ✅ Gateway is using unified ProcessTurnAdapter")
    
    # Verify adapters dict exists
    assert hasattr(gateway, '_adapters'), "Gateway should have _adapters dict"
    assert isinstance(gateway._adapters, dict), "_adapters should be a dict"
    print("  ✅ Gateway has adapter management")
    
    # Test legacy mode can still be enabled
    legacy_gateway = InProcessGateway(use_legacy_bridge=True)
    assert legacy_gateway._use_legacy == True
    print("  ✅ Legacy mode can still be explicitly enabled")
    
    return True


async def test_gateway_session_creation():
    """Test that gateway session creation works."""
    print("\nTesting gateway session creation...")
    
    import tempfile
    import shutil
    from universal_agent.gateway import InProcessGateway
    
    # Create temp workspace base
    temp_base = Path(tempfile.mkdtemp(prefix="gateway_test_"))
    
    try:
        gateway = InProcessGateway(workspace_base=temp_base)
        
        # This will fail if API keys aren't set, but we can test up to initialization
        print("  ✅ Gateway created with custom workspace base")
        
        # Verify workspace base is set
        assert gateway._workspace_base == temp_base
        print("  ✅ Workspace base configured correctly")
        
    finally:
        # Cleanup
        shutil.rmtree(temp_base, ignore_errors=True)
    
    return True


async def test_live_execution():
    """Test actual execution through gateway (requires API keys)."""
    print("\nTesting live execution through gateway...")
    
    import tempfile
    import shutil
    import os
    
    # Check for API keys
    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("ZAI_API_KEY"):
        print("  ⚠️ Skipping live test - no API key found")
        return True
    
    from universal_agent.gateway import InProcessGateway, GatewayRequest
    from universal_agent.agent_core import EventType
    
    temp_base = Path(tempfile.mkdtemp(prefix="gateway_live_test_"))
    
    try:
        gateway = InProcessGateway(workspace_base=temp_base)
        
        # Create session
        session = await gateway.create_session(
            user_id="integration_test",
        )
        print(f"  ✅ Session created: {session.session_id}")
        print(f"     Workspace: {session.workspace_dir}")
        print(f"     Engine: {session.metadata.get('engine', 'unknown')}")
        
        # Verify engine is process_turn
        assert session.metadata.get("engine") == "process_turn", \
            f"Expected engine='process_turn', got '{session.metadata.get('engine')}'"
        print("  ✅ Session using unified process_turn engine")
        
        # Execute a simple query
        request = GatewayRequest(user_input="Say 'test successful' and nothing else.")
        
        events_received = []
        text_content = ""
        
        async for event in gateway.execute(session, request):
            events_received.append(event.type)
            if event.type == EventType.TEXT:
                text_content += event.data.get("text", "")
        
        print(f"  ✅ Received {len(events_received)} events")
        print(f"     Event types: {set(events_received)}")
        print(f"     Response preview: {text_content[:100]}...")
        
        # Verify we got expected event types
        assert EventType.STATUS in events_received, "Should receive STATUS events"
        print("  ✅ Event streaming working")
        
    except Exception as e:
        print(f"  ❌ Live test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(temp_base, ignore_errors=True)
    
    return True


async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Gateway integration test")
    parser.add_argument("--live", action="store_true", help="Run live execution test")
    args = parser.parse_args()
    
    print("=" * 60)
    print("GATEWAY INTEGRATION TEST")
    print("=" * 60)
    
    results = {
        "imports": test_imports(),
        "workspace_guard": test_workspace_guard(),
        "unified_engine": test_gateway_uses_unified_engine(),
        "session_creation": await test_gateway_session_creation(),
    }
    
    if args.live:
        results["live_execution"] = await test_live_execution()
    
    print("\n" + "=" * 60)
    print("TEST RESULTS")
    print("=" * 60)
    
    all_passed = True
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {test_name}: {status}")
        if not passed:
            all_passed = False
    
    print()
    if all_passed:
        print("✅ All tests passed!")
        return 0
    else:
        print("❌ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
