import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

try:
    from Memory_System.manager import MemoryManager
    import chromadb
except ImportError:
    MemoryManager = None

class TestMemoryHybrid(unittest.TestCase):
    def setUp(self):
        if not MemoryManager:
            self.skipTest("Memory_System dependencies not found")
            
        self.test_dir = tempfile.mkdtemp()
        self.storage_dir = os.path.join(self.test_dir, "db_data")
        self.workspace_dir = os.path.join(self.test_dir, "workspace")
        self.memory_dir = os.path.join(self.workspace_dir, "memory")
        
        os.makedirs(self.memory_dir, exist_ok=True)
        
        # Initialize manager with isolated storage and workspace
        # We need to set the flag to enable watcher
        # Patch the function name in the manager module namespace because it's already imported
        with patch("Memory_System.manager.memory_index_enabled", return_value=True):
            self.manager = MemoryManager(storage_dir=self.storage_dir, workspace_dir=self.workspace_dir)

    def tearDown(self):
        if self.manager:
            self.manager.close()
        try:
            shutil.rmtree(self.test_dir)
        except Exception:
            pass

    def test_hybrid_search(self):
        """Test RRF fusion of Vector and FTS results."""
        # 1. Insert documents
        # "The quick brown fox" -> Vector match for "fast animal"
        self.manager.archival_memory_insert("The quick brown fox jumps.", tags="test")
        
        # "SpecialKeyWord123" -> Keyword match only (unlikely to have semantic meaning)
        self.manager.archival_memory_insert("This contains SpecialKeyWord123.", tags="test")
        
        # 2. Query hitting both
        # "fast fox" should hit vector
        # "SpecialKeyWord123" should hit FTS
        query = "fast fox SpecialKeyWord123"
        result_str = self.manager.archival_memory_search(query)
        
        # 3. Verify
        self.assertIn("quick brown fox", result_str)
        self.assertIn("SpecialKeyWord123", result_str)

    def test_watcher_sync(self):
        """Test that file system changes are auto-indexed."""
        # Check watcher started
        if not self.manager.watcher:
            self.skipTest("Watcher not initialized (check flags or install)")
            
        # 1. Create file in memory dir
        new_file = os.path.join(self.memory_dir, "idea.md")
        unique_content = "The secret code is BlueBanana42."
        
        with open(new_file, "w") as f:
            f.write(unique_content)
            
        # 2. Wait for async watcher (debounce + processing)
        # Watchdog usually fast, but give it safe buffer
        time.sleep(2)
        
        # 3. Search for it
        result_str = self.manager.archival_memory_search("BlueBanana42")
        self.assertIn("BlueBanana42", result_str)
        
        # 4. Verify tag
        # We assume tag format is file:filename
        # But search result string format might not show tags explicitly in content unless we formatted it so?
        # Manager implementation: output.append(f"\n[{i+1}] (Tags: {item.tags})")
        self.assertIn("file:idea.md", result_str)

    def test_transcript_indexing(self):
        """Verify transcript indexing works."""
        session_id = "sess_123"
        content = "User asked: What is the capital of Mars? Assistant: It is a planet, no capital."
        
        self.manager.transcript_index(session_id, content)
        
        # Search
        result_str = self.manager.archival_memory_search("capital of Mars")
        self.assertIn("User asked", result_str)
        self.assertIn(f"session:{session_id}", result_str)

if __name__ == "__main__":
    unittest.main()
