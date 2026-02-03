#!/usr/bin/env python
"""
Composio SDK Regression Tests - UPDATED for v0.10.6

These tests verify that our Composio integration works correctly after SDK upgrades.
Run before and after any Composio SDK version change.

Usage:
    cd /home/kjdragan/lrepos/universal_agent
    uv run python tests/test_composio_regression.py
"""

import asyncio
import os
import sys
from typing import Any, Optional
from dataclasses import dataclass

# Ensure src is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@dataclass
class TestResult:
    __test__ = False
    name: str
    passed: bool
    message: str
    data: Optional[Any] = None


def _sdk_import_result() -> TestResult:
    """Test 1: Basic SDK imports work."""
    try:
        from composio import Composio
        return TestResult("SDK Import", True, "composio.Composio imported successfully")
    except ImportError as e:
        return TestResult("SDK Import", False, f"Import failed: {e}")


def _anthropic_provider_import_result() -> TestResult:
    """Test 2: Anthropic provider import works."""
    try:
        from composio_anthropic import AnthropicProvider
        return TestResult("Anthropic Provider", True, "AnthropicProvider imported successfully")
    except ImportError as e:
        return TestResult("Anthropic Provider", False, f"Import failed: {e}")


def _claude_agent_sdk_provider_import_result() -> TestResult:
    """Test 3: NEW - Claude Agent SDK provider import works."""
    try:
        from composio_claude_agent_sdk import ClaudeAgentSDKProvider
        return TestResult("Claude Agent SDK Provider", True, "ClaudeAgentSDKProvider imported successfully")
    except ImportError as e:
        return TestResult("Claude Agent SDK Provider", False, f"Import failed: {e}")


def _client_import_result() -> TestResult:
    """Test 4: Composio client import works."""
    try:
        from composio_client import Composio as ComposioClient
        return TestResult("Composio Client", True, "composio_client.Composio imported")
    except ImportError as e:
        return TestResult("Composio Client", False, f"Import failed: {e}")


def _composio_initialization_result() -> TestResult:
    """Test 5: Composio client can be initialized."""
    api_key = os.getenv("COMPOSIO_API_KEY")
    if not api_key:
        return TestResult("Composio Init", False, "COMPOSIO_API_KEY not set in environment")
    
    try:
        from composio import Composio
        client = Composio(api_key=api_key)
        return TestResult("Composio Init", True, "Composio client initialized", {"client": type(client).__name__})
    except Exception as e:
        return TestResult("Composio Init", False, f"Initialization failed: {e}")


def _composio_init_with_file_dir_result() -> TestResult:
    """Test 6: Composio initialization with file_download_dir (our pattern)."""
    api_key = os.getenv("COMPOSIO_API_KEY")
    if not api_key:
        return TestResult("Composio Init (file dir)", False, "COMPOSIO_API_KEY not set")
    
    try:
        from composio import Composio
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            client = Composio(api_key=api_key, file_download_dir=tmpdir)
            return TestResult("Composio Init (file dir)", True, f"Initialized with file_download_dir={tmpdir}")
    except Exception as e:
        return TestResult("Composio Init (file dir)", False, f"Failed: {e}")


def _session_creation_result() -> TestResult:
    """Test 7: Session can be created (our main pattern)."""
    api_key = os.getenv("COMPOSIO_API_KEY")
    user_id = os.getenv("COMPOSIO_USER_ID", "test_user")
    
    if not api_key:
        return TestResult("Session Creation", False, "COMPOSIO_API_KEY not set")
    
    try:
        from composio import Composio
        
        client = Composio(api_key=api_key)
        session = client.create(
            user_id=user_id,
            toolkits={"disable": ["firecrawl", "exa"]}  # Our standard config
        )
        
        mcp_url = session.mcp.url if hasattr(session, 'mcp') else None
        
        return TestResult(
            "Session Creation", 
            True, 
            f"Session created for user {user_id}",
            {"mcp_url": mcp_url[:50] + "..." if mcp_url else None}
        )
    except Exception as e:
        return TestResult("Session Creation", False, f"Failed: {e}")


