#!/usr/bin/env python3
"""
Gateway Refactor Test Suite

Organized, repeatable test suite for the unified execution engine refactor.

Usage:
    uv run python scripts/test_gateway_refactor.py --test imports
    uv run python scripts/test_gateway_refactor.py --test unit
    uv run python scripts/test_gateway_refactor.py --test gateway
    uv run python scripts/test_gateway_refactor.py --test all
    uv run python scripts/test_gateway_refactor.py --test live-cli      # Requires API key
    uv run python scripts/test_gateway_refactor.py --test live-gateway  # Requires API key
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Load .env file for API keys
from dotenv import load_dotenv
load_dotenv()

# Results storage
TEST_RESULTS: dict[str, dict] = {}


def record_result(test_name: str, passed: bool, details: str = "", error: str = ""):
    """Record a test result."""
    TEST_RESULTS[test_name] = {
        "passed": passed,
        "details": details,
        "error": error,
        "timestamp": datetime.now().isoformat(),
    }
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status}: {test_name}")
    if details:
        print(f"         {details}")
    if error:
        print(f"         Error: {error}")


# =============================================================================
# TEST 1: Import Validation
# =============================================================================

def test_imports():
    """Test that all new modules can be imported correctly."""
    print("\n" + "=" * 60)
    print("TEST 1: Import Validation")
    print("=" * 60)
    
    # Test execution_engine module
    try:
        from universal_agent.execution_engine import ProcessTurnAdapter, EngineConfig, ExecutionEngineFactory
        record_result("import_execution_engine", True, "ProcessTurnAdapter, EngineConfig, ExecutionEngineFactory")
    except Exception as e:
        record_result("import_execution_engine", False, error=str(e))
    
    # Test gateway module with new features
    try:
        from universal_agent.gateway import InProcessGateway, EXECUTION_ENGINE_AVAILABLE, GatewaySession, GatewayRequest
        record_result("import_gateway", True, f"EXECUTION_ENGINE_AVAILABLE={EXECUTION_ENGINE_AVAILABLE}")
    except Exception as e:
        record_result("import_gateway", False, error=str(e))
    
    # Test workspace guard
    try:
        from universal_agent.guardrails.workspace_guard import (
            enforce_workspace_path,
            workspace_scoped_path,
            WorkspaceGuardError,
            validate_tool_paths,
            is_inside_workspace,
        )
        record_result("import_workspace_guard", True, "All guard functions imported")
    except Exception as e:
        record_result("import_workspace_guard", False, error=str(e))
    
    # Test agent_core events
    try:
        from universal_agent.agent_core import AgentEvent, EventType
        record_result("import_agent_core_events", True, f"EventType values: {[e.value for e in EventType][:5]}...")
    except Exception as e:
        record_result("import_agent_core_events", False, error=str(e))
    
    # Test main.py process_turn signature
    try:
        from universal_agent.main import process_turn
        import inspect
        sig = inspect.signature(process_turn)
        has_event_callback = "event_callback" in sig.parameters
        record_result("process_turn_signature", has_event_callback, 
                     f"event_callback parameter: {has_event_callback}")
    except Exception as e:
        record_result("process_turn_signature", False, error=str(e))


# =============================================================================
# TEST 2: Unit Tests
# =============================================================================

def test_unit():
    """Unit tests for individual components."""
    print("\n" + "=" * 60)
    print("TEST 2: Unit Tests")
    print("=" * 60)
    
    # Test workspace guard - relative path
    try:
        from universal_agent.guardrails.workspace_guard import enforce_workspace_path, WorkspaceGuardError
        workspace = Path("/tmp/test_ws")
        result = enforce_workspace_path("output.txt", workspace)
        expected = workspace / "output.txt"
        record_result("workspace_guard_relative", result == expected, 
                     f"Resolved: {result}")
    except Exception as e:
        record_result("workspace_guard_relative", False, error=str(e))
    
    # Test workspace guard - escape blocked
    try:
        from universal_agent.guardrails.workspace_guard import enforce_workspace_path, WorkspaceGuardError
        workspace = Path("/tmp/test_ws")
        try:
            enforce_workspace_path("../escape.txt", workspace)
            record_result("workspace_guard_escape_block", False, "Should have raised error")
        except WorkspaceGuardError:
            record_result("workspace_guard_escape_block", True, "Escape correctly blocked")
    except Exception as e:
        record_result("workspace_guard_escape_block", False, error=str(e))
    
    # Test workspace guard - absolute outside blocked
    try:
        from universal_agent.guardrails.workspace_guard import enforce_workspace_path, WorkspaceGuardError
        workspace = Path("/tmp/test_ws")
        try:
            enforce_workspace_path("/etc/passwd", workspace)
            record_result("workspace_guard_outside_block", False, "Should have raised error")
        except WorkspaceGuardError:
            record_result("workspace_guard_outside_block", True, "Outside path correctly blocked")
    except Exception as e:
        record_result("workspace_guard_outside_block", False, error=str(e))
    
    # Test validate_tool_paths
    try:
        from universal_agent.guardrails.workspace_guard import validate_tool_paths
        workspace = Path("/tmp/test_ws")
        tool_input = {"path": "output.txt", "other": "value"}
        result = validate_tool_paths(tool_input, workspace)
        expected_path = str(workspace / "output.txt")
        record_result("validate_tool_paths", result["path"] == expected_path,
                     f"Transformed path: {result['path']}")
    except Exception as e:
        record_result("validate_tool_paths", False, error=str(e))
    
    # Test EngineConfig defaults
    try:
        from universal_agent.execution_engine import EngineConfig
        config = EngineConfig(workspace_dir="/tmp/test")
        has_user_id = config.user_id is not None
        has_run_id = config.run_id is not None
        record_result("engine_config_defaults", has_user_id and has_run_id,
                     f"user_id={config.user_id is not None}, run_id={config.run_id is not None}")
    except Exception as e:
        record_result("engine_config_defaults", False, error=str(e))
    
    # Test InProcessGateway uses unified engine
    try:
        from universal_agent.gateway import InProcessGateway, EXECUTION_ENGINE_AVAILABLE
        gateway = InProcessGateway()
        uses_unified = not gateway._use_legacy and EXECUTION_ENGINE_AVAILABLE
        record_result("gateway_uses_unified_engine", uses_unified,
                     f"_use_legacy={gateway._use_legacy}, EXECUTION_ENGINE_AVAILABLE={EXECUTION_ENGINE_AVAILABLE}")
    except Exception as e:
        record_result("gateway_uses_unified_engine", False, error=str(e))
    
    # Test InProcessGateway legacy mode
    try:
        from universal_agent.gateway import InProcessGateway
        gateway = InProcessGateway(use_legacy_bridge=True)
        record_result("gateway_legacy_mode", gateway._use_legacy == True,
                     f"_use_legacy={gateway._use_legacy}")
    except Exception as e:
        record_result("gateway_legacy_mode", False, error=str(e))


# =============================================================================
# TEST 3: Gateway Integration (Non-Live)
# =============================================================================

async def test_gateway_integration():
    """Test gateway integration without live API calls."""
    print("\n" + "=" * 60)
    print("TEST 3: Gateway Integration (Non-Live)")
    print("=" * 60)
    
    temp_base = Path(tempfile.mkdtemp(prefix="gateway_test_"))
    
    try:
        # Test gateway creation with custom workspace
        from universal_agent.gateway import InProcessGateway
        gateway = InProcessGateway(workspace_base=temp_base)
        record_result("gateway_custom_workspace", gateway._workspace_base == temp_base,
                     f"workspace_base={gateway._workspace_base}")
        
        # Test adapter dict exists
        record_result("gateway_has_adapters", hasattr(gateway, '_adapters') and isinstance(gateway._adapters, dict),
                     "Adapter management initialized")
        
        # Test session dict exists
        record_result("gateway_has_sessions", hasattr(gateway, '_sessions') and isinstance(gateway._sessions, dict),
                     "Session management initialized")
        
    except Exception as e:
        record_result("gateway_integration", False, error=str(e))
    finally:
        shutil.rmtree(temp_base, ignore_errors=True)


# =============================================================================
# TEST 4: Live CLI Mode
# =============================================================================

async def test_live_cli():
    """Test direct CLI mode execution (requires API key)."""
    print("\n" + "=" * 60)
    print("TEST 4: Live CLI Mode (Direct process_turn)")
    print("=" * 60)
    
    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("ZAI_API_KEY"):
        record_result("cli_mode_live", False, error="No API key found - skipping")
        return
    
    temp_workspace = Path(tempfile.mkdtemp(prefix="cli_test_"))
    (temp_workspace / "work_products").mkdir(exist_ok=True)
    
    try:
        from universal_agent.main import process_turn, setup_session
        from claude_agent_sdk.client import ClaudeSDKClient
        from universal_agent.agent_core import AgentEvent, EventType
        
        # Track events via callback
        events_received: list[AgentEvent] = []
        def event_callback(event: AgentEvent):
            events_received.append(event)
        
        # Setup session (returns 6 values: options, session, user_id, workspace_dir, trace, agent)
        options, session, user_id, ws_dir, trace, agent = await setup_session(
            workspace_dir_override=str(temp_workspace),
        )
        record_result("cli_setup_session", True, f"workspace={ws_dir}")
        
        # Run process_turn
        async with ClaudeSDKClient(options) as client:
            result = await process_turn(
                client=client,
                user_input="Say 'CLI test successful' and nothing else.",
                workspace_dir=str(temp_workspace),
                force_complex=False,
                max_iterations=5,
                event_callback=event_callback,
            )
            
            # ExecutionResult has: response_text, execution_time_seconds, tool_calls, etc.
            has_response = bool(result.response_text)
            record_result("cli_process_turn_executed", has_response, 
                         f"response_len={len(result.response_text)}, events={len(events_received)}")
            
            # Check events were emitted
            event_types = [e.type for e in events_received]
            record_result("cli_events_emitted", len(events_received) > 0,
                         f"Event types: {set(event_types)}")
            
    except Exception as e:
        import traceback
        record_result("cli_mode_live", False, error=f"{e}\n{traceback.format_exc()}")
    finally:
        shutil.rmtree(temp_workspace, ignore_errors=True)


# =============================================================================
# TEST 5: Live Gateway Mode
# =============================================================================

async def test_live_gateway():
    """Test gateway mode execution (requires API key)."""
    print("\n" + "=" * 60)
    print("TEST 5: Live Gateway Mode (--use-gateway equivalent)")
    print("=" * 60)
    
    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("ZAI_API_KEY"):
        record_result("gateway_mode_live", False, error="No API key found - skipping")
        return
    
    temp_base = Path(tempfile.mkdtemp(prefix="gateway_live_test_"))
    
    try:
        from universal_agent.gateway import InProcessGateway, GatewayRequest
        from universal_agent.agent_core import EventType
        
        gateway = InProcessGateway(workspace_base=temp_base)
        
        # Create session
        session = await gateway.create_session(user_id="test_user")
        record_result("gateway_session_created", True,
                     f"session_id={session.session_id}, engine={session.metadata.get('engine')}")
        
        # Verify using unified engine
        record_result("gateway_uses_process_turn", session.metadata.get("engine") == "process_turn",
                     f"engine={session.metadata.get('engine')}")
        
        # Execute query
        request = GatewayRequest(user_input="Say 'Gateway test successful' and nothing else.")
        
        events = []
        text_content = ""
        async for event in gateway.execute(session, request):
            events.append(event)
            if event.type == EventType.TEXT:
                text_content += event.data.get("text", "")
        
        record_result("gateway_execution_complete", len(events) > 0,
                     f"events={len(events)}, text_preview={text_content[:50]}...")
        
        # Check event types
        event_types = set(e.type for e in events)
        record_result("gateway_events_received", EventType.STATUS in event_types or EventType.TEXT in event_types,
                     f"Event types: {event_types}")
        
    except Exception as e:
        import traceback
        record_result("gateway_mode_live", False, error=f"{e}\n{traceback.format_exc()}")
    finally:
        shutil.rmtree(temp_base, ignore_errors=True)


# =============================================================================
# MAIN
# =============================================================================

def print_summary():
    """Print test results summary."""
    print("\n" + "=" * 60)
    print("TEST RESULTS SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for r in TEST_RESULTS.values() if r["passed"])
    failed = sum(1 for r in TEST_RESULTS.values() if not r["passed"])
    
    print(f"\nTotal: {len(TEST_RESULTS)} | Passed: {passed} | Failed: {failed}")
    
    if failed > 0:
        print("\nFailed Tests:")
        for name, result in TEST_RESULTS.items():
            if not result["passed"]:
                print(f"  ❌ {name}")
                if result["error"]:
                    print(f"     Error: {result['error'][:200]}")
    
    # Save results to file
    results_path = Path(__file__).parent / "test_results.json"
    with open(results_path, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "summary": {"total": len(TEST_RESULTS), "passed": passed, "failed": failed},
            "results": TEST_RESULTS,
        }, f, indent=2)
    print(f"\nResults saved to: {results_path}")
    
    return failed == 0


async def main():
    parser = argparse.ArgumentParser(description="Gateway Refactor Test Suite")
    parser.add_argument("--test", choices=["imports", "unit", "gateway", "live-cli", "live-gateway", "all"],
                       default="all", help="Which test to run")
    args = parser.parse_args()
    
    print("=" * 60)
    print("GATEWAY REFACTOR TEST SUITE")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("=" * 60)
    
    if args.test in ("imports", "all"):
        test_imports()
    
    if args.test in ("unit", "all"):
        test_unit()
    
    if args.test in ("gateway", "all"):
        await test_gateway_integration()
    
    if args.test == "live-cli":
        await test_live_cli()
    
    if args.test == "live-gateway":
        await test_live_gateway()
    
    if args.test == "all":
        print("\n⚠️  Live tests skipped in 'all' mode. Run separately:")
        print("    uv run python scripts/test_gateway_refactor.py --test live-cli")
        print("    uv run python scripts/test_gateway_refactor.py --test live-gateway")
    
    success = print_summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
