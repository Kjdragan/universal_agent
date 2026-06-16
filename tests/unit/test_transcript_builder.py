
import json
import os

import pytest

from src.universal_agent.transcript_builder import generate_transcript


def test_generate_transcript(tmp_path):
    """Test generating a transcript from mock trace data."""
    
    # Mock trace data
    trace_data = {
        "session_info": {
            "user_id": "test_user",
            "url": "http://mock-url"
        },
        "trace_id": "abcdef123456",
        "start_time": "2025-12-26T10:00:00.000000",
        "end_time": "2025-12-26T10:00:10.000000",
        "total_duration_seconds": 10.5,
        "query": "Test query",
        "iterations": [
            {"iteration": 1}
        ],
        "tool_calls": [
            {
                "iteration": 1,
                "name": "TEST_TOOL",
                "id": "call_123",
                "time_offset_seconds": 1.2,
                "input_preview": {"arg": "value"}
            }
        ],
        "tool_results": [
            {
                "tool_use_id": "call_123",
                "content_preview": "Tool result content",
                "is_error": False
            }
        ]
    }
    
    # Define output path
    output_path = tmp_path / "transcript.md"
    
    # Execute
    success = generate_transcript(trace_data, str(output_path))
    
    # Verify
    assert success is True
    assert os.path.exists(output_path)
    
    with open(output_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    # Check for key content
    assert "# 🎬 Session Transcript" in content
    assert "Test query" in content
    assert "TEST_TOOL" in content
    assert "Tool result content" in content
    assert "10.5s" in content


def _single_call_trace(tool_input, content_preview, *, is_error=False):
    """Build a minimal trace with one tool call + one matching result.

    Used by the exception-narrowing regression tests below to drive the
    specific parsing/IO branches inside ``generate_transcript``.
    """
    return {
        "session_info": {"user_id": "test_user"},
        "trace_id": "trace_xyz",
        "start_time": "2025-12-26T10:00:00.000000",
        "end_time": "2025-12-26T10:00:10.000000",
        "total_duration_seconds": 10.0,
        "query": "drive the parsing fallbacks",
        "iterations": [{"iteration": 1}],
        "tool_calls": [
            {
                "iteration": 1,
                "name": "TEST_TOOL",
                "id": "call_1",
                "time_offset_seconds": 0.0,
                "input": tool_input,
            }
        ],
        "tool_results": [
            {
                "tool_use_id": "call_1",
                "content_preview": content_preview,
                "is_error": is_error,
            }
        ],
    }


def test_non_serializable_tool_input_falls_back_to_str(tmp_path):
    """json.dumps raises TypeError on a non-serializable value (a set).

    The input-rendering handler must catch it and fall back to ``str()``
    rather than crashing the whole transcript.
    """
    out = tmp_path / "t.md"
    ok = generate_transcript(_single_call_trace({"items": set()}, "ok"), str(out))
    assert ok is True
    text = out.read_text(encoding="utf-8")
    # str({"items": set()}) renders the set, proving the fallback ran
    assert "set()" in text


def test_malformed_literal_eval_uses_regex_fallback(tmp_path):
    """ast.literal_eval raises on a truncated Python-literal content_preview.

    The handler must fall back to the regex unwrap path instead of crashing.
    This also exercises the json.loads handler on the (non-JSON) fallback text.
    """
    out = tmp_path / "t.md"
    truncated = "[{'type': 'text', 'text': 'regexfallback'"  # no closing brackets
    ok = generate_transcript(_single_call_trace({}, truncated), str(out))
    assert ok is True
    text = out.read_text(encoding="utf-8")
    assert "regexfallback" in text


def test_literal_eval_unwrap_success(tmp_path):
    """A well-formed Python literal of text blocks unwraps to the inner text."""
    out = tmp_path / "t.md"
    literal = "[{'type': 'text', 'text': 'unwrapped-text'}]"
    ok = generate_transcript(_single_call_trace({}, literal), str(out))
    assert ok is True
    text = out.read_text(encoding="utf-8")
    assert "unwrapped-text" in text


def test_non_json_result_left_as_text(tmp_path):
    """json.loads raises JSONDecodeError on plain text; it must be caught
    and left as-is rather than crashing."""
    out = tmp_path / "t.md"
    ok = generate_transcript(_single_call_trace({}, "plain text not json"), str(out))
    assert ok is True
    text = out.read_text(encoding="utf-8")
    assert "plain text not json" in text


def test_valid_json_result_pretty_printed(tmp_path):
    """A valid JSON content_preview is parsed and pretty-printed."""
    out = tmp_path / "t.md"
    ok = generate_transcript(_single_call_trace({}, '{"key": "val"}'), str(out))
    assert ok is True
    text = out.read_text(encoding="utf-8")
    assert '"key": "val"' in text


def test_write_failure_returns_false(tmp_path):
    """Writing to a path whose parent does not exist raises OSError; the
    final write handler must catch it and return False (not raise)."""
    bad_path = str(tmp_path / "no_such_dir" / "sub" / "t.md")
    ok = generate_transcript(_single_call_trace({}, "anything"), bad_path)
    assert ok is False