def _connected_accounts_list_result() -> TestResult:
    """Test 8: Can list connected accounts (our discovery pattern)."""
    api_key = os.getenv("COMPOSIO_API_KEY")
    user_id = os.getenv("COMPOSIO_USER_ID", "test_user")
    
    if not api_key:
        return TestResult("Connected Accounts", False, "COMPOSIO_API_KEY not set")
    
    try:
        from composio import Composio
        
        client = Composio(api_key=api_key)
        connections = client.connected_accounts.list(user_ids=[user_id])
        
        count = len(connections.items) if hasattr(connections, 'items') else 0
        
        return TestResult(
            "Connected Accounts", 
            True, 
            f"Found {count} connected accounts",
            {"count": count}
        )
    except Exception as e:
        return TestResult("Connected Accounts", False, f"Failed: {e}")


def _composio_init_signature_result() -> TestResult:
    """Test 9: Check Composio init parameters (provider pattern)."""
    try:
        from composio import Composio
        import inspect
        
        sig = inspect.signature(Composio.__init__)
        params = list(sig.parameters.keys())
        
        has_provider = "provider" in params
        has_kwargs = "kwargs" in params
        
        return TestResult(
            "Composio Init Signature", 
            has_provider, 
            f"Parameters: {params}",
            {"has_provider": has_provider, "has_kwargs": has_kwargs}
        )
    except Exception as e:
        return TestResult("Composio Init Signature", False, f"Failed: {e}")


def _claude_agent_sdk_provider_workflow_result() -> TestResult:
    """Test 10: NEW - Claude Agent SDK provider pattern works."""
    api_key = os.getenv("COMPOSIO_API_KEY")
    
    if not api_key:
        return TestResult("Provider Workflow", False, "COMPOSIO_API_KEY not set")
    
    try:
        from composio import Composio
        from composio_claude_agent_sdk import ClaudeAgentSDKProvider
        
        # Initialize with the new provider pattern
        provider = ClaudeAgentSDKProvider()
        client = Composio(api_key=api_key, provider=provider)
        
        return TestResult(
            "Provider Workflow", 
            True, 
            "Provider pattern initialized successfully",
            {"provider_type": type(provider).__name__}
        )
    except Exception as e:
        return TestResult("Provider Workflow", False, f"Failed: {e}")


def _session_modifier_availability_result() -> TestResult:
    """Test 11: NEW - Check if session modifiers are available."""
    try:
        from composio.core.models._modifiers import (
            schema_modifier,
            before_execute_meta,
            after_execute_meta,
            SchemaModifier,
            BeforeExecuteMeta,
            AfterExecuteMeta,
        )
        
        return TestResult(
            "Session Modifiers", 
            True, 
            "All modifier decorators and types available",
            {"available": ["schema_modifier", "before_execute_meta", "after_execute_meta"]}
        )
    except ImportError as e:
        return TestResult("Session Modifiers", False, f"Import failed: {e}")


def _mcp_with_provider_session_result() -> TestResult:
    """Test 12: Can we get MCP URL with provider-initialized client?"""
    api_key = os.getenv("COMPOSIO_API_KEY")
    user_id = os.getenv("COMPOSIO_USER_ID", "test_user")
    
    if not api_key:
        return TestResult("MCP + Provider Session", False, "COMPOSIO_API_KEY not set")
    
    try:
        from composio import Composio
        from composio_claude_agent_sdk import ClaudeAgentSDKProvider
        
        provider = ClaudeAgentSDKProvider()
        client = Composio(api_key=api_key, provider=provider)
        
        # Can we still create a session for MCP URL?
        session = client.create(user_id=user_id)
        
        has_mcp = hasattr(session, 'mcp') and hasattr(session.mcp, 'url')
        mcp_url = session.mcp.url[:50] + "..." if has_mcp else None
        
        return TestResult(
            "MCP + Provider Session", 
            has_mcp, 
            "Session has MCP URL with provider" if has_mcp else "No MCP URL available with provider",
            {"mcp_url": mcp_url}
        )
    except Exception as e:
        return TestResult("MCP + Provider Session", False, f"Failed: {e}")


