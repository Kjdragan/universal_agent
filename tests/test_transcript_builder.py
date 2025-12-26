
import os
import json
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
