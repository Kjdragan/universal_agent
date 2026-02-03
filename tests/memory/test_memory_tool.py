import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Import the tool directly
from universal_agent.tools.memory import ua_memory_get
# Import AgentSetup for scaffolding test
from universal_agent.agent_setup import AgentSetup

class TestMemoryTool(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.workspace_dir = os.path.join(self.test_dir, "workspace")
        os.makedirs(self.workspace_dir)
        
        # Set env var for tool to find workspace
        self.env_patcher = patch.dict(os.environ, {"AGENT_WORKSPACE_DIR": self.workspace_dir})
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()
        shutil.rmtree(self.test_dir)

    def test_scaffolding(self):
        """Verify AgentSetup creates memory files when enabled."""
        # Mock dependencies to avoid full agent startup
        with patch.dict(os.environ, {"UA_DISABLE_LOCAL_MEMORY": "false"}):
            
            setup = AgentSetup(
                workspace_dir=self.workspace_dir,
                enable_memory=True,
                verbose=False
            )
            # Trigger directory setup manually since we aren't calling initialize() full chain
            setup._setup_workspace_dirs()
            
            memory_file = Path(self.workspace_dir) / "MEMORY.md"
            memory_dir = Path(self.workspace_dir) / "memory"
            
            self.assertTrue(memory_dir.exists(), "memory/ directory should exist")
            self.assertTrue(memory_dir.is_dir(), "memory/ should be a directory")
            self.assertTrue(memory_file.exists(), "MEMORY.md should exist")
            
            with open(memory_file, "r") as f:
                content = f.read()
                self.assertIn("# Agent Memory", content)

    def test_tool_valid_access(self):
        """Verify reading valid files."""
        # Setup files
        memory_file = Path(self.workspace_dir) / "MEMORY.md"
        with open(memory_file, "w") as f:
            f.write("Start\nLine 2\nLine 3\n")
            
        memory_subdir = Path(self.workspace_dir) / "memory"
        os.makedirs(memory_subdir, exist_ok=True)
        sub_file = memory_subdir / "notes.txt"
        with open(sub_file, "w") as f:
            f.write("Note content")

        # Test reading MEMORY.md
        content = ua_memory_get("MEMORY.md")
        self.assertIn("Start", content)
        self.assertIn("Line 3", content)

        # Test reading subdirectory file
        content = ua_memory_get("memory/notes.txt")
        self.assertEqual(content, "Note content")
        
    def test_tool_security_guardrails(self):
        """Verify access controls."""
        root = Path(self.workspace_dir)
        
        # 1. File outside workspace
        outside_file = Path(self.test_dir) / "secret.env"
        with open(outside_file, "w") as f:
            f.write("SECRET_KEY=123")
            
        result = ua_memory_get("../secret.env")
        self.assertIn("Access Denied", result)
        self.assertIn("outside the active workspace", result)
        
        # 2. File inside workspace but not allowed
        random_file = root / "random.txt"
        with open(random_file, "w") as f:
            f.write("should not read")
            
        result = ua_memory_get("random.txt")
        self.assertIn("Access Denied", result)
        self.assertIn("only read 'MEMORY.md' or files in the 'memory/'", result)
        
        # 3. Directory traversal attempt
        result = ua_memory_get("memory/../../secret.env")
        self.assertIn("Access Denied", result)
        
    def test_line_limits(self):
        """Verify line reading limits."""
        memory_file = Path(self.workspace_dir) / "MEMORY.md"
        with open(memory_file, "w") as f:
            f.write("\n".join([f"Line {i}" for i in range(1, 11)]))
            
        # Read lines 2-4 (3 lines)
        content = ua_memory_get("MEMORY.md", line_start=2, num_lines=3)
        self.assertNotIn("Line 1", content)
        self.assertIn("Line 2", content)
        self.assertIn("Line 3", content)
        self.assertIn("Line 4", content)
        self.assertNotIn("Line 5", content)

if __name__ == "__main__":
    unittest.main()