def _modifier_decorator_function_result() -> TestResult:
    """Test 13: Test that modifier decorators work as functions."""
    try:
        from composio.core.models._modifiers import schema_modifier
        
        @schema_modifier
        def my_schema_modifier(tool_schema):
            """Clean up tool schema."""
            return tool_schema
        
        return TestResult(
            "Modifier Decorator", 
            True, 
            "schema_modifier decorator works as intended",
            {"func_name": my_schema_modifier.__name__ if hasattr(my_schema_modifier, '__name__') else 'wrapped'}
        )
    except Exception as e:
        return TestResult("Modifier Decorator", False, f"Failed: {e}")


def test_sdk_import() -> None:
    result = _sdk_import_result()
    assert result.passed, result.message


def test_anthropic_provider_import() -> None:
    result = _anthropic_provider_import_result()
    assert result.passed, result.message


def test_claude_agent_sdk_provider_import() -> None:
    result = _claude_agent_sdk_provider_import_result()
    assert result.passed, result.message


def test_client_import() -> None:
    result = _client_import_result()
    assert result.passed, result.message


def test_composio_initialization() -> None:
    result = _composio_initialization_result()
    assert result.passed, result.message


def test_composio_init_with_file_dir() -> None:
    result = _composio_init_with_file_dir_result()
    assert result.passed, result.message


def test_session_creation() -> None:
    result = _session_creation_result()
    assert result.passed, result.message


def test_connected_accounts_list() -> None:
    result = _connected_accounts_list_result()
    assert result.passed, result.message


def test_composio_init_signature() -> None:
    result = _composio_init_signature_result()
    assert result.passed, result.message


def test_claude_agent_sdk_provider_workflow() -> None:
    result = _claude_agent_sdk_provider_workflow_result()
    assert result.passed, result.message


def test_session_modifier_availability() -> None:
    result = _session_modifier_availability_result()
    assert result.passed, result.message


def test_mcp_with_provider_session() -> None:
    result = _mcp_with_provider_session_result()
    assert result.passed, result.message


def test_modifier_decorator_function() -> None:
    result = _modifier_decorator_function_result()
    assert result.passed, result.message


def run_all_tests() -> list[TestResult]:
    """Run all regression tests."""
    tests = [
        _sdk_import_result,
        _anthropic_provider_import_result,
        _claude_agent_sdk_provider_import_result,
        _client_import_result,
        _composio_initialization_result,
        _composio_init_with_file_dir_result,
        _session_creation_result,
        _connected_accounts_list_result,
        _composio_init_signature_result,
        _claude_agent_sdk_provider_workflow_result,
        _session_modifier_availability_result,
        _mcp_with_provider_session_result,
        _modifier_decorator_function_result,
    ]
    
    results = []
    for test_fn in tests:
        try:
            result = test_fn()
            results.append(result)
        except Exception as e:
            results.append(TestResult(test_fn.__name__, False, f"Unexpected error: {e}"))
    
    return results


def print_results(results: list[TestResult]):
    """Print test results in a formatted way."""
    print("\n" + "=" * 70)
    print("COMPOSIO SDK REGRESSION TEST RESULTS (v0.10.6)")
    print("=" * 70)
    
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    
    for i, result in enumerate(results, 1):
        status = "✅ PASS" if result.passed else "❌ FAIL"
        print(f"\n{i:2}. [{status}] {result.name}")
        print(f"    Message: {result.message}")
        if result.data:
            print(f"    Data: {result.data}")
    
    print("\n" + "-" * 70)
    print(f"SUMMARY: {passed}/{len(results)} tests passed, {failed} failed")
    print("-" * 70)
    
    return failed == 0


if __name__ == "__main__":
    # Load environment
    from dotenv import load_dotenv
    load_dotenv()
    
    results = run_all_tests()
    success = print_results(results)
    
    sys.exit(0 if success else 1)
