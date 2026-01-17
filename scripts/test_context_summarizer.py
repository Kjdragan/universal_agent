#!/usr/bin/env python3
"""
Test script for Context Summarizer

Tests the deterministic context summarization system before integration.
"""

import sys
import tempfile
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from universal_agent.urw.context_summarizer import (
    ContextCheckpoint,
    ContextSummarizer,
)


def test_checkpoint_creation():
    """Test basic checkpoint creation."""
    print("=" * 60)
    print("Test 1: Checkpoint Creation")
    print("=" * 60)
    
    checkpoint = ContextCheckpoint(
        checkpoint_id="test_ckpt_001",
        session_id="test_session",
        trigger="manual",
        original_request="Research the latest AI developments and create a report",
        current_objective="Gathering information from sources",
        current_task="Execute web searches",
        completed_tasks=["Define research scope", "Identify key sources"],
        pending_tasks=["Analyze findings", "Write report", "Send email"],
        overall_progress_pct=40.0,
        artifacts=[
            {"path": "research_scope.md", "type": "file", "summary": "Research scope document with 5 key questions"},
            {"path": "sources_list.md", "type": "file", "summary": "List of 12 sources to crawl"},
        ],
        subagent_results=[
            {"subagent_type": "research-specialist", "summary": "Completed initial research gathering with 45 sources"},
        ],
        learnings=[
            "Use specific date ranges in search queries",
            "Prefer .gov and .edu sources for reliability",
        ],
        failed_approaches=[
            "Direct Wikipedia scraping blocked by rate limits",
        ],
    )
    
    print(f"✅ Checkpoint created: {checkpoint.checkpoint_id}")
    print(f"   Session: {checkpoint.session_id}")
    print(f"   Progress: {checkpoint.overall_progress_pct}%")
    print(f"   Artifacts: {len(checkpoint.artifacts)}")
    print()
    
    return checkpoint


def test_checkpoint_serialization(checkpoint: ContextCheckpoint):
    """Test checkpoint serialization to dict and back."""
    print("=" * 60)
    print("Test 2: Checkpoint Serialization")
    print("=" * 60)
    
    # Serialize to dict
    data = checkpoint.to_dict()
    print(f"✅ Serialized to dict with {len(data)} keys")
    
    # Deserialize
    restored = ContextCheckpoint.from_dict(data)
    print(f"✅ Restored from dict: {restored.checkpoint_id}")
    
    # Verify key fields preserved
    assert restored.original_request == checkpoint.original_request
    assert restored.current_task == checkpoint.current_task
    assert len(restored.artifacts) == len(checkpoint.artifacts)
    print(f"✅ All key fields preserved correctly")
    print()
    
    return restored


def test_injection_prompt(checkpoint: ContextCheckpoint):
    """Test generating injection prompt from checkpoint."""
    print("=" * 60)
    print("Test 3: Injection Prompt Generation")
    print("=" * 60)
    
    prompt = checkpoint.to_injection_prompt(max_length=4000)
    
    print(f"✅ Generated injection prompt ({len(prompt)} chars)")
    print()
    print("--- INJECTION PROMPT PREVIEW ---")
    print(prompt[:1500])
    print("...")
    print("--- END PREVIEW ---")
    print()
    
    # Verify key sections present
    assert "Context Summary" in prompt
    assert "Original Request" in prompt
    assert "Current State" in prompt
    assert "Produced Artifacts" in prompt
    print(f"✅ All expected sections present")
    print()
    
    return prompt


def test_summarizer_persistence():
    """Test ContextSummarizer saves and loads checkpoints."""
    print("=" * 60)
    print("Test 4: Checkpoint Persistence")
    print("=" * 60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        summarizer = ContextSummarizer(workspace)
        
        # Create checkpoint
        checkpoint = summarizer.create_checkpoint(
            session_id="persist_test",
            trigger="test",
            original_request="Test persistence",
            current_task="Testing save/load",
            overall_progress_pct=50.0,
        )
        
        # Save
        filepath = summarizer.save_checkpoint(checkpoint)
        print(f"✅ Checkpoint saved to: {filepath}")
        
        # Load by ID
        loaded = summarizer.load_checkpoint(checkpoint.checkpoint_id)
        assert loaded is not None
        assert loaded.checkpoint_id == checkpoint.checkpoint_id
        print(f"✅ Loaded by ID: {loaded.checkpoint_id}")
        
        # Load latest
        latest = summarizer.load_checkpoint()
        assert latest is not None
        assert latest.checkpoint_id == checkpoint.checkpoint_id
        print(f"✅ Loaded latest: {latest.checkpoint_id}")
        
        # List checkpoints
        checkpoints = summarizer.list_checkpoints()
        print(f"✅ Listed {len(checkpoints)} checkpoint(s)")
        print()


def test_context_limits():
    """Test that injection prompt respects max_length."""
    print("=" * 60)
    print("Test 5: Context Length Limits")
    print("=" * 60)
    
    # Create checkpoint with lots of data
    checkpoint = ContextCheckpoint(
        checkpoint_id="large_ckpt",
        session_id="test",
        original_request="A" * 1000,  # Long request
        completed_tasks=[f"Task {i}" for i in range(50)],  # Many tasks
        artifacts=[{"path": f"file_{i}.md", "type": "file", "summary": "X" * 200} for i in range(20)],
        learnings=[f"Learning {i}: " + "Y" * 100 for i in range(20)],
    )
    
    # Test with small limit
    short_prompt = checkpoint.to_injection_prompt(max_length=500)
    print(f"✅ Short prompt: {len(short_prompt)} chars (limit 500)")
    assert len(short_prompt) <= 500 + 50  # Some tolerance for truncation message
    
    # Test with larger limit
    long_prompt = checkpoint.to_injection_prompt(max_length=4000)
    print(f"✅ Long prompt: {len(long_prompt)} chars (limit 4000)")
    
    print()


def main():
    print()
    print("🚀 Context Summarizer Test Suite")
    print("=" * 60)
    print()
    
    # Run tests
    checkpoint = test_checkpoint_creation()
    test_checkpoint_serialization(checkpoint)
    test_injection_prompt(checkpoint)
    test_summarizer_persistence()
    test_context_limits()
    
    print("=" * 60)
    print("✅ All tests passed!")
    print("=" * 60)
    print()
    print("The context summarizer is ready for integration.")
    print()
    print("Next steps:")
    print("1. Integrate with URW orchestrator for phase boundaries")
    print("2. Add PreCompact hook to agent_core.py")
    print("3. Test with actual agent run")
    print()


if __name__ == "__main__":
    main()
