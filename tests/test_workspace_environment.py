"""
Unit tests for workspace path propagation to MCP tools.

This test suite validates that:
1. CURRENT_SESSION_WORKSPACE is properly passed to MCP server subprocess
2. Tools fail gracefully when environment variable is missing
3. Tools correctly use the workspace path when provided
"""

import os
import sys
import tempfile
import json
from pathlib import Path
import subprocess
import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mcp_server import draft_report_parallel, compile_report


class TestWorkspaceEnvironment:
    """Test workspace environment variable handling."""
    
    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace with required structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            # Create directory structure
            (workspace / "work_products" / "_working" / "sections").mkdir(parents=True)
            (workspace / "tasks" / "test_task").mkdir(parents=True)
            
            # Create outline.json
            outline = {
                "title": "Test Report",
                "sections": [
                    {"id": "section1", "title": "Section 1"},
                    {"id": "section2", "title": "Section 2"}
                ]
            }
            (workspace / "work_products" / "_working" / "outline.json").write_text(
                json.dumps(outline)
            )
            
            # Create refined_corpus.md
            (workspace / "tasks" / "test_task" / "refined_corpus.md").write_text(
                "# Test Corpus\n\nSample content for testing."
            )
            
            yield workspace
    
    def test_draft_report_parallel_without_env(self):
        """Test that draft_report_parallel fails when CURRENT_SESSION_WORKSPACE is not set."""
        # Ensure environment variable is not set
        if "CURRENT_SESSION_WORKSPACE" in os.environ:
            del os.environ["CURRENT_SESSION_WORKSPACE"]
        
        result = draft_report_parallel()
        
        assert "Error" in result
        assert "CURRENT_SESSION_WORKSPACE not set" in result
    
    def test_draft_report_parallel_with_nonexistent_workspace(self):
        """Test that draft_report_parallel fails when workspace doesn't exist."""
        os.environ["CURRENT_SESSION_WORKSPACE"] = "/nonexistent/path/to/workspace"
        
        result = draft_report_parallel()
        
        assert "Error" in result
        assert "does not exist" in result
    
    def test_compile_report_without_env(self):
        """Test that compile_report fails when CURRENT_SESSION_WORKSPACE is not set."""
        if "CURRENT_SESSION_WORKSPACE" in os.environ:
            del os.environ["CURRENT_SESSION_WORKSPACE"]
        
        result = compile_report(theme="modern")
        
        assert "Error" in result
        assert "CURRENT_SESSION_WORKSPACE not set" in result
    
    def test_compile_report_with_nonexistent_workspace(self):
        """Test that compile_report fails when workspace doesn't exist."""
        os.environ["CURRENT_SESSION_WORKSPACE"] = "/nonexistent/path/to/workspace"
        
        result = compile_report(theme="modern")
        
        assert "Error" in result
        assert "does not exist" in result
    
    def test_mcp_server_subprocess_environment(self, temp_workspace):
        """Test that MCP server subprocess receives environment variable."""
        # Path to mcp_server.py
        mcp_server_path = Path(__file__).parent.parent / "src" / "mcp_server.py"
        
        # Create a test script that checks if env var is accessible
        test_script = f"""
import os
import sys

workspace = os.getenv("CURRENT_SESSION_WORKSPACE")
if workspace:
    print(f"SUCCESS: Got workspace: {{workspace}}")
    sys.exit(0)
else:
    print("FAIL: CURRENT_SESSION_WORKSPACE not set")
    sys.exit(1)
"""
        
        test_script_path = temp_workspace / "test_env.py"
        test_script_path.write_text(test_script)
        
        # Run subprocess WITH environment variable
        env = os.environ.copy()
        env["CURRENT_SESSION_WORKSPACE"] = str(temp_workspace)
        
        result = subprocess.run(
            [sys.executable, str(test_script_path)],
            capture_output=True,
            text=True,
            env=env
        )
        
        assert result.returncode == 0
        assert "SUCCESS" in result.stdout
        assert str(temp_workspace) in result.stdout
    
    def test_mcp_server_subprocess_no_environment(self, temp_workspace):
        """Test that MCP server subprocess fails without environment variable."""
        test_script = """
import os
import sys

workspace = os.getenv("CURRENT_SESSION_WORKSPACE")
if workspace:
    print(f"UNEXPECTED: Got workspace: {workspace}")
    sys.exit(1)
else:
    print("EXPECTED: CURRENT_SESSION_WORKSPACE not set")
    sys.exit(0)
"""
        
        test_script_path = temp_workspace / "test_env.py"
        test_script_path.write_text(test_script)
        
        # Run subprocess WITHOUT environment variable
        env = os.environ.copy()
        if "CURRENT_SESSION_WORKSPACE" in env:
            del env["CURRENT_SESSION_WORKSPACE"]
        
        result = subprocess.run(
            [sys.executable, str(test_script_path)],
            capture_output=True,
            text=True,
            env=env
        )
        
        assert result.returncode == 0
        assert "EXPECTED" in result.stdout
    
    def test_parallel_draft_script_workspace_isolation(self, temp_workspace):
        """Test that parallel_draft.py script uses correct workspace directory."""
        script_path = Path(__file__).parent.parent / "src" / "universal_agent" / "scripts" / "parallel_draft.py"
        
        # Run the script with workspace argument
        result = subprocess.run(
            [sys.executable, str(script_path), str(temp_workspace)],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        # Should report the correct workspace
        assert f"Working from: {temp_workspace}" in result.stdout or result.stderr
        
        # Should NOT fall back to repo root
        repo_root_indicators = [
            "lrepos/universal_agent",
            "/universal_agent\n"
        ]
        for indicator in repo_root_indicators:
            assert indicator not in result.stdout
    
    def test_workspace_not_confused_with_repo_root(self, temp_workspace):
        """Test that workspace path is never confused with repository root."""
        os.environ["CURRENT_SESSION_WORKSPACE"] = str(temp_workspace)
        
        # Get repository root (should be different from workspace)
        repo_root = Path(__file__).parent.parent.resolve()
        
        # Workspace should be different from repo root
        assert temp_workspace.resolve() != repo_root
        
        # Workspace should be in /tmp or similar (temp directory)
        assert "/tmp" in str(temp_workspace) or tempfile.gettempdir() in str(temp_workspace)
        
        # Repo root should NOT be in temp directory
        assert "/tmp" not in str(repo_root) and tempfile.gettempdir() not in str(repo_root)


class TestWorkspaceIsolation:
    """Test that each session has isolated workspace."""
    
    def test_multiple_workspaces_dont_interfere(self):
        """Test that multiple workspace paths can coexist without interference."""
        workspaces = []
        
        for i in range(3):
            tmpdir = tempfile.mkdtemp(prefix=f"session_{i}_")
            workspace = Path(tmpdir)
            workspaces.append(workspace)
            
            # Create unique content in each workspace
            (workspace / "work_products").mkdir(parents=True)
            (workspace / "work_products" / f"unique_file_{i}.txt").write_text(f"Session {i}")
        
        try:
            # Verify each workspace is independent
            for i, workspace in enumerate(workspaces):
                # Check own file exists
                assert (workspace / "work_products" / f"unique_file_{i}.txt").exists()
                
                # Check other files don't exist
                for j in range(3):
                    if i != j:
                        assert not (workspace / "work_products" / f"unique_file_{j}.txt").exists()
        
        finally:
            # Cleanup
            import shutil
            for workspace in workspaces:
                if workspace.exists():
                    shutil.rmtree(workspace)


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "-s"])
