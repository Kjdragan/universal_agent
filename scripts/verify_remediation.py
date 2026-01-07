
import sys
import os
import sqlite3
import pytest
from unittest.mock import MagicMock

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), "../src"))

from universal_agent.guardrails.tool_schema import _match_schema, validate_tool_input
from universal_agent.durable.ledger import ToolCallLedger

def test_tool_schema_matching():
    print("Testing Tool Schema Matching...")
    
    # 1. TodoWrite should match its own schema
    schema = _match_schema("TodoWrite")
    assert schema is not None, "TodoWrite schema not found"
    assert "todos" in schema.required, "TodoWrite schema incorrect"
    print("✅ TodoWrite matches correct schema")

    # 2. Write should match its own schema
    schema = _match_schema("Write")
    assert schema is not None, "Write schema not found"
    assert "file_path" in schema.required, "Write schema incorrect"
    print("✅ Write matches correct schema")

    # 3. TodoWrite should NOT match Write
    # (Implicitly tested by #1, but good to be sure logic doesn't return Write schema)
    schema = _match_schema("TodoWrite")
    assert "file_path" not in schema.required, "TodoWrite incorrectly matched Write schema!"
    print("✅ TodoWrite did NOT fall back to Write schema")

def test_ledger_integrity():
    print("\nTesting Ledger Integrity Handling...")
    
    # Setup in-memory DB with schema
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    
    # Minimal schema for tool_calls
    conn.executescript("""
    CREATE TABLE tool_calls (
      tool_call_id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      step_id TEXT NOT NULL,
      created_at TEXT,
      updated_at TEXT,
      raw_tool_name TEXT,
      tool_name TEXT,
      tool_namespace TEXT,
      side_effect_class TEXT,
      replay_policy TEXT,
      policy_matched INTEGER,
      policy_rule_id TEXT,
      normalized_args_hash TEXT,
      idempotency_key TEXT NOT NULL UNIQUE,
      status TEXT,
      attempt INTEGER,
      request_ref TEXT,
      response_ref TEXT,
      external_correlation_id TEXT,
      error_detail TEXT
    );
    """)
    
    ledger = ToolCallLedger(conn)
    
    # 1. First Insert (Success)
    print("Attempting initial insert...")
    ledger.prepare_tool_call(
        tool_call_id="call_1",
        run_id="run_1",
        step_id="step_1",
        tool_name="test_tool",
        tool_namespace="test",
        tool_input={"a": 1},
        idempotency_nonce="nonce1" # Force specific key generation if needed, but nonce usage depends on allow_duplicate
    )
    print("✅ Initial insert succeeded")

    # 2. Duplicate Insert (Should recover, not crash)
    print("Attempting duplicate insert (Unique Constraint)...")
    # We use the same inputs which should generate the same idempotency key
    # And we act as if we are retrying (allow_duplicate=False is default)
    try:
        receipt, key = ledger.prepare_tool_call(
            tool_call_id="call_2", # Different call ID but same logical action/inputs -> same idempotency key
            run_id="run_1",
            step_id="step_1",
            tool_name="test_tool",
            tool_namespace="test",
            tool_input={"a": 1},
            idempotency_nonce="nonce1" 
        )
        assert receipt is not None, "Should return receipt for existing call"
        assert receipt.tool_call_id == "call_1", "Should return original tool call ID"
        print("✅ Duplicate insert handled gracefully (returned existing)")
    except Exception as e:
        pytest.fail(f"Duplicate insert crashed: {e}")

if __name__ == "__main__":
    try:
        test_tool_schema_matching()
        test_ledger_integrity()
        print("\n🎉 ALL TESTS PASSED")
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
