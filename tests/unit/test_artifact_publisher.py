import os
import shutil
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# The tool itself
from universal_agent.tools.artifact_publisher import mcp__internal__publish_artifact

@pytest.fixture
def run_workspace(tmp_path):
    """Provides a dummy run workspace with some test files."""
    work_products = tmp_path / "work_products"
    work_products.mkdir()
    
    # Create a test file
    test_file = work_products / "test_report.md"
    test_file.write_text("# Test Report\n\nSome test content.")
    
    # Create a test directory
    test_dir = work_products / "chart_data"
    test_dir.mkdir()
    (test_dir / "data.csv").write_text("a,b,c\n1,2,3")
    
    return work_products

@pytest.mark.asyncio
async def test_publish_artifact_missing_args():
    """Test that missing required arguments returns an error."""
    # missing source_path
    result = await mcp__internal__publish_artifact.handler({
        "skill_or_topic": "test",
        "description": "test"
    })
    text = result["content"][0]["text"]
    assert "error: 'source_path' is required" in text

    # missing skill_or_topic
    result = await mcp__internal__publish_artifact.handler({
        "source_path": "/fake/path",
        "description": "test"
    })
    text = result["content"][0]["text"]
    assert "error: 'skill_or_topic' is required" in text

@pytest.mark.asyncio
async def test_publish_artifact_file_success(run_workspace, tmp_path):
    """Test publishing a single file successfully."""
    source_file = run_workspace / "test_report.md"
    fake_artifacts_root = tmp_path / "persistent_artifacts"
    
    with patch("universal_agent.tools.artifact_publisher.resolve_artifacts_dir", return_value=fake_artifacts_root):
        result = await mcp__internal__publish_artifact.handler({
            "source_path": str(source_file),
            "skill_or_topic": "research",
            "description": "Test report publish"
        })
        
        # Verify success response
        import json
        payload_str = result["content"][0]["text"]
        
        assert "error" not in payload_str.lower(), f"Unexpected error in payload: {payload_str}"
        payload = json.loads(payload_str)
        assert payload["status"] == "success"
        
        published_path = Path(payload["published_path"])
        assert published_path.exists()
        assert published_path.name == "test_report.md"
        assert published_path.read_text() == "# Test Report\n\nSome test content."
        
        # Verify metadata was written
        metadata_file = published_path.parent / "metadata.txt"
        assert metadata_file.exists()
        metadata_content = metadata_file.read_text()
        assert "Topic: research" in metadata_content
        assert "Source: test_report.md" in metadata_content
        assert "Description: Test report publish" in metadata_content
        
@pytest.mark.asyncio
async def test_publish_artifact_directory_success(run_workspace, tmp_path):
    """Test publishing an entire directory successfully."""
    source_dir = run_workspace / "chart_data"
    fake_artifacts_root = tmp_path / "persistent_artifacts"
    
    with patch("universal_agent.tools.artifact_publisher.resolve_artifacts_dir", return_value=fake_artifacts_root):
        result = await mcp__internal__publish_artifact.handler({
            "source_path": str(source_dir),
            "skill_or_topic": "analysis",
            "description": "Chart data dir"
        })
        
        import json
        payload = json.loads(result["content"][0]["text"])
        assert payload["status"] == "success"
        
        published_path = Path(payload["published_path"])
        assert published_path.exists()
        assert published_path.is_dir()
        
        # Verify contents
        copied_file = published_path / "data.csv"
        assert copied_file.exists()
        assert copied_file.read_text() == "a,b,c\n1,2,3"
        
        # Verify metadata
        metadata_file = published_path.parent / "metadata.txt"
        metadata_content = metadata_file.read_text()
        assert "Topic: analysis" in metadata_content
        assert "Source Dir: chart_data" in metadata_content
